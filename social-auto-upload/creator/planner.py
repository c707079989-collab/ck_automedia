# -*- coding: utf-8 -*-
"""planner.py —— 把「爆款」或「一句话」变成可执行的创作方案。

三种入口（mode）：
    viral     从已分析的爆款推理：拆它的钩子/结构/可复用套路，换人设换场景做同构方案
    original  完全自创：给主题/人群/风格，直接出方案
    talking   口播类：出口播稿 + 分段提词卡 + 分镜 + 字幕，不出 AI 画面

为什么出片字段要英文：
    MiniMax H3 的文本编码器是多语模型，但英文提示词的镜头/光影/动作词命中率明显更稳；
    中文留在 visual / subtitle / voiceover 里给人看。
为什么单独抽 cast.identity_en：
    H3 纯文生视频会角色漂移。实测唯一低成本缓解手段是把同一段身份描述逐字复制进每镜 prompt，
    所以让模型只写一次，由本模块拼接，避免它自己每镜改写一遍。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config as C
from ._cli import agent_call, agent_extract_json

MAX_SHOT_SECONDS = 5.0


# ---------------------------------------------------------------- 提示词

_COMMON_RULES = """
你必须严格遵守：
1. 只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。
2. 所有分镜的 seconds 不超过 5（本机显卡单段上限），单条视频 3-8 个分镜。
3. shots[].prompt 必须是英文，用来驱动视频生成模型；写成"主体+动作+镜头运动+光线+声音"的连续描述，
   不超过 60 个英文单词，不要出现文字水印、字幕、logo 等描述。
3.1 如果该镜有台词（voiceover 非空），**必须**把原话一字不改地用双引号写进 prompt 结尾，
   格式固定为：says in Mandarin Chinese: "台词原句"。
   视频模型只有看到这句原话才可能对上口型，只写 "a man speaking" 一定会口胡。不许改写、缩写、翻译或省略。
4. 中文只放在 visual（给人看的画面说明）、subtitle（字幕）、voiceover（口播/旁白）里。
5. cast.identity_en 写一段英文，描述每个出场人物与主场景的固定特征（年龄/脸型/发型/服装/环境），
   会被逐字拼进每个分镜，所以不要写动作，也不要写"第几镜"。
6. 字段宁可短，不要编造不存在的信息。
""".strip()

_SCHEMA_HINT = """
输出 JSON 结构（字段全部必填）：
{
  "title": "成片标题，带钩子，20 字内",
  "genre": "内容类型，如 美食教程/剧情/口播科普",
  "format": "portrait 或 landscape",
  "duration_target": 整数秒，15-60 之间,
  "target_audience": "目标人群，一句话",
  "angle": "这条片子靠什么取胜，一句话",
  "hook": "前 3 秒怎么抓人，一句话（写清画面上发生什么、说了什么）",
  "identity": "中文版人物与场景设定，供人阅读",
  "cast": {"identity_en": "英文角色与场景固定描述"},
  "script": "整片台本，带时间轴的文字稿（口播/对白/旁白）",
  "shots": [
    {"idx": 1, "seconds": 4,
     "visual": "中文画面说明",
     "prompt": "english prompt for the video model",
     "camera": "运镜，如 手持跟拍/固定机位/推近",
     "subtitle": "该镜字幕，15 字内",
     "voiceover": "该镜的口播/对白，没有就写空字符串",
     "audio": "该镜音效或环境音"}
  ],
  "hashtags": ["话题1", "话题2", "话题3"],
  "publish_tips": "发布建议，一句话"
}
""".strip()


def _viral_prompt(analysis: Dict[str, Any], extra: str, fmt: str) -> str:
    a = analysis or {}
    brief = {
        "原标题": a.get("title", ""),
        "平台": a.get("platform", ""),
        "爆款判定": a.get("verdict", ""),
        "钩子": a.get("hook", ""),
        "结构": a.get("structure", []),
        "情绪价值": a.get("emotion", ""),
        "为什么爆": a.get("why_viral", []),
        "标题公式": a.get("title_formula", ""),
        "可复用套路": a.get("reusable", ""),
        "原片翻拍建议": a.get("remake", []),
        "风险": a.get("risks", ""),
    }
    return f"""你是一名短视频编导。下面是一条已经爆过的视频的结构拆解，请据此做一条**同构但不同款**的新片方案：
