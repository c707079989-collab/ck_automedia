# -*- coding: utf-8 -*-
"""
pipeline.py —— 抓取任务编排（领域模式 / 热搜模式）

一次抓取 = 生成搜索词 -> （可选）驱动 MediaCrawler 搜索 -> 归一化 -> 下载封面 -> 落盘。
整个流程跑在后台线程，前端用 /hotCapture/task/<id> 轮询进度。

两种模式:
  domain  用户选领域（可加自定义词），关键词来自 domains.py 的词表
  hot     沿用原链路：热搜榜 -> jieba 抽核心短词

落盘:
  data/hot_capture/tasks/<id>.json     任务状态与日志
  data/hot_capture/capture_<date>.json 快照（按 platform+id 去重合并）
  data/hot_capture/latest_items.json   列表页直接消费的条目（已排序、已截断、带封面）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import capture as _capture
    import domains as _domains
    import cover_cache as _covers
    import accounts as _accounts
except Exception:  # 作为包导入时
    from . import capture as _capture
    from . import domains as _domains
    from . import cover_cache as _covers
    from . import accounts as _accounts

CST = timezone(timedelta(hours=8))
APP_ROOT = _capture.APP_ROOT
TASK_DIR = _capture.SNAPSHOT_DIR / "tasks"
LATEST_ITEMS = _capture.SNAPSHOT_DIR / "latest_items.json"

PLATFORM_CODE = {"xiaohongshu": "xhs", "douyin": "dy"}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


# ---------- 任务状态 ----------

def _task_path(task_id: str) -> Path:
    return TASK_DIR / f"{Path(task_id).name}.json"


def save_task(task: Dict[str, Any]) -> None:
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    p = _task_path(task["id"])
    p.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def load_task(task_id: str) -> Optional[Dict[str, Any]]:
    p = _task_path(task_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def recent_tasks(limit: int = 10) -> List[Dict[str, Any]]:
    if not TASK_DIR.exists():
        return []
    out = []
    for f in sorted(TASK_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:limit]:
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            out.append({k: t.get(k) for k in ("id", "status", "mode", "platform", "created_at",
                                              "finished_at", "progress", "step", "message")}
                       | {"items": (t.get("result") or {}).get("item_count", 0)})
        except Exception:
            continue
    return out


def _log(task: Dict[str, Any], msg: str, progress: Optional[int] = None) -> None:
    task.setdefault("logs", []).append({"t": _now(), "msg": msg})
    task["message"] = msg
    if progress is not None:
        task["progress"] = progress
    save_task(task)


# ---------- 关键词 ----------

def build_keywords(mode: str, domains: List[str], custom: str, limit: int,
                   platform: str, hot_top: int = 10) -> List[Dict[str, Any]]:
    """返回 [{"keyword", "quota", "from"}]。"""
    if mode == "hot":
        boards = _capture.load_hot_boards()
        kws = _capture.extract_hot_keywords(boards, (platform,), hot_top)
        picked: List[Dict[str, Any]] = []
        seen = set()
        for k in kws:
            if not _capture.is_creative_suitable(k.get("title", "")):
                continue
            term = k.get("search_term") or k.get("title")
            if not term or term in seen:
                continue
            seen.add(term)
            picked.append({"keyword": term, "quota": 0, "from": f"hot:{k.get('title', '')[:20]}"})
        if not picked:
            return []
        base = max(1, limit // len(picked))
        for i, p in enumerate(picked):
            p["quota"] = base + (1 if i < limit % len(picked) else 0)
        return picked
    return _domains.resolve_keywords(domains, custom, limit)


# ---------- MediaCrawler ----------

def _mc_python() -> str:
    p = _capture.MC_ROOT / ".venv" / "Scripts" / "python.exe"
    if not p.exists():
        p = _capture.MC_ROOT / ".venv" / "bin" / "python"
    return str(p) if p.exists() else ""


def _mc_cookie_file(platform: str) -> str:
    """复用 login_xhs.py / 手动保存的 cookie 串（name=value; ...）。"""
    code = PLATFORM_CODE.get(platform, "xhs")
    for name in (f"{code}_cookie.txt", "xhs_cookie.txt"):
        p = _capture.MC_ROOT / ".run" / name
        if p.exists() and p.stat().st_size > 20:
            return str(p)
    return ""


def resolve_auth(platform: str) -> Dict[str, Any]:
    """决定这次抓取用哪套登录态。

    抖音：只认「账号管理」页的 storage_state —— 抓取页不再单独扫码。
    小红书：优先沿用 refresh 过的 cookie 串（CDP 有头链路已跑通），
            没有时退回账号管理的 storage_state。

    返回 {"mode": "storage_state"|"cookie", "value": 路径, "account": {...}}
    失败抛 accounts.AccountError，文案直接展示给用户。
    """
    if platform == "xiaohongshu":
        cookie = _mc_cookie_file(platform)
        if cookie:
            return {"mode": "cookie", "value": cookie, "account": None}
    acc = _accounts.resolve(platform)
    return {"mode": "storage_state", "value": acc["path"], "account": acc}


def run_mediacrawler(keywords: List[str], platform: str, per_keyword: int,
                     auth: Optional[Dict[str, Any]] = None,
                     timeout: float = 900.0) -> Dict[str, Any]:
    """驱动 MediaCrawler 做关键词搜索。返回 {ok, code, output}。"""
    py = _mc_python()
    if not py or not (_capture.MC_ROOT / "main.py").exists():
        return {"ok": False, "code": -1, "output": f"未找到 MediaCrawler（{_capture.MC_ROOT}）"}
    code = PLATFORM_CODE.get(platform, "xhs")
    auth = auth or {}
    cmd = [
        py, "main.py",
        "--platform", code,
        "--type", "search",
        "--keywords", ",".join(keywords),
        "--crawler_max_notes_count", str(max(1, per_keyword)),
        "--get_media", "false",
        "--get_comment", "false",
    ]
    if auth.get("mode") == "storage_state" and auth.get("value"):
        # 导入登录态：无头即可（抖音实测可行），也不再走扫码
        cmd += ["--headless", "true", "--storage-state", str(auth["value"])]
    else:
        cmd += ["--headless", "false"]
        if auth.get("value"):
            try:
                cmd += ["--cookies", Path(auth["value"]).read_text(encoding="utf-8").strip()]
            except Exception:
                pass
    try:
        proc = subprocess.run(cmd, cwd=str(_capture.MC_ROOT), capture_output=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-8:])
        return {"ok": proc.returncode == 0, "code": proc.returncode, "output": tail}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -2, "output": f"MediaCrawler 超时（>{int(timeout)}s）"}
    except Exception as e:
        return {"ok": False, "code": -3, "output": str(e)}


# ---------- 增量读取（MediaCrawler 的 jsonl 是按天追加的） ----------

def _jsonl_baseline(platform: str) -> Dict[str, int]:
    """记录抓取前各 jsonl 的行数，抓完只读新增部分，避免把历史数据当成新结果。"""
    base: Dict[str, int] = {}
    pdir = _capture.MC_ROOT / "data" / _capture.MC_DIR.get(platform, platform)
    if not pdir.exists():
        return base
    for f in pdir.rglob("*.jsonl"):
        if "comment" in f.name.lower():
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                base[str(f)] = sum(1 for _ in fh)
        except OSError:
            base[str(f)] = 0
    return base


def _read_new_mc_records(platform: str, baseline: Dict[str, int]) -> List[Dict[str, Any]]:
    pdir = _capture.MC_ROOT / "data" / _capture.MC_DIR.get(platform, platform)
    if not pdir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(pdir.rglob("*.jsonl")):
        if "comment" in f.name.lower():
            continue
        skip = baseline.get(str(f), 0)
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i < skip:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return out


# ---------- 主流程 ----------

def _collect_items(platforms: List[str], keywords: List[str], limit: int,
                   fresh: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> List[Dict[str, Any]]:
    """从 MediaCrawler 产物里挑出本次关键词命中的记录并归一化。

    fresh 有值时只处理本次新增记录（走增量），否则回退到读全量目录。
    """
    kwset = set(keywords)
    term2hot = {}
    items: List[Dict[str, Any]] = []
    for p in platforms:
        records = (fresh or {}).get(p)
        if records is None:
            records = _capture.read_mc_jsonl(_capture.MC_ROOT / "data", p)
        for rec in records:
            kw = str(rec.get("source_keyword", "")).strip()
            if kwset and kw not in kwset:
                continue
            items.append(_capture.normalize_mc_record(rec, p, kw, term2hot.get(kw, "")))
    items.sort(key=lambda x: (x.get("likes", 0), x.get("collects", 0)), reverse=True)
    return items[:limit]


def _run(task: Dict[str, Any]) -> None:
    cfg = task["config"]
    platforms = [cfg.get("platform", "xiaohongshu")]
    limit = int(cfg.get("limit", 20))
    try:
        task["status"] = "running"
        _log(task, "解析搜索词…", 5)
        kws = build_keywords(cfg.get("mode", "domain"), cfg.get("domains", []),
                             cfg.get("custom", ""), limit, platforms[0],
                             int(cfg.get("hot_top", 10)))
        if not kws:
            task.update({"status": "error", "step": "keywords", "finished_at": _now()})
            _log(task, "没有解析出任何搜索词，请换个领域或补自定义词", 100)
            return
        task["result"]["keywords"] = kws
        _log(task, f"搜索词 {len(kws)} 个：" + "、".join(k["keyword"] for k in kws[:12]), 15)

        fresh: Optional[Dict[str, List[Dict[str, Any]]]] = None
        if cfg.get("use_mc", True):
            platform = platforms[0]
            try:
                auth = resolve_auth(platform)
            except _accounts.AccountError as e:
                task.update({"status": "error", "step": "auth", "finished_at": _now()})
                _log(task, str(e), 100)
                return
            acc = auth.get("account") or {}
            if acc:
                _log(task, f"登录态：复用账号管理里的「{acc['name']}」（{acc.get('logged_at') or '时间未知'} 登录）", 20)
            else:
                _log(task, "登录态：沿用本地已保存的 cookie", 20)

            per = max(1, max(k["quota"] for k in kws))
            baseline = _jsonl_baseline(platform)
            mode_hint = "无头模式" if auth.get("mode") == "storage_state" else "有头模式"
            _log(task, f"启动 MediaCrawler 抓取（每词 {per} 条，{mode_hint}）…", 30)
            r = run_mediacrawler([k["keyword"] for k in kws], platform, per, auth=auth)
            fresh = {platform: _read_new_mc_records(platform, baseline)}
            got = len(fresh[platform])
            if not r["ok"]:
                _log(task, f"MediaCrawler 返回异常（code={r['code']}）：{r['output'][-300:]}", 60)
            else:
                _log(task, f"MediaCrawler 抓取完成，新增 {got} 条原始记录", 60)
            if not got:
                msg = ("没有抓到任何内容。若提示登录态失效，请到「账号管理」页重新登录"
                       "；否则可能是关键词过于冷门或被平台风控，换个词再试。")
                task.update({"status": "error", "step": "empty", "finished_at": _now()})
                _log(task, msg, 100)
                return
        else:
            _log(task, "跳过抓取，直接读取本地已有数据", 55)

        _log(task, "归一化记录…", 65)
        items = _collect_items(platforms, [k["keyword"] for k in kws], limit, fresh=fresh)

        _log(task, f"下载封面 {len(items)} 张…", 75)
        stat = _covers.attach_covers(items)
        for it in items:
            it["cover_url"] = _covers.public_url(it)

        snap = {
            "schema_version": 1,
            "captured_at": _now(),
            "mode": cfg.get("mode", "domain"),
            "domains": cfg.get("domains", []),
            "keywords": kws,
            "items": items,
            "stats": {"keyword_count": len(kws), "item_count": len(items),
                      "cover_ok": stat["ok"], "cover_fail": stat["fail"],
                      "platforms": platforms},
        }
        # 注意：save_snapshot 会按 platform+id 与当天快照合并，从而改写 snap["items"]。
        # 列表页要的是"本次抓取的 N 条"，所以先留一份引用，再落快照。
        latest_snap = {**snap, "items": items}
        path = _capture.save_snapshot(snap, _capture.SNAPSHOT_DIR)
        _write_latest(latest_snap, path.name)

        task["result"].update({
            "item_count": len(items),
            "cover_ok": stat["ok"],
            "cover_fail": stat["fail"],
            "snapshot": path.name,
        })
        task.update({"status": "done", "step": "done", "finished_at": _now()})
        _log(task, f"完成：{len(items)} 条内容，封面成功 {stat['ok']} 张", 100)
    except Exception as e:
        task.update({"status": "error", "step": "exception", "finished_at": _now()})
        _log(task, f"任务异常：{e}", 100)


def _write_latest(snap: Dict[str, Any], snapshot_file: str) -> None:
    LATEST_ITEMS.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": snap.get("captured_at"),
        "snapshot": snapshot_file,
        "mode": snap.get("mode"),
        "domains": snap.get("domains", []),
        "keywords": snap.get("keywords", []),
        "stats": snap.get("stats", {}),
        "items": snap.get("items", []),
    }
    with _LOCK:
        LATEST_ITEMS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def start_capture(config: Dict[str, Any]) -> Dict[str, Any]:
    """建任务并起后台线程，立即返回任务对象。"""
    task = {
        "id": datetime.now(CST).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6],
        "status": "pending",
        "mode": config.get("mode", "domain"),
        "platform": config.get("platform", "xiaohongshu"),
        "created_at": _now(),
        "finished_at": "",
        "progress": 0,
        "step": "created",
        "message": "任务已创建",
        "config": config,
        "logs": [],
        "result": {},
    }
    save_task(task)
    th = threading.Thread(target=_run, args=(task,), daemon=True)
    th.start()
    return task


def load_latest() -> Optional[Dict[str, Any]]:
    if not LATEST_ITEMS.exists():
        return None
    try:
        return json.loads(LATEST_ITEMS.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_items(snapshot: Optional[str] = None, platform: str = "",
               keyword: str = "", q: str = "", sort: str = "likes",
               limit: int = 200) -> Dict[str, Any]:
    """列表页数据源：默认读 latest_items，也可指定快照文件。"""
    if snapshot:
        f = _capture.SNAPSHOT_DIR / Path(snapshot).name
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    else:
        data = load_latest()
    if not data:
        return {"items": [], "total": 0, "captured_at": "", "snapshot": ""}
    items = data.get("items", [])
    if platform:
        items = [i for i in items if i.get("platform") == platform]
    if keyword:
        items = [i for i in items if i.get("keyword") == keyword]
    if q:
        ql = q.lower()
        items = [i for i in items
                 if ql in str(i.get("title", "")).lower() or ql in str(i.get("desc", "")).lower()]
    key = {"likes": lambda x: x.get("likes", 0),
           "collects": lambda x: x.get("collects", 0),
           "comments": lambda x: x.get("comments", 0),
           "time": lambda x: str(x.get("captured_at", ""))}.get(sort, lambda x: x.get("likes", 0))
    items = sorted(items, key=key, reverse=True)[:max(1, limit)]
    for i in items:
        i.setdefault("cover_url", _covers.public_url(i))
    return {
        "items": items,
        "total": len(items),
        "captured_at": data.get("captured_at", ""),
        "snapshot": data.get("snapshot", ""),
        "stats": data.get("stats", {}),
        "keywords": data.get("keywords", []),
    }
