# -*- coding: utf-8 -*-
"""config.py —— 创作工作台的路径与 ComfyUI 出片参数。

所有默认值来自本机实测，但一律允许 data/creator/config.json 覆盖
（换机器 / 换模型 / 换端口时不用改代码）。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "creator"
PLAN_DIR = DATA_DIR / "plans"
TASK_DIR = DATA_DIR / "tasks"
VIDEO_DIR = DATA_DIR / "videos"
# 临时方案（草稿）：选题还没生成成方案时的表单状态，随时可还原继续
DRAFT_DIR = DATA_DIR / "drafts"
CONFIG_FILE = DATA_DIR / "config.json"

# ---- ComfyUI 默认值（F:/ComfyUI130 实测：MiniMax H3 全套权重在此）----
DEFAULTS: Dict[str, Any] = {
    # 端口会变（本机现在是 5008，早先是 7878），所以 comfy_url 可配置，
    # 并且 comfy_ports 里的候选端口会自动探测、命中后自动落盘跟随。
    "comfy_url": "http://127.0.0.1:5008",
    "comfy_ports": [5008, 7878, 8188],
    # 产物在 ComfyUI 的 output 目录下，需要复制回项目
    "comfy_output": "F:/ComfyUI130/ComfyUI/output",
    # 模型名必须与 /object_info 里列出的完全一致
    "unet": "minimax_h3_hybrid_fl2va_ref2va_b25-49-int8.safetensors",
    "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "vae_video": "minimax_h3_video_vae_fp16.safetensors",
    "vae_audio": "minimax_h3_audio_vae_fp32.safetensors",
    "lora_turbo": "minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors",
    "steps": 8,                 # turbo LoRA 8 步；4 步会崩（实测）
    "sampler": "res_multistep",
    "scheduler": "simple",
    "max_shot_seconds": 5.0,    # 16G 显存实测安全上限（124 帧）
    "fps": 24,
    "timeout": 1800,            # 单段轮询上限（秒）
    "agent_model": "",          # 空 = 用 hot_capture.agent_cli 的默认本地模型
    "agent_timeout": 420,
}

# 画幅：宽高必须被 32 整除，否则模型直接报错
FORMATS = {
    "portrait": {"label": "竖屏 9:16（抖音/快手）", "width": 768, "height": 1344},
    "landscape": {"label": "横屏 16:9（B站/视频号）", "width": 1344, "height": 768},
    "square": {"label": "方形 1:1", "width": 1024, "height": 1024},
}


def _ensure_dirs() -> None:
    for d in (DATA_DIR, PLAN_DIR, TASK_DIR, VIDEO_DIR, DRAFT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> Dict[str, Any]:
    _ensure_dirs()
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg.update({k: v for k, v in saved.items() if v not in (None, "")})
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    for k, v in (patch or {}).items():
        if k in DEFAULTS and v not in (None, ""):
            cfg[k] = v
    _ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


_URL_LOCK = threading.Lock()
_URL_CACHE: Dict[str, Any] = {"url": "", "ts": 0.0}


def _alive(url: str, timeout: float = 2.0) -> bool:
    return bool(_get_json(url.rstrip("/") + "/system_stats", timeout=timeout))


def resolve_comfy_url(cfg: Optional[Dict[str, Any]] = None, force: bool = False) -> str:
    """拿到一个真的能连上的 ComfyUI 地址。

    配置里的 comfy_url 优先；连不上就按 comfy_ports 逐个试探，命中后写回配置，
    这样 ComfyUI 换端口（7878 -> 5008）时不用改代码也不用改文件，自检一次就自动跟上。
    结果缓存 30 秒，避免每次轮询都去探测。
    """
    cfg = cfg or load_config()
    conf = str(cfg.get("comfy_url") or "").rstrip("/")
    now = time.time()
    with _URL_LOCK:
        cached = _URL_CACHE.get("url")
        if not force and cached and cached == conf and now - float(_URL_CACHE.get("ts") or 0) < 30:
            return cached

    candidates = [conf]
    for p in cfg.get("comfy_ports") or []:
        try:
            candidates.append(f"http://127.0.0.1:{int(p)}")
        except (TypeError, ValueError):
            continue

    chosen, seen = "", set()
    for u in candidates:
        u = (u or "").rstrip("/")
        if not u or u in seen:
            continue
        seen.add(u)
        if _alive(u):
            chosen = u
            break

    if chosen and chosen != conf:
        try:
            save_config({"comfy_url": chosen})
        except OSError:
            pass
    result = chosen or conf
    with _URL_LOCK:
        _URL_CACHE.update({"url": result, "ts": now})
    return result


def frames_for(seconds: float) -> int:
    """秒 -> 合法帧数。H3 的 length 必须落在 17k+5 网格上（24fps）。"""
    n = max(5, round(float(seconds) * 24))
    return n + (5 - (n % 17)) % 17


def resolve_size(fmt: str) -> Dict[str, int]:
    return FORMATS.get(fmt or "portrait", FORMATS["portrait"])


def find_ffmpeg() -> str:
    """拼接成片要用 ffmpeg。找不到就返回空串，pipeline 会退化为「只给分段」。"""
    for c in (
        os.environ.get("FFMPEG_BIN", ""),
        r"E:/software/ffmpeg/ffmpeg.exe",
        r"C:/ffmpeg/bin/ffmpeg.exe",
        shutil.which("ffmpeg") or "",
    ):
        if c and Path(c).exists():
            return str(c)
    return ""


def _get_json(url: str, timeout: float = 6.0) -> Optional[Any]:
    """查 ComfyUI 用。必须绕开系统代理，否则会走 127.0.0.1:59432 报 upstream failed。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def probe(deep: bool = False) -> Dict[str, Any]:
    """环境自检：ComfyUI 在不在、要用的权重和节点齐不齐。

    deep=False 只查 /system_stats，快；deep=True 再拉一次 /object_info 校验模型名。
    """
    cfg = load_config()
    conf = str(cfg.get("comfy_url") or "").rstrip("/")
    url = resolve_comfy_url(cfg)
    info: Dict[str, Any] = {
        "comfy_url": url,
        "comfy_url_configured": conf,
        "comfy_ports": cfg.get("comfy_ports") or [],
        "url_fallback": bool(url and url != conf),
        "online": False,
        "models_ok": None,
        "missing": [],
        "ffmpeg": find_ffmpeg(),
        "ready": False,
    }
    stats = _get_json(url + "/system_stats", timeout=4)
    if not stats:
        info["hint"] = (f"ComfyUI 未响应（试过 {', '.join([conf] + [f'127.0.0.1:{p}' for p in cfg.get('comfy_ports') or []])}），"
                        "请先启动出片服务，或在页面上改端口")
        return info
    info["online"] = True
    try:
        dev = (stats.get("devices") or [{}])[0]
        info["device"] = dev.get("name", "")
        info["vram_gb"] = round(float(dev.get("vram_total", 0)) / 1024 ** 3, 1)
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    info["version"] = (stats.get("system") or {}).get("comfyui_version", "")
    q = _get_json(url + "/queue", timeout=4) or {}
    info["queue_running"] = len(q.get("queue_running") or [])
    info["queue_pending"] = len(q.get("queue_pending") or [])

    if deep:
        oi = _get_json(url + "/object_info", timeout=90) or {}
        need_nodes = ["MiniMaxH3ImageToVideo", "MiniMaxH3ReferenceToVideo", "UNETLoader",
                      "CLIPLoader", "VAELoader", "SaveVideo", "CreateVideo", "VAEDecodeAudio"]
        info["missing"] += [n for n in need_nodes if n not in oi]
        for node, field, key in (
            ("UNETLoader", "unet_name", "unet"),
            ("CLIPLoader", "clip_name", "clip"),
            ("VAELoader", "vae_name", "vae_video"),
            ("VAELoader", "vae_name", "vae_audio"),
        ):
            spec = (((oi.get(node) or {}).get("input") or {}).get("required") or {}).get(field)
            opts = spec[0] if isinstance(spec, list) and spec and isinstance(spec[0], list) else []
            if opts and cfg[key] not in opts:
                info["missing"].append(f"{key}={cfg[key]}")
        info["models_ok"] = not info["missing"]
    info["ready"] = bool(info["online"] and (info["models_ok"] is not False))
    return info
