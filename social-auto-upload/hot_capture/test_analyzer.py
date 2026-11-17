# -*- coding: utf-8 -*-
"""分析引擎自测：不依赖网络与 LLM。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyzer  # noqa


SAMPLE_SNAPSHOT = {
    "keywords": [
        {"platform": "xiaohongshu", "title": "用万能旅行拍照姿势美美出片", "search_term": "出片"},
        {"platform": "xiaohongshu", "title": "我拍到了海鸥雨", "search_term": "海鸥"},
    ],
    "items": [
        {"id": "a1", "platform": "xiaohongshu", "keyword": "出片", "hot_title": "用万能旅行拍照姿势美美出片",
         "title": "为什么这张照片这么出片？3个万能姿势", "likes": 66000, "comments": 379,
         "collects": 106894, "shares": 10317},
        {"id": "a2", "platform": "xiaohongshu", "keyword": "海鸥", "hot_title": "我拍到了海鸥雨",
         "title": "海鸥：你冒昧吗？？？", "likes": 6141, "comments": 82,
         "collects": 923, "shares": 534},
        {"id": "a3", "platform": "xiaohongshu", "keyword": "海鸥", "hot_title": "我拍到了海鸥雨",
         "title": "在斯坦利被海鸥狂追200m", "likes": 55093, "comments": 1136,
         "collects": 5353, "shares": 6242},
    ],
}


def test_score_item():
    s = analyzer.score_item(SAMPLE_SNAPSHOT["items"][0])
    assert 0 <= s["score"] <= 100
    assert s["score"] > analyzer.score_item(SAMPLE_SNAPSHOT["items"][1])["score"]
    assert any("赞" in r for r in s["reasons"])
    assert "疑问句标题" in s["reasons"] or "含数字" in s["reasons"]
    print("  [ok] test_score_item")


def test_rule_analyze():
    r = analyzer.rule_analyze(SAMPLE_SNAPSHOT["items"], SAMPLE_SNAPSHOT["keywords"])
    assert r["engine"] == "rule"
    assert r["summary"]["item_count"] == 3
    assert r["summary"]["total_likes"] == 66000 + 6141 + 55093
    assert len(r["top_items"]) == 3
    assert r["top_items"][0]["id"] == "a1"
    assert len(r["theme_stats"]) == 2  # 两个 hot_title 主题
    assert r["theme_stats"][0]["theme"]
    assert any(p["pattern"] == "疑问句" for p in r["title_patterns"])
    assert len(r["suggestions"]) >= 1
    print("  [ok] test_rule_analyze")


def test_analyze_degrade():
    # 未配置 LLM 时降级，engine 仍为 rule，不抛异常
    r = analyzer.analyze(SAMPLE_SNAPSHOT, use_llm=True)
    assert r["engine"] in ("rule", "llm+rule")
    assert "llm_status" in r
    assert r["summary"]["item_count"] == 3
    print("  [ok] test_analyze_degrade")


def test_llm_config():
    cfg = analyzer._llm_config()
    assert set(["base_url", "model", "api_key", "timeout"]).issubset(cfg.keys())
    print("  [ok] test_llm_config")


if __name__ == "__main__":
    print("analyzer selftest:")
    test_score_item()
    test_rule_analyze()
    test_analyze_degrade()
    test_llm_config()
    print("ALL PASSED")
