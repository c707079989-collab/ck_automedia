# -*- coding: utf-8 -*-
"""爆款工作台自测（不联网、不调 CLI）。

覆盖：领域展开 / 关键词配额 / 封面键 / JSON 提取 / 规则兜底 / 任务状态读写。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_cli
import analyzer
import cover_cache
import domains
import pipeline


def test_domain_options():
    opts = domains.domain_options()
    assert len(opts) >= 10, len(opts)
    assert all(o["keyword_count"] >= 5 for o in opts)
    assert any(o["name"] == "美食" for o in opts)
    assert all(o["recommended"] for o in opts if o["name"] in domains.RECOMMENDED)


def test_resolve_keywords_quota():
    kws = domains.resolve_keywords(["美食"], "", 20)
    assert kws, "应展开出关键词"
    assert all(k["from"] == "domain:美食" for k in kws)
    assert sum(k["quota"] for k in kws) == 20, sum(k["quota"] for k in kws)
    assert all(k["quota"] >= 1 for k in kws)


def test_resolve_keywords_multi_and_custom():
    kws = domains.resolve_keywords(["美食", "旅行"], "新疆, 自驾", 10)
    words = [k["keyword"] for k in kws]
    assert len(words) == len(set(words)), "关键词应去重"
    assert "新疆" in words
    assert sum(k["quota"] for k in kws) == 10
    # 自驾 既是旅行领域词也是自定义词，只保留一次（领域优先）
    assert next(k for k in kws if k["keyword"] == "自驾")["from"] == "domain:旅行"


def test_resolve_empty():
    assert domains.resolve_keywords([], "", 10) == []
    assert domains.resolve_keywords([], "  ", 10) == []


def test_unknown_domain_as_keyword():
    kws = domains.resolve_keywords(["某个冷门赛道"], "", 3)
    assert len(kws) == 1 and kws[0]["keyword"] == "某个冷门赛道"


def test_cover_key_stable():
    a = cover_cache.cover_key("xiaohongshu", "abc123")
    b = cover_cache.cover_key("xiaohongshu", "abc123")
    c = cover_cache.cover_key("douyin", "abc123")
    assert a == b and a != c and len(a) == 16


def test_cover_first_url():
    assert cover_cache._first_url(["http://a/1.jpg", "http://a/2.jpg"]) == "http://a/1.jpg"
    assert cover_cache._first_url([{"url_default": "http://x"}]) == "http://x"
    assert cover_cache._first_url("") == ""


def test_extract_json_variants():
    raw = '好的，结果如下：\n```json\n{"score": 88, "verdict": "test"}\n```\n以上'
    assert agent_cli.extract_json(raw) == {"score": 88, "verdict": "test"}
    assert agent_cli.extract_json('{"a": {"b": 1}}') == {"a": {"b": 1}}
    assert agent_cli.extract_json("没有 json") is None


def test_rule_item_fallback():
    item = {"platform": "xiaohongshu", "id": "x1", "title": "3 个技巧让你秒变博主？",
            "likes": 66000, "collects": 40000, "comments": 900, "shares": 100}
    a = analyzer.rule_analyze_item(item)
    assert a["engine"] == "rule"
    assert 0 < a["score"] <= 100
    assert a["remake"] and a["why_viral"]
    assert "赞" in a["verdict"] or "收藏" in a["verdict"]


def test_analysis_cache_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        old = analyzer.ANALYSIS_DIR
        analyzer.ANALYSIS_DIR = Path(td)
        try:
            item = {"platform": "xiaohongshu", "id": "cache-me", "title": "t",
                    "likes": 100, "collects": 10, "comments": 1}
            assert analyzer.load_item_analysis(item) is None
            analyzer.ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
            payload = {"platform": item["platform"], "item_id": item["id"],
                       "engine": "rule", "analysis": {"score": 1}}
            analyzer._analysis_file(item).write_text(json.dumps(payload), encoding="utf-8")
            got = analyzer.load_item_analysis(item)
            assert got["analysis"]["score"] == 1
            assert "xiaohongshu:cache-me" in analyzer.analyzed_ids()
        finally:
            analyzer.ANALYSIS_DIR = old


def test_pipeline_keywords_domain():
    kws = pipeline.build_keywords("domain", ["摄影"], "", 6, "xiaohongshu")
    assert kws and sum(k["quota"] for k in kws) == 6


def test_task_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        old = pipeline.TASK_DIR
        pipeline.TASK_DIR = Path(td)
        try:
            t = {"id": "t1", "status": "pending", "mode": "domain", "platform": "xiaohongshu",
                 "created_at": "x", "config": {}, "logs": [], "result": {}}
            pipeline.save_task(t)
            assert pipeline.load_task("t1")["status"] == "pending"
            assert pipeline.load_task("nope") is None
            recent = pipeline.recent_tasks()
            assert recent and recent[0]["id"] == "t1"
        finally:
            pipeline.TASK_DIR = old


def test_accounts_listing_has_no_cookie_payload():
    """账号列表只给元信息，绝不能把 cookie 内容吐给前端。"""
    import accounts
    rows = accounts.list_accounts("douyin") + accounts.list_accounts("xiaohongshu")
    for r in rows:
        assert set(r) >= {"id", "name", "file", "ok"}, r
        assert "cookies" not in r and "cookie" not in " ".join(r.keys()).lower().replace("cookie_count", ""), r


def test_resolve_auth_prefers_local_cookie_for_xhs():
    """小红书保留原有 cookie 链路；抖音必须走 storage_state（不扫码）。"""
    import accounts
    auth = pipeline.resolve_auth("xiaohongshu")
    assert auth["mode"] in ("cookie", "storage_state"), auth
    # 抖音：有账号则必须 storage_state，没账号则抛明确错误（而不是悄悄去扫码）
    try:
        dy = pipeline.resolve_auth("douyin")
        assert dy["mode"] == "storage_state", dy
        assert (dy.get("account") or {}).get("path") or dy["mode"] == "storage_state"
    except accounts.AccountError as e:
        assert "账号管理" in str(e), str(e)


def test_jsonl_increment_only_new_lines():
    """按行数基线只取新增记录，避免 MediaCrawler 按天追加导致重复入库。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pdir = root / "data" / "douyin"
        pdir.mkdir(parents=True)
        f = pdir / "search_contents_2026-11-17.jsonl"
        f.write_text('{"aweme_id":"a"}\n{"aweme_id":"b"}\n', encoding="utf-8")
        old_root = pipeline._capture.MC_ROOT
        try:
            pipeline._capture.MC_ROOT = root
            base = pipeline._jsonl_baseline("douyin")
            assert base[str(f)] == 2, base
            with open(f, "a", encoding="utf-8") as fh:
                fh.write('{"aweme_id":"c"}\n')
            new = pipeline._read_new_mc_records("douyin", base)
            assert [r["aweme_id"] for r in new] == ["c"], new
        finally:
            pipeline._capture.MC_ROOT = old_root


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    print("workbench selftest:")
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  [ok] {name}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {name}: {e}")
    print("ALL PASSED" if not failed else f"{failed} FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
