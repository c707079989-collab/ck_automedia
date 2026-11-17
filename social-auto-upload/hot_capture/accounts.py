# -*- coding: utf-8 -*-
"""复用「账号管理」页面的登录态。

账号管理页（sau 的登录流程）把登录结果落成 Playwright storage_state 文件，
存在 ``cookiesFile/<uuid>.json``，并在 ``db/database.db`` 的 ``user_info`` 表里
登记平台类型与文件名。爆款抓取不再单独扫码，而是直接拿这份 storage_state
交给 MediaCrawler 起 context，实现「账号管理登录一次，抓取直接复用」。

对外只暴露两个函数：
- ``list_accounts(platform)``：列出该平台已登录账号（脱敏，不返回 cookie 内容）
- ``resolve(platform)``：解析出供 MediaCrawler 使用的 storage_state 路径
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR = Path(__file__).resolve().parent.parent
DB_PATH = APP_DIR / "db" / "database.db"
COOKIE_DIR = APP_DIR / "cookiesFile"

# sau 账号表的 type 取值
PLATFORM_TYPE = {
    "xiaohongshu": 1,
    "tencent": 2,
    "douyin": 3,
    "kuaishou": 4,
}
PLATFORM_CN = {"xiaohongshu": "小红书", "douyin": "抖音", "tencent": "视频号", "kuaishou": "快手"}

# 判定登录态是否「看起来可用」的必需 cookie：
# 小红书靠 web_session；抖音主站靠 sessionid/sid_guard 会话族。
REQUIRED_COOKIES = {
    "xiaohongshu": ("web_session",),
    "douyin": ("sessionid", "sessionid_ss", "sid_guard"),
}


class AccountError(RuntimeError):
    """账号登录态缺失或不可用。message 直接给用户看。"""


def _rows(platform: str) -> List[Dict[str, Any]]:
    ptype = PLATFORM_TYPE.get(platform)
    if ptype is None:
        return []
    if not DB_PATH.exists():
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT id, type, filePath, userName, status FROM user_info WHERE type = ? ORDER BY id DESC",
                (ptype,),
            )
            return [dict(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return []


def _inspect(path: Path, platform: str) -> Dict[str, Any]:
    """检查 storage_state 文件，返回 {ok, reason, cookie_count, logged_at}。"""
    info: Dict[str, Any] = {"ok": False, "reason": "", "cookie_count": 0, "logged_at": ""}
    if not path.exists():
        info["reason"] = "登录态文件不存在"
        return info
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        info["reason"] = f"登录态文件损坏（{e.__class__.__name__}）"
        return info
    cookies = raw.get("cookies") or []
    info["cookie_count"] = len(cookies)
    names = {str(c.get("name", "")) for c in cookies}
    need = REQUIRED_COOKIES.get(platform, ())
    if need and not (names & set(need)):
        info["reason"] = f"缺少会话 cookie（{'/'.join(need)}），需要重新登录"
        return info
    # 用文件修改时间近似登录时间（storage_state 由登录成功时写入）
    try:
        info["logged_at"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        pass
    info["ok"] = True
    return info


def list_accounts(platform: str) -> List[Dict[str, Any]]:
    """列出该平台在账号管理里登记的账号（含可用性判断，不含 cookie 内容）。"""
    out: List[Dict[str, Any]] = []
    for r in _rows(platform):
        fname = str(r.get("filePath") or "")
        path = COOKIE_DIR / Path(fname).name
        state = _inspect(path, platform)
        out.append({
            "id": r.get("id"),
            "name": r.get("userName") or "未命名账号",
            "file": path.name,
            "platform": platform,
            "platform_cn": PLATFORM_CN.get(platform, platform),
            "ok": state["ok"],
            "reason": state["reason"],
            "logged_at": state["logged_at"],
            "cookie_count": state["cookie_count"],
        })
    return out


def resolve(platform: str) -> Dict[str, Any]:
    """挑一个可用的登录态给 MediaCrawler 用。

    优先取最新的、cookie 校验通过的那个；一个都没有时抛 AccountError，
    错误文案直接告诉用户去哪儿登录。
    """
    accounts = list_accounts(platform)
    cn = PLATFORM_CN.get(platform, platform)
    if not accounts:
        raise AccountError(f"账号管理里还没有{cn}账号，请先到「账号管理」页扫码登录{cn}")
    for acc in accounts:
        if acc["ok"]:
            return {
                "path": str(COOKIE_DIR / acc["file"]),
                "name": acc["name"],
                "logged_at": acc["logged_at"],
                "platform": platform,
            }
    reasons = "；".join(f"{a['name']}：{a['reason']}" for a in accounts if a.get("reason"))
    raise AccountError(f"{cn}登录态不可用（{reasons or '未知原因'}），请到「账号管理」页重新登录")


if __name__ == "__main__":  # 手动排查用
    import sys

    for pf in ("xiaohongshu", "douyin"):
        print(f"--- {pf} ---")
        for a in list_accounts(pf):
            print(a)
        try:
            print("resolve:", resolve(pf))
        except AccountError as e:
            print("resolve 失败:", e)
