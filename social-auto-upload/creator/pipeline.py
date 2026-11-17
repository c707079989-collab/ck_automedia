# -*- coding: utf-8 -*-
"""pipeline.py —— 方案落盘 + 出片任务编排。

设计取舍：
  * 任务用后台线程 + JSON 文件存进度，不引 celery/redis。
    单机、单 GPU、本来就串行出片，进程内线程足够，且重启后进度还在。
  * 逐段串行提交而不是并发：16G 显存跑一段 768x1344 已经吃满，
    并发只会互相踢显存、全部变慢甚至 OOM。
  * 每段出完立刻落盘。中途挂了重跑只需补失败的镜（rerun 接口带 shots 参数）。
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import comfy
from . import config as C

_LOCK = threading.Lock()
_JOBS: Dict[str, threading.Thread] = {}


# ---------------------------------------------------------------- 方案存取

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(prefix: str = "cp") -> str:
    return f"{prefix}_{datetime.now().strftime('%y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"


def save_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    C._ensure_dirs()
    if not plan.get("id"):
        plan["id"] = new_id()
    plan["saved_at"] = _now()
    p = C.PLAN_DIR / f"{plan['id']}.json"
    p.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def load_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    p = C.PLAN_DIR / f"{Path(plan_id).name}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_plans(limit: int = 50) -> List[Dict[str, Any]]:
    """方案列表。

    除了方案本身，还要带上「最近一次出片任务」的状态：
    之前只给 has_video，正在出片/部分完成/失败的方案在列表里完全看不出变化，
    用户会以为状态没刷新。
    """
    C._ensure_dirs()
    latest = _latest_task_by_plan()
    out: List[Dict[str, Any]] = []
    for f in sorted(C.PLAN_DIR.glob("cp_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pid = d.get("id", f.stem)
        t = latest.get(pid) or {}
        out.append({
            "id": pid, "title": d.get("title", ""), "mode": d.get("mode", ""),
            "genre": d.get("genre", ""), "format": d.get("format", ""),
            "duration_planned": d.get("duration_planned", 0),
            "shots": len(d.get("shots") or []),
            "has_video": bool(d.get("final_video")),
            "render_status": t.get("status", ""),
            "render_done": t.get("done", 0),
            "render_total": t.get("total", 0),
            "render_error": t.get("error", ""),
            "created_at": d.get("created_at", ""),
            "thumb": d.get("thumb", ""),
        })
    return out


def _latest_task_by_plan() -> Dict[str, Dict[str, Any]]:
    """每个方案最近一次任务（按文件 mtime 取最新）。"""
    C._ensure_dirs()
    best: Dict[str, Any] = {}
    for f in C.TASK_DIR.glob("ct_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            mt = f.stat().st_mtime
        except (json.JSONDecodeError, OSError):
            continue
        pid = d.get("plan_id")
        if not pid:
            continue
        if pid not in best or mt > best[pid][0]:
            best[pid] = (mt, d)
    return {k: v[1] for k, v in best.items()}


def delete_plan(plan_id: str) -> bool:
    p = C.PLAN_DIR / f"{Path(plan_id).name}.json"
    if not p.exists():
        return False
    p.unlink()
    d = C.VIDEO_DIR / Path(plan_id).name
    if d.is_dir():
        for f in d.rglob("*"):
            if f.is_file():
                f.unlink()
        for sub in sorted(d.rglob("*"), reverse=True):
            if sub.is_dir():
                sub.rmdir()
        d.rmdir()
    return True


# ---------------------------------------------------------------- 临时方案（草稿）
#
# 选题阶段（选模式 / 挑爆款 / 写一句话 / 补充要求 / 画幅）都还没生成方案，
# 但用户可能写了一半就去干别的。所以把这一屏的表单状态随时落盘成「临时方案」，
# 从「我的方案」里点一下就能还原接着写。

_DRAFT_FIELDS = ("mode", "brief", "item_id", "extra", "format", "step",
                 "source_title", "note")


def _draft_path(draft_id: str) -> Path:
    return C.DRAFT_DIR / f"{Path(draft_id).name}.json"


def save_draft(data: Dict[str, Any]) -> Dict[str, Any]:
    C._ensure_dirs()
    did = str(data.get("id") or "").strip()
    if not re.fullmatch(r"dr_\d{12}_[0-9a-f]{4}", did):
        did = new_id("dr")
    old = load_draft(did) or {}
    d: Dict[str, Any] = {"id": did, "created_at": old.get("created_at") or _now()}
    for k in _DRAFT_FIELDS:
        if k in data:
            d[k] = data[k]
    d.setdefault("mode", "original")
    d.setdefault("format", "portrait")
    d.setdefault("step", 0)
    d["updated_at"] = _now()
    _draft_path(did).write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune_drafts(keep=40)
    return d


def load_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    p = _draft_path(draft_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_drafts(limit: int = 20) -> List[Dict[str, Any]]:
    C._ensure_dirs()
    out: List[Dict[str, Any]] = []
    for f in sorted(C.DRAFT_DIR.glob("dr_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        # 空草稿（没写任何内容）不占列表
        if not (str(d.get("brief") or "").strip() or str(d.get("item_id") or "").strip()):
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def delete_draft(draft_id: str) -> bool:
    p = _draft_path(draft_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def _prune_drafts(keep: int = 40) -> None:
    files = sorted(C.DRAFT_DIR.glob("dr_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- 任务存取

def _task_path(task_id: str) -> Path:
    return C.TASK_DIR / f"{Path(task_id).name}.json"


def save_task(task: Dict[str, Any]) -> Dict[str, Any]:
    C._ensure_dirs()
    task["updated_at"] = _now()
    _task_path(task["id"]).write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    return task


def load_task(task_id: str) -> Optional[Dict[str, Any]]:
    p = _task_path(task_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_tasks(limit: int = 20) -> List[Dict[str, Any]]:
    C._ensure_dirs()
    out = []
    for f in sorted(C.TASK_DIR.glob("ct_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(_reconcile(d))
    return out


# ---------------------------------------------------------------- 出片

def _update(task: Dict[str, Any], msg: str) -> None:
    task["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
    task["log"] = task["log"][-60:]
    save_task(task)


def _safe_name(name: Any, limit: int = 0) -> str:
    """把标题变成安全的文件名片段。

    注意：只能作用在**文件名**上。之前对整条绝对路径做 re.sub，
    把 "C:\\...\\videos\\cp_x\\标题_cp_x.mp4" 变成了一个相对文件名，
    结果成片被写到进程工作目录（项目根），页面又按方案目录去找 → 404。
    """
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name or "")).strip(" ._")
    if limit:
        s = s[:limit].strip(" ._")
    return s or "video"


def _prompt_for_shot(shot: Dict[str, Any]) -> str:
    """出片提示词。

    口播类必须把这一镜「要说的那句原话」写进提示词，否则 H3 只看到
    "a man speaking to camera"，没人告诉它说什么，出来的就是口胡。
    """
    prompt = (shot.get("prompt") or shot.get("prompt_body") or "").strip()
    vo = (shot.get("voiceover") or "").strip()
    if vo and vo not in prompt:
        say = f'says in Mandarin Chinese: "{vo}"'
        prompt = f"{prompt.rstrip('. ')}. {say}" if prompt else (
            "a person faces the camera and speaks, medium close-up, soft natural light. " + say)
    if not prompt:
        prompt = ("a person speaks directly to the camera in a bright clean room, "
                  "medium shot, natural window light, calm and friendly delivery")
    return prompt


def _concat(parts: List[Path], dest: Path) -> Dict[str, Any]:
    """把分段拼成成片。

    先试 -c copy（不重编码，几秒完成）；分段参数不一致时复制会失败或产出坏文件，
    再退回 libx264+aac 重编码。清单文件放在成片同目录，避免污染工作目录。
    """
    ff = C.find_ffmpeg()
    if not ff:
        return {"ok": False, "error": "未找到 ffmpeg，已保留分段文件"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    listfile = dest.parent / f".concat_{uuid.uuid4().hex[:8]}.txt"
    with listfile.open("w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.as_posix()}'\n")

    base = [ff, "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(listfile)]
    attempts = [
        base + ["-c", "copy", "-movflags", "+faststart", str(dest)],
        base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(dest)],
    ]
    err = ""
    try:
        for cmd in attempts:
            dest.unlink(missing_ok=True)
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=900,
                                   encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                err = "拼接超时"
                continue
            if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
                return {"ok": True, "error": ""}
            err = (r.stderr or "")[-400:]
    finally:
        listfile.unlink(missing_ok=True)
    return {"ok": False, "error": err or "拼接失败"}


def _finalize(task_id: str, plan_id: str) -> None:
    """所有分段就绪后拼接成片并回写方案。正常流程和中断恢复都走这里。"""
    task = load_task(task_id) or {}
    plan = load_plan(plan_id) or {}
    shots = task.get("shots") or []
    out_dir = C.VIDEO_DIR / Path(plan_id).name
    out_dir.mkdir(parents=True, exist_ok=True)

    ready = [s for s in shots if s.get("status") == "done" and s.get("path") and Path(s["path"]).exists()]
    task["done"] = len(ready)
    if not shots or len(ready) != len(shots):
        task["status"] = "partial"
        task["error"] = f"{len(shots) - len(ready)} 个镜次没有产物，未拼接"
        save_task(task)
        return

    title = _safe_name(plan.get("title") or "video", 20)
    final = out_dir / f"{title}_{Path(plan_id).name}.mp4"
    _update(task, "全部镜次完成，开始拼接成片")
    c = _concat([Path(s["path"]) for s in ready], final)
    if c["ok"]:
        task.update({"status": "done", "final_path": str(final), "error": ""})
        plan["final_video"] = str(final)
        _update(task, f"成片已生成：{final.name}")
    else:
        task.update({"status": "partial", "error": c["error"]})
        _update(task, f"拼接失败：{c['error'][:200]}")
    plan["rendered"] = [{"idx": s["idx"], "status": s["status"], "path": s.get("path", "")} for s in shots]
    save_plan(plan)
    save_task(task)


def _live_job(task_id: str) -> bool:
    if not task_id:
        return False
    with _LOCK:
        th = _JOBS.get(task_id)
    return bool(th and th.is_alive())


def _reconcile(task: Dict[str, Any]) -> Dict[str, Any]:
    """任务状态自愈。

    后端重启 / 线程被回收后，磁盘上的任务永远停在 running，前端就一直显示
    「出片中」不刷新。这里按已落盘的分段重算：全齐就补拼接，缺镜就标成部分完成。
    """
    if not task or task.get("status") not in ("running", "finalizing"):
        return task
    if _live_job(task.get("id")):
        return task

    shots = task.get("shots") or []
    for s in shots:
        if s.get("status") == "running":
            ok = bool(s.get("path")) and Path(s["path"]).exists()
            s.update({"status": "done" if ok else "pending", "error": "" if ok else "任务中断"})
    ready = [s for s in shots if s.get("status") == "done" and s.get("path") and Path(s["path"]).exists()]
    task["done"] = len(ready)
    if shots and len(ready) == len(shots):
        task["status"] = "finalizing"
        save_task(task)
        th = threading.Thread(target=_finalize, args=(task["id"], task["plan_id"]), daemon=True)
        with _LOCK:
            _JOBS[task["id"]] = th
        th.start()
    else:
        task["status"] = "partial"
        task["error"] = f"任务中断（后端重启或进程退出），已完成 {len(ready)}/{len(shots)} 镜，可重新点出片补齐"
        _update(task, task["error"])
    return task


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """读任务并做一次状态自愈，前端轮询走这里。"""
    t = load_task(task_id)
    return _reconcile(t) if t else None


def _run_job(task_id: str, plan_id: str, only: Optional[List[int]] = None) -> None:
    task = load_task(task_id) or {}
    plan = load_plan(plan_id) or {}
    shots = plan.get("shots") or []
    size = C.resolve_size(plan.get("format", "portrait"))
    out_dir = C.VIDEO_DIR / Path(plan_id).name
    seed_base = int(time.time()) % 100000

    # 断点续跑：目录里已有的分段直接认账，只有 only 里点名的才强制重出。
    # 这样"补齐失败镜"不会把已经出好的镜次再烧一遍显卡。
    out_dir.mkdir(parents=True, exist_ok=True)
    existing: Dict[int, Path] = {}
    for f in out_dir.glob("s*_*.mp4"):
        m = re.match(r"s(\d+)_", f.name)
        if m:
            existing[int(m.group(1))] = f

    finalized = False
    try:
        for st in task["shots"]:
            idx = st["idx"]
            shot = next((s for s in shots if s["idx"] == idx), None)
            if shot is None:
                st.update({"status": "failed", "error": "方案里没有这一镜"})
                save_task(task)
                continue
            reuse = idx in existing and (only is None or idx not in only)
            if reuse:
                st.update({"status": "done", "path": str(existing[idx]), "error": ""})
                continue
            # only 模式（重跑指定镜）：本次没点名的镜保持原状，不要顺带再烧一遍显卡
            if only is not None and idx not in only:
                continue
            if st["status"] == "done" and st.get("path") and Path(st["path"]).exists():
                continue

            prompt = _prompt_for_shot(shot)

            st.update({"status": "running", "error": ""})
            _update(task, f"第 {idx} 镜开始生成（{shot['seconds']}s，{size['width']}x{size['height']}）")

            r = comfy.generate(
                prompt=prompt, width=size["width"], height=size["height"],
                seconds=shot["seconds"], seed=seed_base + idx * 7919,
                prefix=f"creator/{Path(plan_id).name}/s{idx:02d}",
                dest_dir=out_dir,
            )
            if r["ok"]:
                st.update({"status": "done", "path": r["path"], "seconds_spent": r.get("seconds", 0),
                           "prompt_id": r.get("prompt_id", "")})
                _update(task, f"第 {idx} 镜完成（{r.get('seconds', 0)}s）")
            else:
                st.update({"status": "failed", "error": r.get("error", "")})
                _update(task, f"第 {idx} 镜失败：{r.get('error', '')[:160]}")
            task["done"] = sum(1 for x in task["shots"] if x["status"] == "done")
            save_task(task)

        done = [s for s in task["shots"] if s["status"] == "done"]
        task["done"] = len(done)
        if task["shots"] and len(done) == len(task["shots"]):
            # 拼接放到 _finalize 里统一做：中断恢复走的也是同一条路
            save_task(task)
            finalized = True
            _finalize(task_id, plan_id)
            return
        task["status"] = "partial"
        task["error"] = f"{len(task['shots']) - len(done)} 个镜次失败"
    except Exception as e:  # 线程里不能抛出去，否则前端永远看到 running
        task["status"] = "failed"
        task["error"] = f"{type(e).__name__}: {e}"
        _update(task, f"任务异常：{task['error'][:200]}")
    finally:
        task["ended_at"] = _now()
        if not finalized:
            save_task(task)
            # 把每镜结果回写到方案里，下次打开页面能直接看到哪些镜已经有片子
            plan["rendered"] = [{"idx": s["idx"], "status": s["status"],
                                 "path": s.get("path", "")} for s in task["shots"]]
            if task.get("final_path"):
                plan["final_video"] = task["final_path"]
            save_plan(plan)
        with _LOCK:
            _JOBS.pop(task_id, None)


def start_render(plan_id: str, only: Optional[List[int]] = None) -> Dict[str, Any]:
    plan = load_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "方案不存在", "task": None}
    shots = plan.get("shots") or []
    if not shots:
        return {"ok": False, "error": "方案里没有分镜", "task": None}

    with _LOCK:
        running = [t for t in _JOBS.values() if t.is_alive()]
        if running:
            return {"ok": False, "error": "已有出片任务在跑，16G 显存不支持并发，请等它结束", "task": None}

    task = {
        "id": new_id("ct"),
        "plan_id": plan_id,
        "plan_title": plan.get("title", ""),
        "status": "running",
        "total": len(shots),
        "done": 0,
        "shots": [{"idx": s["idx"], "status": "pending", "path": "", "error": "",
                   "seconds": s["seconds"]} for s in shots],
        "final_path": "",
        "started_at": _now(),
        "log": [],
    }
    save_task(task)
    th = threading.Thread(target=_run_job, args=(task["id"], plan_id, only), daemon=True)
    with _LOCK:
        _JOBS[task["id"]] = th
    th.start()
    return {"ok": True, "error": "", "task": task}


# ---------------------------------------------------------------- 口播/文本导出

def build_srt(plan: Dict[str, Any]) -> str:
    """按分镜时长累计生成 SRT，字幕直接用 shots[].subtitle。"""
    def ts(sec: float) -> str:
        h = int(sec // 3600); m = int(sec % 3600 // 60); s = int(sec % 60)
        ms = int(round((sec - int(sec)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: List[str] = []
    t = 0.0
    n = 0
    for shot in plan.get("shots") or []:
        txt = (shot.get("subtitle") or "").strip()
        dur = float(shot.get("seconds") or 4)
        if txt:
            n += 1
            lines += [str(n), f"{ts(t)} --> {ts(t + dur)}", txt, ""]
        t += dur
    return "\n".join(lines)


def build_teleprompter(plan: Dict[str, Any]) -> str:
    """提词卡：口播稿按句切开，一行一句，方便读。"""
    script = (plan.get("script") or "").strip()
    if not script:
        script = " ".join((s.get("voiceover") or s.get("subtitle") or "")
                          for s in plan.get("shots") or []).strip()
    parts = [p.strip() for p in re.split(r"[。！？!?\n]+", script) if p.strip()]
    head = [f"# {plan.get('title', '')}", "",
            f"目标时长 {plan.get('duration_target', '')}s / 口播稿 {len(parts)} 句", "",
            f"**开场钩子**：{plan.get('hook', '')}", "", "---", ""]
    return "\n".join(head + [f"{i}. {p}" for i, p in enumerate(parts, 1)]) + "\n"


def build_shotlist(plan: Dict[str, Any]) -> str:
    """分镜表（markdown 表格），给人拍摄和剪辑用。"""
    rows = ["| 镜号 | 时长 | 画面 | 运镜 | 字幕 | 口播 | 音效 |",
            "|---|---|---|---|---|---|---|"]
    t = 0.0
    for s in plan.get("shots") or []:
        t += float(s.get("seconds") or 0)
        cells = [str(s["idx"]), f"{s['seconds']}s", s.get("visual", ""), s.get("camera", ""),
                 s.get("subtitle", ""), s.get("voiceover", ""), s.get("audio", "")]
        rows.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    head = [f"# {plan.get('title', '')} · 分镜表", "",
            f"- 类型：{plan.get('genre', '')}",
            f"- 人群：{plan.get('target_audience', '')}",
            f"- 取胜点：{plan.get('angle', '')}",
            f"- 时长：约 {round(t, 1)}s，共 {len(plan.get('shots') or [])} 镜",
            "", "## 人物与场景", plan.get("identity", ""), ""]
    return "\n".join(head + rows) + "\n"
