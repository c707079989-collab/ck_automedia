# -*- coding: utf-8 -*-
"""
cover_cache.py —— 封面图本地缓存

为什么必须本地化:
  小红书封面走 sns-webpic-qc.xhscdn.com，URL 里带签名时间戳，实测抓取后 8 小时即返回
  403（加 Referer、换 https 均无效）。前端直连必然裂图，所以抓取一结束就立刻把图拉下来。

文件名: <sha1(platform:id)[:16]>.<ext>，稳定可复算，重复抓取不会堆垃圾。
"""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    import capture as _capture
except Exception:  # 作为包导入时
    from . import capture as _capture

COVER_DIR = _capture.SNAPSHOT_DIR / "covers"

_HEADERS = {
    # 小红书 CDN 对 UA/Referer 有要求；抖音封面基本裸链可下
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Referer": "https://www.xiaohongshu.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def _first_url(cover: Any) -> str:
    if isinstance(cover, (list, tuple)):
        for c in cover:
            if isinstance(c, str) and c.strip():
                return c.strip()
            if isinstance(c, dict):
                u = c.get("url_default") or c.get("url") or c.get("url_pre")
                if u:
                    return str(u).strip()
        return ""
    if isinstance(cover, dict):
        return str(cover.get("url_default") or cover.get("url") or "").strip()
    return str(cover or "").strip()


def cover_key(platform: str, item_id: str, url: str = "") -> str:
    raw = f"{platform}:{item_id or url}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def local_path(name: str) -> Optional[Path]:
    """由文件名取本地绝对路径（防目录穿越）。"""
    if not name:
        return None
    p = COVER_DIR / Path(name).name
    return p if p.exists() else None


def _ext_from(content_type: str, url: str) -> str:
    ct = (content_type or "").lower()
    if "webp" in ct:
        return ".webp"
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "avif" in ct:
        return ".avif"
    suffix = os.path.splitext(url.split("?")[0])[1].lower()
    return suffix if suffix in _ALLOWED_EXT else ".jpg"


def download_one(platform: str, item_id: str, url: str, timeout: float = 12.0) -> str:
    """下单个封面，返回本地文件名（失败返回空串）。已存在则直接复用。"""
    url = _first_url(url)
    if not url:
        return ""
    key = cover_key(platform, item_id, url)
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    for ext in (".jpg", ".webp", ".png", ".avif", ".gif", ".jpeg"):
        p = COVER_DIR / f"{key}{ext}"
        if p.exists() and p.stat().st_size > 512:
            return p.name
    try:
        import requests
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        if r.status_code != 200 or len(r.content) < 512:
            return ""
        ext = _ext_from(r.headers.get("Content-Type", ""), url)
        p = COVER_DIR / f"{key}{ext}"
        p.write_bytes(r.content)
        return p.name
    except Exception:
        return ""


def attach_covers(items: Iterable[Dict[str, Any]], workers: int = 12) -> Dict[str, int]:
    """给一批条目补 cover_file 字段（并发下载）。

    就地修改 items，返回 {"ok": n, "fail": n}。
    失败不抛异常：封面缺失只是列表少张图，不应阻断采集流程。
    """
    items = list(items)
    tasks = []
    for it in items:
        url = _first_url(it.get("cover"))
        if url:
            tasks.append((it, url))
    ok = fail = 0
    if not tasks:
        return {"ok": 0, "fail": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {
            ex.submit(download_one, it.get("platform", ""), it.get("id", ""), url): it
            for it, url in tasks
        }
        for fut in as_completed(futs):
            it = futs[fut]
            try:
                name = fut.result()
            except Exception:
                name = ""
            if name:
                it["cover_file"] = name
                ok += 1
            else:
                it["cover_file"] = ""
                fail += 1
    return {"ok": ok, "fail": fail}


def public_url(item: Dict[str, Any]) -> str:
    """给前端用的封面地址：优先本地缓存，其次原始 URL（多半会过期）。"""
    if item.get("cover_file"):
        return f"/hotCapture/cover/{item['cover_file']}"
    return _first_url(item.get("cover"))


def list_cached() -> List[str]:
    if not COVER_DIR.exists():
        return []
    return sorted(p.name for p in COVER_DIR.iterdir() if p.is_file())
