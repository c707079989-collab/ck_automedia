# -*- coding: utf-8 -*-
"""comfy.py —— MiniMax H3 出片客户端。

拓扑固定（不手搭节点链），只替换参数 —— 见三条总原则第 1 条：
    UNETLoader(hybrid fl2va+ref2va)
      -> LoraLoaderModelOnly(turbo, 8 步)
      -> BasicScheduler / BasicGuider
    CLIPLoader(qwen3vl_32b_minimax_h3)
    VAELoader(video fp16) + VAELoader(audio fp32)
    MiniMaxH3ImageToVideo(可选 first_frame) -> SamplerCustomAdvanced
      -> VAEDecode + VAEDecodeAudio -> CreateVideo -> SaveVideo

踩过的坑（都在本机复现过）：
  * 宽高必须被 32 整除，否则提交直接报错；
  * length 必须落在 17k+5 网格（24fps），否则时长对不上；
  * 轮询 /queue 必须 GET（POST 是删除语义，返回空体导致 JSON 解析失败）；
  * 系统代理会让本地 127.0.0.1 请求走代理 -> 一律用空 ProxyHandler。
"""
from __future__ import annotations

import json
import shutil
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import config as C


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _post(url: str, body: Dict[str, Any], timeout: float = 60.0) -> Dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with _opener().open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _get(url: str, timeout: float = 20.0) -> Any:
    with _opener().open(url, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else None


# ---------------------------------------------------------------- 工作流

def build_workflow(
    prompt: str,
    width: int,
    height: int,
    seconds: float,
    seed: int,
    prefix: str,
    first_frame: Optional[str] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """生成一份 API 格式工作流。first_frame 为 ComfyUI input 目录里的文件名。"""
    cfg = cfg or C.load_config()
    length = C.frames_for(seconds)

    wf: Dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {
            "unet_name": cfg["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": cfg["clip"], "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae_video"]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae_audio"]}},
        "6": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "clip": ["2", 0], "vae": ["3", 0], "prompt": prompt,
            "width": int(width), "height": int(height), "length": int(length)}},
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": cfg["sampler"]}},
        "9": {"class_type": "BasicScheduler", "inputs": {
            "model": ["5", 0], "scheduler": cfg["scheduler"],
            "steps": int(cfg["steps"]), "denoise": 1.0}},
        "10": {"class_type": "BasicGuider", "inputs": {"model": ["5", 0], "conditioning": ["6", 0]}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["7", 0], "guider": ["10", 0], "sampler": ["8", 0],
            "sigmas": ["9", 0], "latent_image": ["6", 1]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["11", 0], "vae": ["4", 0]}},
        "14": {"class_type": "CreateVideo", "inputs": {
            "images": ["12", 0], "audio": ["13", 0], "fps": float(cfg["fps"])}},
        "15": {"class_type": "SaveVideo", "inputs": {
            "video": ["14", 0], "filename_prefix": prefix, "format": "auto", "codec": "auto"}},
    }

    # 有 turbo LoRA 才挂；没有就直接吃 UNET 输出
    if cfg.get("lora_turbo"):
        wf["5"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["1", 0], "lora_name": cfg["lora_turbo"], "strength_model": 1.0}}
    else:
        for nid in ("9", "10"):
            wf[nid]["inputs"]["model"] = ["1", 0]

    if first_frame:
        # 参考图节点用高号段，避免与 MiniMax 节点的编号撞号（撞号 ComfyUI 会返 502）
        wf["41"] = {"class_type": "LoadImage", "inputs": {"image": first_frame, "upload": "image"}}
        wf["6"]["inputs"]["first_frame"] = ["41", 0]

    return wf


# ---------------------------------------------------------------- 提交与等待