保留它验证过的钩子机制、节奏结构和情绪落点，但必须换人设、换场景、换具体内容，不能照抄原片。

原片拆解：
{json.dumps(brief, ensure_ascii=False, indent=2)}

补充要求：{extra or "无"}
画幅：{fmt}

{_COMMON_RULES}

{_SCHEMA_HINT}"""


def _original_prompt(brief: str, extra: str, fmt: str) -> str:
    return f"""你是一名短视频编导。请围绕下面的创作想法，出一条可以直接开拍的短视频方案。

创作想法：{brief}
补充要求：{extra or "无"}
画幅：{fmt}

{_COMMON_RULES}

{_SCHEMA_HINT}"""


def _talking_prompt(brief: str, extra: str, fmt: str) -> str:
    return f"""你是一名短视频口播编导。主题如下，请写一条**真人出镜口播**方案：
重口播稿质量（口语、短句、有钩子、有信息增量），画面只需要「人对着镜头说 + 少量辅助画面」，
不需要复杂的 AI 生成画面。

主题：{brief}
补充要求：{extra or "无"}
画幅：{fmt}

在遵守下面规则的基础上，额外要求：
- 画面上只写"真人半身出镜对镜头说话"这类简单英文描述（环境、景别、光线、表情），不要编造动作剧情
- shots[].prompt 结尾必须带 says in Mandarin Chinese: "这一镜的 voiceover 原句"，台词一字不改
- voiceover 必须是这一镜真正要念出口的话，不许留空、不许只写要点
- subtitle 是 voiceover 的逐句拆分（15 字内），方便直接当提词器和大字字幕
- script 是完整口播稿，可以直接照着念，带自然的口语停顿
- 每镜 voiceover 拼起来约等于 script

{_COMMON_RULES}

