# -*- coding: utf-8 -*-
"""
analyzer.py —— 爆款内容分析引擎（social-auto-upload 主程序调用 agent）

两级分析:
  1. 规则引擎（永远可用，零外部依赖）:
     - 单条爆款评分（互动率/收藏赞比/评论赞比/时效）
     - 主题聚类（按 hot_title / keyword 聚合）
     - 标题套路提取（疑问句/数字/情绪词/emoji）
     - 发布时段与内容类型分布
     - 生成"可复用选题 + 创作要点"结构化建议
  2. LLM 增强（可选）:
     - 通过 OpenAI 兼容 /v1/chat/completions 调用 vLLM（如 Qwen3.8-27B）
     - 地址从 conf.py 的 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY 读取
     - 不可达 / 超时 / 未配置 -> 自动降级为规则引擎，不阻断主流程

边界: 只消费 hot_capture.capture 落盘的统一 schema 元数据，不重新抓取。
"""
from __future__ import annotations

import json
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import capture as _capture
except Exception:  # 作为包导入时
    from . import capture as _capture

CST = timezone(timedelta(hours=8))

# ---------- LLM 配置（conf.py 可覆盖）----------

_LLM_CONFIG_FILE = Path(__file__).resolve().parent / "llm_config.json"


def load_llm_config_file() -> Dict[str, str]:
    """读取本地 LLM 配置文件（由前端配置接口写入）。"""
    try:
        if _LLM_CONFIG_FILE.exists():
            d = json.loads(_LLM_CONFIG_FILE.read_text(encoding="utf-8"))
            return {k: str(v) for k, v in d.items() if isinstance(v, (str, int, float))}
    except Exception:
        pass
    return {}


def save_llm_config_file(cfg: Dict[str, str]) -> None:
    _LLM_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _llm_config() -> Dict[str, str]:
    cfg = {"base_url": "", "model": "", "api_key": "", "timeout": "20"}
    # 优先级：环境变量 > 本地配置文件 > conf.py
    file_cfg = load_llm_config_file()
    for key in cfg:
        env_v = os.environ.get(f"LLM_{key.upper()}", "")
        cfg[key] = env_v or file_cfg.get(key, "")
    try:
        import conf as _conf
        mapping = {
            "base_url": ("LLM_BASE_URL", "VLLM_BASE_URL", "OPENAI_BASE_URL"),
            "model": ("LLM_MODEL", "VLLM_MODEL"),
            "api_key": ("LLM_API_KEY", "OPENAI_API_KEY"),
            "timeout": ("LLM_TIMEOUT",),
        }
        for key, names in mapping.items():
            if cfg[key]:
                continue
            for n in names:
                v = getattr(_conf, n, "")
                if v:
                    cfg[key] = str(v)
                    break
    except Exception:
        pass
    if not cfg["timeout"]:
        cfg["timeout"] = "20"
    return cfg


# ---------- 规则引擎 ----------

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_QUESTION_RE = re.compile(r"[?？]|为什么|怎么|如何|是否|吗$|呢$")
_NUM_RE = re.compile(r"\d")
_EMOTION_WORDS = ("绝了", "神器", "必看", "震惊", "建议", "收藏", "保姆", "干货", "救命", "杀疯", "万能", "宝藏")