def submit(wf: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> str:
    cfg = cfg or C.load_config()
    url = C.resolve_comfy_url(cfg)
    res = _post(url + "/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())}, timeout=60)
    if res.get("node_errors"):
        raise RuntimeError(f"工作流节点报错: {json.dumps(res['node_errors'], ensure_ascii=False)[:500]}")
    pid = res.get("prompt_id")
    if not pid:
        raise RuntimeError(f"提交失败: {json.dumps(res, ensure_ascii=False)[:300]}")
    return str(pid)


def _queue_state(pid: str, url: str) -> str:
    """返回 queued / running / unknown。"""
    try:
        q = _get(url + "/queue", timeout=10) or {}
    except Exception:
        return "unknown"
    running = [x[1] for x in (q.get("queue_running") or []) if isinstance(x, (list, tuple)) and len(x) > 1]
    pending = [x[1] for x in (q.get("queue_pending") or []) if isinstance(x, (list, tuple)) and len(x) > 1]
    if pid in running:
        return "running"
    if pid in pending:
        return "queued"
    return "unknown"


def wait(pid: str, cfg: Optional[Dict[str, Any]] = None,
         on_tick: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
    """轮询到出结果。返回 {ok, outputs, error, seconds}。"""
    cfg = cfg or C.load_config()
    url = C.resolve_comfy_url(cfg)
    limit = float(cfg.get("timeout") or 1800)
    t0 = time.time()
    last = ""
    while True:
        el = int(time.time() - t0)
        if el > limit:
            return {"ok": False, "outputs": {}, "error": f"超时（>{int(limit)}s）", "seconds": el}
        state = _queue_state(pid, url)
        if state != last and on_tick:
            on_tick(el, state)
            last = state
        try:
            hist = _get(f"{url}/history/{pid}", timeout=20)
        except Exception:
            hist = None
        if isinstance(hist, dict) and pid in hist:
            entry = hist[pid] or {}
            status = entry.get("status") or {}
            outs = entry.get("outputs") or {}
            if status.get("status_str") == "success" or outs:
                return {"ok": True, "outputs": outs, "error": "", "seconds": el}
            msgs = status.get("messages") or []
            err = "生成失败"
            for m in msgs:
                if isinstance(m, (list, tuple)) and len(m) > 1 and m[0] in ("execution_error", "execution_interrupted"):
                    err = json.dumps(m[1], ensure_ascii=False)[:400]
            return {"ok": False, "outputs": outs, "error": err, "seconds": el}
        time.sleep(5)


def _pick_video(outputs: Dict[str, Any]) -> Dict[str, str]:
    """从 history 的 outputs 里挑出视频产物（SaveVideo 节点返回 kind=video）。"""
    for node_out in (outputs or {}).values():
        for key in ("images", "gifs", "videos"):
            for f in (node_out or {}).get(key) or []:
                name = str(f.get("filename") or "")
                if name.lower().endswith((".mp4", ".webm", ".mov")):
                    return {"filename": name, "subfolder": str(f.get("subfolder") or ""),
                            "type": str(f.get("type") or "output")}
    return {}


def fetch_outputs(outputs: Dict[str, Any], dest_dir: Path,
                  cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把 ComfyUI output 里的文件拷回项目目录，并顺手拿到元数据。"""
    cfg = cfg or C.load_config()
    pick = _pick_video(outputs)
    if not pick:
        return {"ok": False, "error": "没有找到视频产物", "path": ""}
    src = Path(str(cfg["comfy_output"])) / pick["subfolder"] / pick["filename"]
    if not src.exists():
        return {"ok": False, "error": f"产物不存在: {src}", "path": ""}
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / pick["filename"]
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return {"ok": True, "path": str(dest), "error": "", "comfy_path": str(src)}


def generate(prompt: str, width: int, height: int, seconds: float, seed: int,
             prefix: str, dest_dir: Path, first_frame: Optional[str] = None,
             on_tick: Optional[Callable[[int, str], None]] = None) -> Dict[str, Any]:
    """一步到位：建工作流 -> 提交 -> 等 -> 取回。"""
    cfg = C.load_config()
    try:
        wf = build_workflow(prompt, width, height, seconds, seed, prefix, first_frame, cfg)
        pid = submit(wf, cfg)
    except Exception as e:
        return {"ok": False, "error": f"提交失败: {e}", "path": "", "prompt_id": ""}
    r = wait(pid, cfg, on_tick=on_tick)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "path": "", "prompt_id": pid, "seconds": r["seconds"]}
    got = fetch_outputs(r["outputs"], dest_dir, cfg)
    return {"ok": got["ok"], "error": got["error"], "path": got["path"],
            "prompt_id": pid, "seconds": r["seconds"]}
