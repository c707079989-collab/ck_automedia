# -*- coding: utf-8 -*-
"""
domains.py —— 领域词表与关键词展开

为什么需要它:
  热搜榜只覆盖"当下正在爆"的话题，用户想按自己的赛道（美食/旅行/穿搭…）稳定取材，
  就需要一层「领域 -> 高命中搜索词」的映射。

设计要点:
  - 词条一律取 2-6 字的核心名词，而不是完整句子。上一轮实测：整句话题做搜索词
    命中率为 0，短词（出片/古诗词/海鸥）能搜到真实高赞笔记。
  - 领域词只是"起点"，前端可再补自定义词，两者合并去重。
  - "抓多少条"按总上限分配，均分到每个关键词，余数从前往后补，
    避免某个宽泛词（如"美食"）把额度全吃光。
"""
from __future__ import annotations

from typing import Dict, Iterable, List

# 领域 -> 搜索词。词条按"能搜出内容"优先，避免过于抽象的品类名。
DOMAINS: Dict[str, List[str]] = {
    "美食": ["家常菜", "一人食", "空气炸锅", "早餐", "减脂餐", "探店", "深夜食堂", "烘焙"],
    "旅行": ["出片机位", "小众目的地", "citywalk", "海岛", "自驾", "旅行攻略", "露营", "民宿"],
    "穿搭": ["秋冬穿搭", "小个子穿搭", "通勤穿搭", "显瘦", "叠穿", "平价穿搭", "复古穿搭"],
    "美妆": ["伪素颜", "新手化妆", "早八妆", "口红试色", "护肤", "眼妆教程", "发型"],
    "家居": ["小户型", "收纳", "出租房改造", "厨房收纳", "卧室改造", "氛围感", "装修避坑"],
    "母婴": ["辅食", "育儿经验", "亲子游戏", "宝宝穿搭", "绘本推荐", "月子", "早教"],
    "健身": ["居家健身", "减脂", "腹肌", "瑜伽", "体态矫正", "拉伸", "增肌餐"],
    "宠物": ["养猫日常", "狗狗训练", "宠物用品", "猫饭", "多猫家庭", "宠物拍照"],
    "摄影": ["手机摄影", "构图", "人像姿势", "调色", "胶片感", "vlog拍摄", "打光"],
    "职场": ["职场穿搭", "面试", "简历", "副业", "办公效率", "打工人", "转行"],
    "学习": ["学习方法", "笔记", "考研", "英语口语", "时间管理", "考公", "自律"],
    "情感": ["恋爱", "婚姻", "原生家庭", "沟通", "自我成长", "独居", "情绪"],
    "好物": ["平价好物", "开箱", "测评", "回购", "国货", "家居好物", "数码好物"],
    "手工": ["手作", "粘土", "编织", "拼豆", "手工教程", "改造"],
    "三农": ["乡村生活", "赶海", "果园", "农家菜", "田野", "丰收"],
}

# 前端展示顺序（也作为默认推荐领域）
RECOMMENDED = ["美食", "旅行", "穿搭", "美妆", "家居", "好物"]


def domain_options() -> List[Dict]:
    """给前端用的领域清单。"""
    return [
        {"name": name, "keyword_count": len(words), "keywords": words,
         "recommended": name in RECOMMENDED}
        for name, words in DOMAINS.items()
    ]


def resolve_keywords(
    domains: Iterable[str],
    custom: str = "",
    limit: int = 20,
) -> List[Dict]:
    """把领域 + 自定义词展开成带配额的搜索词列表。

    返回 [{"keyword": str, "quota": int, "from": "domain:美食" | "custom"}]
    总配额不超过 limit，且每个词至少 1 条。
    """
    picked: List[str] = []
    origin: Dict[str, str] = {}
    domain_words: List[str] = []
    for d in domains or []:
        d = str(d).strip()
        if not d:
            continue
        words = DOMAINS.get(d)
        if not words:            # 未知领域名当成自定义词直接用
            words = [d]
        for w in words:
            if w not in origin:
                origin[w] = f"domain:{d}"
                domain_words.append(w)

    custom_words = _split_custom(custom)
    for w in custom_words:
        origin.setdefault(w, "custom")

    # 排序：用户手填的词优先（意图更明确），领域词按原顺序跟在后面；
    # 条数不够时被截掉的应该是领域词，而不是用户特意加的。
    seen = set()
    for w in custom_words + domain_words:
        if w and w not in seen:
            seen.add(w)
            picked.append(w)

    if not picked:
        return []

    limit = max(1, int(limit or 1))
    # 关键词比要的条数还多时，只取前 limit 个（领域顺序即优先级），避免超额抓取
    if len(picked) > limit:
        picked = picked[:limit]

    n = len(picked)
    base = limit // n
    rem = limit % n
    out: List[Dict] = []
    for i, w in enumerate(picked):
        quota = base + (1 if i < rem else 0)
        out.append({"keyword": w, "quota": max(1, quota), "from": origin[w]})
    return out


def _split_custom(custom: str) -> List[str]:
    """自定义词输入：支持中英文逗号、顿号、空格、换行分隔。"""
    if not custom:
        return []
    s = str(custom)
    for sep in ("，", "、", ";", "；", "\n", "\t", "|"):
        s = s.replace(sep, ",")
    parts = [p.strip() for p in s.split(",")]
    # 空格分隔仅在整串没有逗号时启用，避免把"秋冬穿搭"这类词拆坏
    if len(parts) == 1 and " " in s:
        parts = [p.strip() for p in s.split(" ")]
    return [p for p in parts if p]