def _safe_num(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def score_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """给单条爆款内容打分（0-100）并给出命中理由。"""
    likes = _safe_num(it.get("likes"))
    comments = _safe_num(it.get("comments"))
    collects = _safe_num(it.get("collects"))
    shares = _safe_num(it.get("shares"))
    title = str(it.get("title") or "")

    reasons: List[str] = []
    # 互动体量分（对数刻度，0-40）
    import math
    vol = min(40.0, math.log10(likes + 1) * 8)
    if likes >= 50000:
        reasons.append(f"高赞({likes // 10000}万)")
    elif likes >= 10000:
        reasons.append(f"万赞({likes // 10000}万+)")

    # 互动质量分（0-35）：收藏赞比 + 评论赞比 + 分享赞比
    quality = 0.0
    if likes > 0:
        cr = collects / likes
        cmr = comments / likes
        sr = shares / likes
        quality += min(15.0, cr * 20)   # 收藏赞比高=实用性强
        quality += min(10.0, cmr * 100)  # 评论赞比高=话题性强
        quality += min(10.0, sr * 100)   # 分享赞比高=传播性强
        if cr > 0.5:
            reasons.append("收藏率高(实用性强)")
        if cmr > 0.05:
            reasons.append("评论活跃(话题性强)")

    # 标题结构分（0-25）
    title_score = 0.0
    if _QUESTION_RE.search(title):
        title_score += 8
        reasons.append("疑问句标题")
    if _NUM_RE.search(title):
        title_score += 6
        reasons.append("含数字")
    if _EMOJI_RE.search(title):
        title_score += 5
    if any(w in title for w in _EMOTION_WORDS):
        title_score += 6
        reasons.append("情绪/干货词")

    total = round(min(100.0, vol + quality + title_score), 1)
    return {
        "id": it.get("id", ""),
        "title": title,
        "score": total,
        "breakdown": {
            "volume": round(vol, 1),
            "quality": round(quality, 1),
            "title": round(title_score, 1),
        },
        "metrics": {"likes": likes, "comments": comments, "collects": collects, "shares": shares},
        "reasons": reasons,
        "hot_title": it.get("hot_title", ""),
        "platform": it.get("platform", ""),
    }


def _title_patterns(titles: List[str]) -> List[Dict[str, Any]]:
    n = len(titles) or 1
    pats = {
        "疑问句": sum(1 for t in titles if _QUESTION_RE.search(t)),
        "含数字": sum(1 for t in titles if _NUM_RE.search(t)),
        "含emoji": sum(1 for t in titles if _EMOJI_RE.search(t)),
        "情绪/干货词": sum(1 for t in titles if any(w in t for w in _EMOTION_WORDS)),
    }
    return [{"pattern": k, "count": v, "ratio": round(v / n, 2)} for k, v in
            sorted(pats.items(), key=lambda x: -x[1])]


def rule_analyze(items: List[Dict[str, Any]], keywords: List[Dict[str, Any]]) -> Dict[str, Any]:
    """纯规则分析，产出结构化报告。"""
    scored = [score_item(it) for it in items]
    scored.sort(key=lambda x: -x["score"])
    top = scored[:20]

    # 主题聚类（按 hot_title，其次 keyword）
    clusters: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in scored:
        key = s["hot_title"] or s.get("platform", "") or "其他"
        clusters[key].append(s)
    theme_stats = []
    for theme, group in clusters.items():
        theme_stats.append({
            "theme": theme,
            "count": len(group),
            "avg_score": round(sum(g["score"] for g in group) / len(group), 1),
            "total_likes": sum(g["metrics"]["likes"] for g in group),
            "top_title": group[0]["title"] if group else "",
        })
    theme_stats.sort(key=lambda x: -x["avg_score"])

    scores = [s["score"] for s in scored]
    likes = [s["metrics"]["likes"] for s in scored]
    patterns = _title_patterns([s["title"] for s in scored])

    # 可复用选题建议：取头部主题 + 其爆款共性
    suggestions = []
    for t in theme_stats[:5]:
        grp = clusters[t["theme"]]
        top_reasons = Counter(r for g in grp for r in g["reasons"])
        suggestions.append({
            "theme": t["theme"],
            "why": [k for k, _ in top_reasons.most_common(3)],
            "example_title": t["top_title"],
            "avg_score": t["avg_score"],
        })

    return {
        "engine": "rule",
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "summary": {
            "item_count": len(items),
            "keyword_count": len(keywords),
            "avg_score": round(statistics.mean(scores), 1) if scores else 0,
            "max_score": max(scores) if scores else 0,
            "median_likes": int(statistics.median(likes)) if likes else 0,
            "total_likes": sum(likes),
        },
        "top_items": top,
        "theme_stats": theme_stats,
        "title_patterns": patterns,
        "suggestions": suggestions,
        "keywords": keywords,
    }


# ---------- LLM 增强 ----------

_LLM_PROMPT = """你是短视频内容策略分析师。下面是一个平台的热搜词和爆款笔记元数据（JSON）。
请基于数据给出简洁、可执行的爆款拆解，直接输出 JSON（不要 markdown 代码块）：

{{
  "trend_summary": "一句话概括这批内容的爆款趋势",
  "winning_factors": ["爆款共性1", "爆款共性2", "爆款共性3"],
  "audience": "目标受众画像（简短）",
  "content_formula": "可复用的内容公式（如 开头钩子+中间展示+结尾互动）",
  "topic_ideas": ["选题建议1", "选题建议2", "选题建议3"],
  "risks": "需要注意的合规/同质化风险"
}}

数据：
{data}
"""


def llm_analyze(data: Dict[str, Any], cfg: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """调 OpenAI 兼容 API 做分析；不可达/未配置返回 None（由调用方降级）。"""
    cfg = cfg or _llm_config()
    if not cfg.get("base_url") or not cfg.get("model"):
        return None
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": _LLM_PROMPT.format(
            data=json.dumps({
                "keywords": data.get("keywords", []),
                "top_items": data.get("top_items", [])[:12],
                "theme_stats": data.get("theme_stats", [])[:6],
            }, ensure_ascii=False)
        )}],
        "temperature": 0.3,
        "max_tokens": 1024,
    }
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        import requests
        r = requests.post(url, json=payload, headers=headers, timeout=float(cfg.get("timeout", 20)))
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        # 容忍模型输出 markdown 代码块
        content = content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```[a-z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content).strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except Exception:
        return None


def analyze(snapshot: Dict[str, Any], use_llm: bool = True) -> Dict[str, Any]:
    """主入口：规则分析 + 可选 LLM 增强。LLM 失败自动保留规则结果。"""
    items = snapshot.get("items", [])
    keywords = snapshot.get("keywords", [])
    report = rule_analyze(items, keywords)
    if use_llm:
        llm = llm_analyze(report)
        if llm:
            report["engine"] = "llm+rule"
            report["llm"] = llm
            report["llm_status"] = "ok"
        else:
            cfg = _llm_config()
            report["llm_status"] = "unavailable" if (cfg.get("base_url") and cfg.get("model")) else "not_configured"
    return report


# ---------- 快照读取 ----------

def list_snapshots() -> List[Dict[str, Any]]:
    """列出可用的落盘快照。"""
    d = _capture.SNAPSHOT_DIR
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("capture_*.json"), reverse=True):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "file": f.name,
                "captured_at": snap.get("captured_at", ""),
                "items": len(snap.get("items", [])),
                "keywords": len(snap.get("keywords", [])),
                "platforms": snap.get("stats", {}).get("platforms", []),
            })
        except Exception:
            continue
    return out


def load_snapshot(filename: Optional[str] = None) -> Optional[Dict[str, Any]]:
    d = _capture.SNAPSHOT_DIR
    if filename:
        f = d / Path(filename).name  # 防目录穿越
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    snaps = sorted(d.glob("capture_*.json"), reverse=True) if d.exists() else []
    return json.loads(snaps[0].read_text(encoding="utf-8")) if snaps else None


def save_report(report: Dict[str, Any], filename: Optional[str] = None) -> Path:
    d = _capture.SNAPSHOT_DIR
    d.mkdir(parents=True, exist_ok=True)
    name = filename or f"report_{datetime.now(CST).strftime('%Y-%m-%d_%H%M%S')}.json"
    p = d / Path(name).name
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ============================================================
# 单条爆款分析（「分析」按钮）
# ============================================================

ANALYSIS_DIR = _capture.SNAPSHOT_DIR / "analysis"

_ITEM_PROMPT = """你是短视频爆款拆解专家。下面是一条来自{brand}的内容元数据（JSON）。
请拆解它为什么能成为爆款，并给出我可以照着拍的复用方案。

要求：直接输出 JSON，不要调用任何工具，不要输出思考过程；每个字段控制在 60 字内，
structure 最多 4 条，remake 最多 3 条，why_viral 最多 3 条。

{cover_line}
只输出一个 JSON 对象，不要 markdown 代码块、不要解释，结构如下：
{{
  "verdict": "一句话结论，说清这条内容的爆点是什么",
  "score": 0-100 的整数，代表这条内容的爆款潜力,
  "hook": "开头/封面钩子是怎么抓注意力的",
  "structure": ["分段拆解1（如 0-3 秒：...）", "分段拆解2", "分段拆解3"],
  "emotion": "它戳中的情绪或需求",
  "why_viral": ["爆款原因1", "爆款原因2", "爆款原因3"],
  "title_formula": "标题可复用的公式（用 X/Y/Z 占位）",
  "reusable": "把这条内容抽象成可复用脚本公式",
  "remake": ["翻拍建议1（具体到镜头/文案）", "翻拍建议2", "翻拍建议3"],
  "risks": "同质化或合规风险（没有就写'无'）"
}}

内容元数据：
{data}
"""

_PLATFORM_CN = {"xiaohongshu": "小红书", "xhs": "小红书", "douyin": "抖音", "dy": "抖音"}


def _agent_cfg() -> Dict[str, str]:
    """agent 相关配置，复用 llm_config.json（不与 vLLM 的 base_url/model 混用）。"""
    cfg = load_llm_config_file()
    return {
        "model": str(cfg.get("agent_model", "") or ""),
        "timeout": str(cfg.get("agent_timeout", "") or "240"),
        "enabled": str(cfg.get("agent_enabled", "1")),
        "use_cover": str(cfg.get("agent_use_cover", "0")),
    }


def _analysis_file(item: Dict[str, Any]) -> Path:
    plat = str(item.get("platform") or "na")
    safe = re.sub(r"[^\w\-]", "_", str(item.get("id") or ""))[:48] or "unknown"
    return ANALYSIS_DIR / f"{plat}_{safe}.json"


def load_item_analysis(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    p = _analysis_file(item)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def analyzed_ids() -> List[str]:
    """已分析过的条目键（platform:id），供列表打标。"""
    if not ANALYSIS_DIR.exists():
        return []
    out = []
    for f in ANALYSIS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append(f"{d.get('platform', '')}:{d.get('item_id', '')}")
        except Exception:
            continue
    return out


def rule_analyze_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """单条规则分析，agent 不可用时的兜底（保证按钮永远有结果）。"""
    s = score_item(item)
    likes = s["metrics"]["likes"]
    collects = s["metrics"]["collects"]
    comments = s["metrics"]["comments"]
    title = s["title"]
    reasons = s["reasons"] or ["互动体量中等"]
    formula = "数字/疑问钩子 + 场景痛点 + 可复制步骤 + 收藏式结尾"
    return {
        "engine": "rule",
        "verdict": f"互动量 {likes} 赞 / {collects} 收藏，属于{'头部' if s['score'] >= 70 else '中等'}爆款",
        "score": s["score"],
        "hook": f"标题「{title[:30]}」用{'疑问' if _QUESTION_RE.search(title) else '陈述'}式开场",
        "structure": ["封面/标题先给结果", "正文给可复制步骤或对比", "结尾引导收藏评论"],
        "emotion": "实用需求驱动的收藏型内容" if collects and collects >= likes * 0.3 else "情绪共鸣驱动的传播型内容",
        "why_viral": reasons,
        "title_formula": "「场景/人群 + 量化结果 + 情绪词」",
        "reusable": formula,
        "remake": [
            "沿用同一钩子结构，把主题替换成你所在领域的具体场景",
            "封面用同构图但换主体，保留高对比配色",
            f"结尾加一句互动引导，对标这条的 {comments} 条评论",
        ],
        "risks": "规则引擎只能看元数据，建议接入 agent 做逐帧拆解",
        "breakdown": s["breakdown"],
        "metrics": s["metrics"],
    }


def agent_analyze_item(item: Dict[str, Any], cfg: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """调 WorkBuddy CLI 做单条拆解。返回 {ok, analysis, error, error_kind, ms, model}。"""
    import agent_cli as _agent  # 延迟导入，避免影响不装 CLI 的环境
    import cover_cache as _covers

    cfg = cfg or _agent_cfg()
    cover_path = _covers.local_path(item.get("cover_file", ""))
    # 默认不喂图：本机 custom-local:qwen3.8-27b 是纯文本模型，让它读图会卡到超时。
    # 换成支持视觉的模型后，把 llm_config.json 的 agent_use_cover 设为 "1" 即可开启。
    use_cover = str(cfg.get("use_cover", "0")) in ("1", "true", "True")
    cover_line = ""
    if use_cover and cover_path:
        cover_line = (f"封面图已下载到本地：{cover_path}\n请先看一眼封面，把封面钩子写进 hook 字段。\n")
    platform = str(item.get("platform") or "")
    prompt = _ITEM_PROMPT.format(
        brand="小红书" if platform in ("xiaohongshu", "xhs") else "抖音",
        platform_cn=_PLATFORM_CN.get(platform, platform),
        cover_line=cover_line,
        data=json.dumps({
            "title": item.get("title"),
            "desc": (item.get("desc") or "")[:400],
            "tags": item.get("tags"),
            "author": item.get("author"),
            "likes": item.get("likes"),
            "collects": item.get("collects"),
            "comments": item.get("comments"),
            "shares": item.get("shares"),
            "keyword": item.get("keyword"),
            "hot_title": item.get("hot_title"),
            "url": item.get("url"),
        }, ensure_ascii=False),
    )
    r = _agent.call(prompt, model=cfg.get("model", ""),
                    timeout=float(cfg.get("timeout", "240") or 240))
    if not r["ok"]:
        return {"ok": False, "analysis": None, "error": r.get("error", ""),
                "error_kind": r.get("error_kind", ""), "ms": r.get("ms", 0), "model": r.get("model", "")}
    parsed = _agent.extract_json(r["text"])
    if not parsed:
        return {"ok": False, "analysis": None,
                "error": "agent 返回的不是 JSON，已丢弃原始文本",
                "error_kind": "parse", "ms": r.get("ms", 0),
                "model": r.get("model", ""), "raw_text": r["text"][:800]}
    return {"ok": True, "analysis": parsed, "error": "", "error_kind": "",
            "ms": r.get("ms", 0), "model": r.get("model", "")}


def analyze_item(item: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """单条分析主入口：缓存 -> agent -> 规则兜底，结果落盘。

    返回 {engine, analysis, cached, agent_status, ms, error}
    """
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    if not force:
        cached = load_item_analysis(item)
        if cached:
            return {"engine": cached.get("engine", ""), "analysis": cached.get("analysis", {}),
                    "cached": True, "agent_status": "cached", "ms": 0, "error": ""}

    cfg = _agent_cfg()
    result: Dict[str, Any]
    if cfg.get("enabled", "1") not in ("0", "false", "False"):
        r = agent_analyze_item(item, cfg)
        if r["ok"]:
            result = {"engine": "agent", "analysis": r["analysis"], "cached": False,
                      "agent_status": "ok", "ms": r["ms"], "error": "",
                      "model": r.get("model", "")}
        else:
            result = {"engine": "rule", "analysis": rule_analyze_item(item), "cached": False,
                      "agent_status": r.get("error_kind", "unknown"), "ms": r["ms"],
                      "error": r.get("error", "")}
    else:
        result = {"engine": "rule", "analysis": rule_analyze_item(item), "cached": False,
                  "agent_status": "disabled", "ms": 0, "error": ""}

    payload = {
        "platform": item.get("platform", ""),
        "item_id": item.get("id", ""),
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "analyzed_at": datetime.now(CST).isoformat(timespec="seconds"),
        **result,
    }
    _analysis_file(item).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def agent_status(probe: bool = False) -> Dict[str, Any]:
    import agent_cli as _agent
    st = _agent.status(probe=probe)
    st["configured_model"] = _agent_cfg().get("model") or st.get("model", "")
    st["enabled"] = _agent_cfg().get("enabled", "1") not in ("0", "false", "False")
    return st
