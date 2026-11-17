# -*- coding: utf-8 -*-
"""
run_search.py: 把热搜词喂给 MediaCrawler 做关键词搜索抓取。

用法（需先有该平台有效登录态，见 README）:
  python run_search.py --platform xiaohongshu --keywords "美食,旅行" \
      --cookie-file ../media-crawler/.run/xhs_cookie.txt --max-notes 5

流程:
  1. 读 cookie 文件（name=value; ... 格式）
  2. 调 media-crawler 的 main.py（CDP 有头模式，规避风控）
  3. 抓取结果落在 media-crawler/data/<platform>/ 下的 jsonl
  4. 再跑 capture.py 归一化到统一 schema

注意：本脚本不破解签名、不绕过风控、不做账号池。登录态由用户扫码后提供。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
APPS_ROOT = APP_ROOT.parent
# 复用 capture 已解析好的路径（支持环境变量/conf 覆盖），避免多处不一致
try:
    from capture import MC_ROOT
except Exception:
    import os as _os
    MC_ROOT = Path(_os.environ.get("MEDIA_CRAWLER_DIR", "")) or (APPS_ROOT / "media-crawler")
PY = MC_ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = MC_ROOT / ".venv" / "bin" / "python"  # 类 Unix 回退

PLATFORM_CODE = {"xiaohongshu": "xhs", "douyin": "dy"}


def keywords_from_hot(top: int = 5, platforms=("douyin", "xiaohongshu")) -> str:
    """从热搜快照提取搜索关键词，逗号分隔（供 --keywords 直接用）。

    优先用 search_term（分词后的核心短词，命中率高）；
    无 search_term 时回退原句。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import capture  # noqa
    boards = capture.load_hot_boards()
    kws = capture.extract_hot_keywords(boards, platforms, top)
    # 去重保序，并过滤时政硬新闻（不适合创作向抓取）
    seen, out = set(), []
    for k in kws:
        if not capture.is_creative_suitable(k["title"]):
            continue
        t = k.get("search_term") or k["title"]
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return ",".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="调用 MediaCrawler 抓取爆款元数据")
    ap.add_argument("--platform", choices=list(PLATFORM_CODE), required=True)
    ap.add_argument("--keywords", default=None, help="不填则从热搜榜自动取词")
    ap.add_argument("--hot-top", type=int, default=5)
    ap.add_argument("--cookie-file", default=None, help="cookie 串文件（name=value; ...）")
    ap.add_argument("--max-notes", type=int, default=5)
    ap.add_argument("--no-comments", action="store_true")
    args = ap.parse_args(argv)

    auto_platforms = ("douyin",) if args.platform == "douyin" else ("xiaohongshu",)
    kws = args.keywords or keywords_from_hot(args.hot_top, auto_platforms)
    print(f"[i] 平台={args.platform} 关键词={kws}")

    cmd = [
        str(PY), "main.py",
        "--platform", PLATFORM_CODE[args.platform],
        "--type", "search",
        "--keywords", kws,
        "--lt", "cookie",
        "--crawler_max_notes_count", str(args.max_notes),
        "--get_media", "false",
        "--headless", "false",
    ]
    if args.no_comments:
        cmd += ["--get_comment", "false"]
    if args.cookie_file:
        cookie = Path(args.cookie_file).read_text(encoding="utf-8").strip()
        cmd += ["--cookies", cookie]

    print(f"[i] 执行: {' '.join(cmd[:6])} ... (cookie 已省略)")
    proc = subprocess.run(cmd, cwd=str(MC_ROOT))
    print(f"[i] MediaCrawler 退出码: {proc.returncode}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
