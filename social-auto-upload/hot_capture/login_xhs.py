# -*- coding: utf-8 -*-
"""
login_xhs.py: 用 MediaCrawler 的 CDP 有头浏览器完成小红书扫码登录。

导出二维码为 PNG 供用户扫描；登录成功后把 storage_state 存到
.run/xhs_storage_state.json，并同步生成 cookie 串供 cookie 登录复用。

用法:
  uv run python ../hot-capture/login_xhs.py --out ../hot-capture/data/xhs_qr.png \
      --state .run/xhs_storage_state.json --cookie-out .run/xhs_cookie.txt
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPS_ROOT = ROOT.parent
try:
    from capture import MC_ROOT as MC
except Exception:
    import os as _os
    MC = Path(_os.environ.get("MEDIA_CRAWLER_DIR", "")) or (APPS_ROOT / "media-crawler")
sys.path.insert(0, str(MC))

from playwright.async_api import async_playwright  # noqa: E402
from tools.cdp_browser import CDPBrowserManager  # noqa: E402
import config  # noqa: E402

XHS_HOME = "https://www.xiaohongshu.com"
QR_SELECTOR = "xpath=//img[@class='qrcode-img']"


def save_qr(data_url: str, out: Path):
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(data_url))
    print(f"[qr] 已保存二维码: {out} ({out.stat().st_size} bytes)", flush=True)


async def main_async(args):
    config.PLATFORM = "xhs"
    manager = CDPBrowserManager()
    async with async_playwright() as pw:
        ctx = await manager.launch_and_connect(
            playwright=pw, playwright_proxy=None, user_agent=None, headless=False,
        )
        page = await ctx.new_page()
        await page.goto(XHS_HOME, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)

        # 关键：清空残留 cookie 后重载，确保从「未登录」干净态开始。
        # CDP profile 可能残留失效/匿名 web_session，不清会导致「第 0 秒误判成功」。
        await ctx.clear_cookies()
        await page.reload(wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)

        def sess_value(cookies):
            return next((c["value"] for c in cookies if c["name"] == "web_session"), "")

        initial_session = sess_value(await ctx.cookies())
        print(f"[i] 初始 web_session: {'匿名/无' if not initial_session else initial_session[:10] + '...'}", flush=True)

        # 尝试拿二维码；没弹出登录框则点登录按钮
        src = ""
        try:
            await page.wait_for_selector(QR_SELECTOR, timeout=6000)
            src = str(await page.locator(QR_SELECTOR).get_attribute("src"))
        except Exception:
            pass
        if not src:
            print("[i] 未找到二维码，尝试点击登录入口", flush=True)
            for sel in ["text=登录", "//*[@id='app']//button[contains(.,'登录')]"]:
                try:
                    loc = page.locator(sel).first
                    if await loc.count():
                        await loc.click(timeout=4000)
                        await page.wait_for_timeout(2000)
                        src = str(await page.locator(QR_SELECTOR).get_attribute("src"))
                        break
                except Exception:
                    continue

        if src and not src.startswith("data:"):
            # 远程二维码 URL，下载转 base64
            import httpx
            r = await httpx.AsyncClient().get(src)
            src = "data:image/png;base64," + base64.b64encode(r.content).decode()
        if src:
            save_qr(src, Path(args.out))
        else:
            print("[!] 未能提取二维码，请在弹出的浏览器窗口中查看", flush=True)

        # 轮询登录成功：web_session 必须是「新值」且非匿名。
        # 真扫码登录后服务端会换发新 web_session；仅名字存在不算（匿名也有）。
        print("[i] 等待扫码登录（最长 240 秒）...", flush=True)
        ok = False
        for i in range(240):
            cookies = await ctx.cookies()
            cur = sess_value(cookies)
            if cur and cur != initial_session:
                ok = True
                print(f"[ok] 登录成功（第 {i}s），web_session 已换发新值", flush=True)
                break
            await asyncio.sleep(1)

        if ok:
            state = Path(args.state)
            state.parent.mkdir(parents=True, exist_ok=True)
            await ctx.storage_state(path=str(state))
            print(f"[ok] storage_state 已保存: {state}", flush=True)
            cookies = await ctx.cookies()
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
            cpath = Path(args.cookie_out)
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(cookie_str, encoding="utf-8")
            print(f"[ok] cookie 串已保存: {cpath}", flush=True)
        else:
            print("[!] 登录超时/未完成", flush=True)

        await manager.cleanup()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "hot_capture" / "xhs_qr.png"))
    ap.add_argument("--state", default=str(MC / ".run" / "xhs_storage_state.json"))
    ap.add_argument("--cookie-out", default=str(MC / ".run" / "xhs_cookie.txt"))
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