{_SCHEMA_HINT}"""


# ---------------------------------------------------------------- 归一化

def _to_float(v: Any, default: float) -> float:
    try:
        f = float(v)
        return f if f > 0 else default
    except (TypeError, ValueError):
        return default


def _as_list(v: Any) -> List[Any]:
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        return [x.strip() for x in re.split(r"[,，、;；\n]", v) if x.strip()]
    return []


def _clean_prompt(p: str) -> str:
    """去掉模型爱塞进提示词里的东西：字幕/水印要求、引号、换行。"""
    s = re.sub(r"\s+", " ", str(p or "")).strip().strip('"').strip("'")
    s = re.sub(r"(?i)\b(subtitle|watermark|logo|caption|text overlay)s?\b[^,.]*[,.]?", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _with_dialogue(body: str, voiceover: str) -> str:
    """把这一镜要说的原话拼进英文提示词。

    H3 会生成原生音轨，但提示词只写 "a man speaking to camera" 时它自己瞎编，
    听感就是口胡。实测补上 says in Mandarin Chinese: "原句" 后语音才落在稿子上。
    """
    v = (voiceover or "").strip()
    b = (body or "").strip()
    if not v or v in b:
        return b
    say = f'says in Mandarin Chinese: "{v}"'
    if not b:
        return "a person faces the camera and speaks, medium close-up, soft natural window light. " + say
    return f"{b.rstrip('. ')}. {say}"


def normalize(raw: Dict[str, Any], mode: str, fmt: str, source: Dict[str, Any]) -> Dict[str, Any]:
    """把 LLM 的自由发挥收敛成页面和出片都能直接吃的结构。"""
    raw = raw if isinstance(raw, dict) else {}
    cast = raw.get("cast") if isinstance(raw.get("cast"), dict) else {}
    identity_en = _clean_prompt(cast.get("identity_en") or raw.get("identity_en") or "")

    shots_in = raw.get("shots") if isinstance(raw.get("shots"), list) else []
    shots: List[Dict[str, Any]] = []
    for i, s in enumerate(shots_in):
        if not isinstance(s, dict):
            continue
        sec = min(MAX_SHOT_SECONDS, max(1.0, _to_float(s.get("seconds"), 4)))
        subtitle = str(s.get("subtitle") or "").strip()
        voiceover = str(s.get("voiceover") or "").strip()
        # 口播方案里模型偶尔只给字幕不给 voiceover：用字幕兜住，别让这一镜变成没人说话
        if mode == "talking" and not voiceover and subtitle:
            voiceover = subtitle
        # 出片 prompt 前面统一挂身份段，保证跨镜同一张脸/同一个屋子；
        # 结尾挂台词原句，否则模型不知道说什么，出来就是口胡。
        body = _with_dialogue(_clean_prompt(s.get("prompt")), voiceover)
        full = f"{identity_en}. {body}".strip(". ").strip() if identity_en else body
        shots.append({
            "idx": i + 1,
            "seconds": sec,
            "visual": str(s.get("visual") or "").strip(),
            "prompt": full,
            "prompt_body": body,
            "camera": str(s.get("camera") or "").strip(),
            "subtitle": subtitle,
            "voiceover": voiceover,
            "audio": str(s.get("audio") or "").strip(),
        })

    if not shots:
        shots = _fallback_shots(
            str(raw.get("title") or source.get("brief") or source.get("title") or "主题"),
            mode, source)

    total = sum(s["seconds"] for s in shots)
    want_fmt = str(raw.get("format") or fmt)
    if want_fmt not in C.FORMATS:
        want_fmt = fmt

    return {
        "id": "",
        "mode": mode,
        "title": str(raw.get("title") or "").strip()[:60] or "未命名方案",
        "genre": str(raw.get("genre") or "").strip(),
        "format": want_fmt,
        "duration_target": int(_to_float(raw.get("duration_target"), total) or total),
        "duration_planned": round(total, 1),
        "target_audience": str(raw.get("target_audience") or "").strip(),
        "angle": str(raw.get("angle") or "").strip(),
        "hook": str(raw.get("hook") or "").strip(),
        "identity": str(raw.get("identity") or raw.get("cast_zh") or "").strip(),
        "identity_en": identity_en,
        "script": str(raw.get("script") or "").strip(),
        "shots": shots,
        "hashtags": [str(x).strip().lstrip("#") for x in _as_list(raw.get("hashtags"))][:8],
        "publish_tips": str(raw.get("publish_tips") or "").strip(),
        "source": source,
        "engine": "agent",
        "rendered": [],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _fallback_shots(topic: str, mode: str, source: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """模型挂了/超时也要给一份能看、有内容的骨架，不让页面空着。

    爆款复刻时 source.analysis 里有采集阶段已分析好的拆解，直接拿它的结构/翻拍建议
    当分镜内容——这样即便模型超时，用户看到的也是"基于原片拆解的应急方案"，
    而不是全是"关于主题"的空壳。
    """
    src = source or {}
    analysis = src.get("analysis") if isinstance(src.get("analysis"), dict) else {}
    # 依次优先：翻拍建议（本就是一条条动作）→ 原片结构 → 通用节拍
    beats = [str(x).strip() for x in (
        analysis.get("remake") or analysis.get("structure") or []) if str(x).strip()]
    beats = [b for b in beats if b][:8]
    if not beats:
        beats = ["抛出问题/结果前置", "给出第一个要点", "给出第二个要点", "反常识补充", "收尾行动指令"]
    hook = str(analysis.get("hook") or "").strip()
    out = []
    for i, b in enumerate(beats):
        vo = b if mode == "talking" else ""
        body = _with_dialogue(
            f"a person speaks to camera in a clean bright room about {topic}, medium shot, natural light", vo)
        out.append({
            "idx": i + 1, "seconds": 4.0,
            "visual": f"{'钩子：' + hook if (i == 0 and hook) else ''}{b}".strip("："),
            "prompt": body, "prompt_body": body,
            "camera": "固定机位" if mode == "talking" else "手持跟拍",
            "subtitle": b[:15], "voiceover": vo, "audio": "环境音",
        })
    return out


def _slim_analysis(a: Dict[str, Any]) -> Dict[str, Any]:
    """把爆款分析里需要在「方案详情页」展示的字段挑出来存进方案。

    之前 source 只存了 item_id/title/url，方案详情页看不到任何和原爆款有关的东西，
    用户选了「爆款复刻」却感觉不到复刻了什么。分析结果留在方案里，
    既能对照原片，也能在原分析被覆盖/删掉后仍然可读。
    """
    a = a or {}
    out: Dict[str, Any] = {}
    for k in ("score", "verdict", "hook", "emotion", "title_formula", "reusable", "risks"):
        v = a.get(k)
        if v not in (None, "", []):
            out[k] = v
    for k in ("structure", "why_viral", "remake"):
        v = a.get(k)
        if isinstance(v, list) and v:
            out[k] = v[:6]
    return out


# ---------------------------------------------------------------- 对外入口

def build(mode: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """生成一份方案。payload 见 _viral_prompt / _original_prompt / _talking_prompt。

    返回 {ok, plan, error, ms}；ok=False 时 plan 仍然可用（降级骨架）。
    """
    cfg = C.load_config()
    fmt = str(payload.get("format") or "portrait")
    if fmt not in C.FORMATS:
        fmt = "portrait"
    extra = str(payload.get("extra") or "").strip()
    t0 = time.time()

    if mode == "viral":
        analysis = payload.get("analysis") or {}
        source = {"type": "viral", "item_id": payload.get("item_id", ""),
                  "title": payload.get("source_title", ""), "url": payload.get("url", ""),
                  "platform": payload.get("platform", ""),
                  "analysis": _slim_analysis(analysis)}
        prompt = _viral_prompt(analysis, extra, fmt)
    elif mode == "talking":
        brief = str(payload.get("brief") or "").strip() or "讲清一个观点"
        source = {"type": "talking", "brief": brief}
        prompt = _talking_prompt(brief, extra, fmt)
    else:
        mode = "original"
        brief = str(payload.get("brief") or "").strip() or "一个生活小技巧"
        source = {"type": "original", "brief": brief}
        prompt = _original_prompt(brief, extra, fmt)

    r = agent_call(prompt, timeout=float(cfg.get("agent_timeout") or 300),
                   model=str(cfg.get("agent_model") or ""))
    raw = agent_extract_json(r.get("text", "")) if r.get("ok") else None
    if not raw:
        plan = normalize({}, mode, fmt, source)
        plan["title"] = str(payload.get("brief") or payload.get("source_title") or plan["title"])[:60]
        # 模型没出结构化结果时，把已存的原片拆解铺进可读字段，避免"全空方案"
        if mode == "viral":
            a = payload.get("analysis") or {}
            base_title = str(payload.get("source_title") or "").strip()
            if base_title:
                plan["title"] = ("复刻·" + base_title)[:60]
            plan["hook"] = str(a.get("hook") or "").strip()
            plan["angle"] = str(a.get("reusable") or a.get("why_viral") or "").strip()[:120]
            if isinstance(a.get("why_viral"), list) and a["why_viral"]:
                plan["angle"] = "；".join(str(x) for x in a["why_viral"][:3])[:120]
            plan["script"] = "【应急方案·模型生成超时】下面是采集阶段已分析好的原片拆解，可直接照着改稿：\n" + \
                "钩子：" + plan["hook"] + "\n" + \
                "可复用套路：" + str(a.get("reusable") or "").strip() + "\n" + \
                "翻拍建议：" + "；".join(str(x) for x in (a.get("remake") or [])[:5])
            plan["identity"] = str(a.get("emotion") or "").strip()
        plan["engine"] = "fallback"
        plan["error"] = r.get("error") or "模型没有返回可解析的 JSON"
        plan["ms"] = int((time.time() - t0) * 1000)
        return {"ok": False, "plan": plan, "error": plan["error"], "ms": plan["ms"]}

    plan = normalize(raw, mode, fmt, source)
    plan["ms"] = int((time.time() - t0) * 1000)
    plan["model"] = r.get("model", "")
    return {"ok": True, "plan": plan, "error": "", "ms": plan["ms"]}


def from_source_item(item_id: str, analysis: Dict[str, Any], extra: str, fmt: str,
                     source_title: str = "", url: str = "") -> Dict[str, Any]:
    """从爆款工作台的分析结果直接起一条方案。

    source_title/url 从分析文件外层传进来（analysis 子对象里没有 title/url）。
    """
    payload = {
        "analysis": analysis,
        "item_id": item_id,
        "source_title": source_title or analysis.get("title", ""),
        "url": url or analysis.get("url", ""),
        "extra": extra,
        "format": fmt,
    }
    return build("viral", payload)
