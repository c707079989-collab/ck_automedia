# -*- coding: utf-8 -*-
"""hot-capture 自测：不依赖网络与登录。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture  # noqa


def test_to_int():
    assert capture._to_int(None) == 0
    assert capture._to_int("") == 0
    assert capture._to_int("123") == 123
    assert capture._to_int("1.2w") == 12000
    assert capture._to_int("3.5万") == 35000  # 中文万字（MediaCrawler 实际格式如 8.1万）
    assert capture._to_int("2亿") == 200000000
    assert capture._to_int("5k") == 5000
    assert capture._to_int("1,024") == 1024
    assert capture._to_int("abc") == 0
    print("  [ok] test_to_int")


def test_extract_hot_keywords():
    boards = {"boards": [
        {"platform": "douyin", "ok": True, "items": [
            {"rank": 1, "title": "A", "hot": 100},
            {"rank": 2, "title": "B", "hot": 90},
        ]},
        {"platform": "xiaohongshu", "ok": True, "items": [
            {"rank": 1, "title": "C", "hot": 80, "hot_text": "80w"},
        ]},
        {"platform": "weibo", "ok": True, "items": [
            {"rank": 1, "title": "D", "hot": 70},
        ]},
        {"platform": "douyin", "ok": False, "items": [
            {"rank": 1, "title": "E", "hot": 999},
        ]},
    ]}
    kws = capture.extract_hot_keywords(boards, top=1)
    assert len(kws) == 2, kws  # 只有 douyin + xiaohongshu，各 1 条
    assert {k["title"] for k in kws} == {"A", "C"}, kws
    assert kws[1]["hot_text"] == "80w"
    print("  [ok] test_extract_hot_keywords")


def test_normalize_xhs():
    rec = {
        "note_id": "n1", "title": "标题", "desc": "正文",
        "note_url": "https://xhs/explore/n1", "image_list": "http://cover.jpg",
        "liked_count": "1.2w", "comment_count": "300", "collected_count": "88",
        "share_count": "7", "tag_list": "美食,旅行", "nickname": "用户***",
        "time": "2026-11-17", "source_keyword": "美食",
    }
    out = capture.normalize_mc_record(rec, "xiaohongshu", "美食", "热点原句")
    assert out["platform"] == "xiaohongshu"
    assert out["id"] == "n1"
    assert out["likes"] == 12000
    assert out["comments"] == 300
    assert out["collects"] == 88
    assert out["shares"] == 7
    assert out["tags"] == "美食,旅行"
    assert out["keyword"] == "美食"
    assert out["hot_title"] == "热点原句"
    assert out["cover"] == "http://cover.jpg"
    assert out["captured_at"]
    print("  [ok] test_normalize_xhs")


def test_search_term_and_filter():
    # 短词提取：话题整句 -> 核心短词（须为原句子串、长度 2-6）
    for title in ["我拍到了海鸥雨", "超日常美食教程速来get", "用万能旅行拍照姿势美美出片"]:
        term = capture.extract_search_term(title)
        assert 2 <= len(term) <= 6, (title, term)
        assert term in title, (title, term)
    # 时政过滤
    assert capture.is_creative_suitable("我拍到了海鸥雨") is True
    assert capture.is_creative_suitable("习近平将同普京特朗普会晤") is False
    assert capture.is_creative_suitable("2分钟读懂APEC") is False
    # 平台目录映射
    assert capture.MC_DIR["xiaohongshu"] == "xhs"
    assert capture.MC_DIR["douyin"] == "douyin"
    print("  [ok] test_search_term_and_filter")


def test_normalize_douyin():
    rec = {
        "aweme_id": "d1", "desc": "抖音文案", "video_url": "https://dy/d1",
        "digg_count": 5000, "comment_count": "120", "collect_count": "60",
        "share_count": "9", "create_time": 1731000000,
    }
    out = capture.normalize_mc_record(rec, "douyin", "热词")
    assert out["id"] == "d1"
    assert out["title"] == "抖音文案"  # desc 兜底标题
    assert out["likes"] == 5000
    assert out["collects"] == 60
    assert out["publish_time"] == 1731000000
    print("  [ok] test_normalize_douyin")


if __name__ == "__main__":
    print("hot-capture selftest:")
    test_to_int()
    test_extract_hot_keywords()
    test_search_term_and_filter()
    test_normalize_xhs()
    test_normalize_douyin()
    print("ALL PASSED")
