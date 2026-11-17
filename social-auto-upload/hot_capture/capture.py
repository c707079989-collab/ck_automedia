# -*- coding: utf-8 -*-
"""
hot-capture: 爆款内容抓取增强层

职责（第 1 步）:
  1. 从热搜榜（公开 JSON / 仓库快照）提取「爆款关键词」入口
  2. 归一化 MediaCrawler 产出的元数据 jsonl 为统一 schema
  3. 落盘为下游 agent 分析可直接消费的结构化 JSON

边界:
  - 不下载视频本体（只保留封面/图片 URL 与互动元数据）
  - 不逆向签名、不绕过风控、不做账号池
  - 真实搜索抓取依赖平台登录态，由用户扫码后 MediaCrawler 自行完成

统一输出 schema (每条爆款内容):
  {
    "platform": "xiaohongshu" | "douyin",
    "source":   "hot_search" | "keyword_search",
    "keyword":  触发词,
    "rank":     榜单名次(仅热搜词条目),
    "hot":      榜单热度值(仅热搜词条目),
    "id":       内容 ID,
    "title":    标题,
    "desc":     正文/描述,
    "url":      内容链接,
    "cover":    封面 URL,
    "likes":    点赞,
    "comments": 评论,
    "collects": 收藏,
    "shares":   分享,
    "tags":     话题标签,
    "author":   作者昵称(上游已脱敏),
    "publish_time": 发布时间,
    "captured_at":   抓取时间
  }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# 路径发现：优先环境变量 -> conf.py 配置 -> 自动探测（apps/ 下的兄弟目录）。
# 主程序 social-auto-upload 内的子模块，采集依赖的第三方仓库在 apps/ 下。
APP_ROOT = Path(__file__).resolve().parent.parent  # social-auto-upload/
APPS_ROOT = APP_ROOT.parent                        # apps/


def _resolve_dir(env_key: str, conf_key: str, default_name: str) -> Path:
    v = os.environ.get(env_key)
    if not v:
        try:
            import conf as _conf
            v = getattr(_conf, conf_key, "")
        except Exception:
            v = ""
    p = Path(v).expanduser().resolve() if v else (APPS_ROOT / default_name)
    return p


HOT_REPO = _resolve_dir("HOT_SEARCH_CRAWLER_DIR", "HOT_SEARCH_CRAWLER_DIR", "hot-search-crawler")
MC_ROOT = _resolve_dir("MEDIA_CRAWLER_DIR", "MEDIA_CRAWLER_DIR", "media-crawler")

# 公开实时榜单（GitHub Pages），直连可用；失败时回退仓库快照
PUBLIC_HOT_JSON = "https://solarhell.github.io/hot-search-crawler/latest.json"
LOCAL_HOT_JSON = HOT_REPO / "data" / "latest-hourly.json"
# 归一化快照落盘目录（主程序内 data/hot_capture/）
SNAPSHOT_DIR = Path(os.environ.get("HOT_SNAPSHOT_DIR", "")) if os.environ.get("HOT_SNAPSHOT_DIR") else (APP_ROOT / "data" / "hot_capture")

SUPPORTED_PLATFORMS = ("douyin", "xiaohongshu")
# 热搜/统一 schema 用可读平台名，MediaCrawler 落盘目录用短代号
MC_DIR = {"xiaohongshu": "xhs", "douyin": "douyin", "xhs": "xhs", "dy": "douyin"}
CST = timezone(timedelta(hours=8))

# 分词停用词：代词/助词/程度副词/营销动词，避免拼出"用万能""美美出片"这类无效搜索词
_SEARCH_STOPWORDS = {
    "我", "你", "他", "她", "它", "我们", "你们", "的", "了", "在", "是", "有", "和", "把", "被",
    "也", "就", "都", "而", "且", "这", "那", "这个", "那个", "真的", "真的存在", "原来",
    "可以", "永远", "相信", "用", "万能", "美美", "速来", "get", "超", "日常", "我的", "更像",
    "拍下", "拍到", "定格", "蒸出", "拼豆上", "这一刻", "这一", "些", "了", "了",
}
_SEARCH_KEEP_LEN = (2, 6)


def extract_search_term(title: str) -> str:
    """从话题式热搜整句中抽取适合媒体搜索的核心短词。

    取最长的高信息量名词片段（2-6 字），而非拼凑，命中率更高。
    例: "我拍到了海鸥雨" -> "海鸥雨"；"超日常美食教程速来get" -> "美食教程"。
    失败时回退为原句（截断到 12 字）。
    """
    if not title:
        return ""
    try:
        import jieba
        words = [w.strip() for w in jieba.lcut(title)
                 if w.strip() and w.strip() not in _SEARCH_STOPWORDS
                 and not w.strip().isdigit()]
    except Exception:
        words = []
    candidates = [w for w in words if _SEARCH_KEEP_LEN[0] <= len(w) <= _SEARCH_KEEP_LEN[1]]
    if not candidates:
        candidates = [w for w in words if len(w) >= 2]
    if not candidates:
        return title[:12]
    # 取最长的单个名词；等长时取靠后的（通常更具体）
    return max(candidates, key=lambda w: (len(w), words.index(w)))[:_SEARCH_KEEP_LEN[1]]


# 时政/硬新闻类热搜：不适合"爆款视频创作"下游，且合规风险高，默认过滤
_POLITICAL_MARKERS = (
    "习近平", "普京", "特朗普", "总统", "总理", "主席", "APEC", "国足", "峰会",
    "外交", "会晤", "抵达深圳", "巴林", "卡尼", "辛鲍姆", "山东泰山", "主教练",
    "贸易伙伴", "工商领导", "斯洛伐克", "国事访问", "外交部", "联合国",
)


def is_creative_suitable(title: str) -> bool:
    """过滤时政硬新闻，仅保留生活/美食/旅行/风景/娱乐等创作向热搜。"""
    if not title:
        return False
    return not any(m in title for m in _POLITICAL_MARKERS)


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


# ---------- 1. 热搜词入口 ----------

def load_hot_boards(source: Optional[str] = None) -> Dict[str, Any]:
    """读取热搜榜快照。优先公开源，失败回退本地仓库快照。"""
    raw = None
    used = source or "public"
    if source == "local" or source is None:
        try:
            if source is None:
                with urllib.request.urlopen(PUBLIC_HOT_JSON, timeout=15) as r:
                    raw = json.loads(r.read().decode("utf-8"))
                used = "public"
        except Exception:
            raw = None
    if raw is None:
        with open(LOCAL_HOT_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
        used = "local"
    # 公开源个别板块可能临时失效（ok=False / items 空），用本地快照按平台补齐，
    # 避免"整体请求成功但目标平台无词"导致下游关键词为空。
    if used == "public":
        try:
            with open(LOCAL_HOT_JSON, "r", encoding="utf-8") as f:
                local = json.load(f)
            lmap = {b.get("platform"): b for b in local.get("boards", [])}
            filled = []
            for b in raw.get("boards", []):
                p = b.get("platform")
                lb = lmap.get(p)
                if (not b.get("ok") or not b.get("items")) and lb and lb.get("ok") and lb.get("items"):
                    b["ok"] = True
                    b["items"] = lb["items"]
                    b["_filled_from_local"] = True
                    filled.append(p)
            if filled:
                print(f"[i] 已从本地快照补齐失效板块: {', '.join(filled)}", file=sys.stderr)
        except Exception:
            pass
    raw["_source"] = used
    return raw


def extract_hot_keywords(
    boards: Dict[str, Any],
    platforms: Iterable[str] = SUPPORTED_PLATFORMS,
    top: int = 10,
) -> List[Dict[str, Any]]:
    """从榜单抽取爆款关键词，统一为 {platform, rank, title, hot, hot_text, tag, url}。"""
    out: List[Dict[str, Any]] = []
    wanted = set(platforms)
    for board in boards.get("boards", []):
        p = board.get("platform")
        if p not in wanted or not board.get("ok"):
            continue
        for item in board.get("items", [])[:top]:
            title = item.get("title")
            out.append({
                "platform": p,
                "rank": item.get("rank"),
                "title": title,
                "search_term": extract_search_term(title),
                "hot": item.get("hot"),
                "hot_text": item.get("hot_text"),
                "tag": item.get("tag"),
                "url": item.get("url"),
            })
    return out


# ---------- 2. MediaCrawler jsonl 归一化 ----------

def _to_int(v: Any) -> int:
    if v is None or v == "":
        return 0
    s = str(v).strip().lower().replace(",", "")
    mult = 1
    if s.endswith("万") or s.endswith("w"):
        mult, s = 10000, s[:-1]
    elif s.endswith("亿"):
        mult, s = 100000000, s[:-1]
    elif s.endswith("k"):
        mult, s = 1000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


def normalize_mc_record(rec: Dict[str, Any], platform: str, keyword: str = "",
                        hot_title: str = "") -> Dict[str, Any]:
    """把 MediaCrawler 的一条 jsonl 记录归一化为统一 schema。兼容 xhs / douyin 字段名差异。"""
    base = {
        "platform": platform,
        "source": "keyword_search",
        "keyword": keyword,
        "hot_title": hot_title,
        "rank": None,
        "hot": None,
        "id": rec.get("note_id") or rec.get("aweme_id") or rec.get("video_id") or "",
        "title": rec.get("title") or (rec.get("desc") or "")[:255],
        "desc": rec.get("desc", ""),
        "url": rec.get("note_url") or rec.get("video_url") or rec.get("aweme_url") or "",
        "cover": rec.get("image_list") or rec.get("cover") or rec.get("cover_url") or "",
        "likes": _to_int(rec.get("liked_count") or rec.get("digg_count")),
        "comments": _to_int(rec.get("comment_count")),
        "collects": _to_int(rec.get("collected_count") or rec.get("collect_count")),
        "shares": _to_int(rec.get("share_count")),
        "tags": rec.get("tag_list", ""),
        "author": rec.get("nickname", ""),
        "publish_time": rec.get("time") or rec.get("create_time"),
        "captured_at": now_iso(),
    }
    return base


def read_mc_jsonl(data_dir: Path, platform: str) -> List[Dict[str, Any]]:
    """读取 MediaCrawler 在该平台目录下产生的所有 jsonl 内容记录。"""
    dirname = MC_DIR.get(platform, platform)
    pdir = data_dir / dirname
    if not pdir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(pdir.rglob("*.jsonl")):
        # 跳过评论文件，只取内容
        if "comment" in f.name.lower():
            continue
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# ---------- 3. 组装与落盘 ----------

def build_capture(
    platforms: Iterable[str] = SUPPORTED_PLATFORMS,
    top: int = 10,
    hot_source: Optional[str] = None,
    data_dir: Path = MC_ROOT / "data",
    include_mc: bool = True,
) -> Dict[str, Any]:
    """产出一份完整抓取快照：热搜词 + 已抓到的爆款元数据。"""
    boards = load_hot_boards(hot_source)
    keywords = extract_hot_keywords(boards, platforms, top)
    # 短词 -> 热搜原句 映射，供下游 agent/生成步骤保留创作语义
    term2hot = {k.get("search_term"): k["title"] for k in keywords if k.get("search_term")}
    items: List[Dict[str, Any]] = []
    if include_mc:
        for p in platforms:
            for rec in read_mc_jsonl(data_dir, p):
                kw = rec.get("source_keyword", "")
                items.append(normalize_mc_record(rec, p, kw, term2hot.get(kw, "")))
    return {
        "schema_version": 1,
        "captured_at": now_iso(),
        "hot_source": boards.get("_source"),
        "hot_generated_at": boards.get("generated_at"),
        "keywords": keywords,
        "items": items,
        "stats": {
            "keyword_count": len(keywords),
            "item_count": len(items),
            "platforms": list(platforms),
        },
    }


def save_snapshot(snap: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(CST).strftime("%Y-%m-%d")
    path = out_dir / f"capture_{day}.json"
    # 同一天追加去重：按 (platform, id) 合并
    merged: Dict[tuple, Dict[str, Any]] = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            for it in prev.get("items", []):
                merged[(it.get("platform"), it.get("id"))] = it
        except Exception:
            pass
    for it in snap.get("items", []):
        merged[(it.get("platform"), it.get("id"))] = it
    snap["items"] = list(merged.values())
    snap["stats"]["item_count"] = len(snap["items"])
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="爆款内容抓取增强层")
    ap.add_argument("--platforms", default="douyin,xiaohongshu", help="逗号分隔")
    ap.add_argument("--top", type=int, default=10, help="每平台取热搜前 N 条")
    ap.add_argument("--hot-source", choices=["public", "local"], default=None,
                    help="榜单来源；默认先公开源后本地回退")
    ap.add_argument("--no-mc", action="store_true", help="只出热搜词，不读 MediaCrawler 产物")
    ap.add_argument("--data-dir", default=str(MC_ROOT / "data"))
    ap.add_argument("--out", default=str(SNAPSHOT_DIR))
    ap.add_argument("--print", action="store_true", help="把结果打到 stdout")
    args = ap.parse_args(argv)

    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    snap = build_capture(
        platforms=platforms,
        top=args.top,
        hot_source=args.hot_source,
        data_dir=Path(args.data_dir),
        include_mc=not args.no_mc,
    )
    path = save_snapshot(snap, Path(args.out))
    if args.print:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(f"[ok] 榜单词 {snap['stats']['keyword_count']} 条 / 元数据 {snap['stats']['item_count']} 条")
        print(f"[ok] 快照: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
