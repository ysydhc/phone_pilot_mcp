"""logcat_wait_for 新增 hit_lines：与 logcat_since.hits 同构的扁平列表。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phone_pilot.script_api import logcat_wait_for
from phone_pilot.script_api.context import ScriptContext


def _ctx_with_log(lines: list[str]):
    ctx = MagicMock(spec=ScriptContext)
    ctx.driver.read_log.return_value = "\n".join(lines)
    return ctx


def test_hit_lines_flattens_hits_dict():
    ctx = _ctx_with_log(["ad load success", "unrelated line"])
    with patch("phone_pilot.script_api.logcat.time.sleep"):
        result = logcat_wait_for(ctx, "load", "success", timeout_s=1, interval_s=0.1)
    assert result["ok"] is True
    assert "ad load success" in result["hit_lines"]


def test_hit_lines_allows_duplicate_when_line_matches_multiple_patterns():
    """设计决定：同一行同时匹配多个 pattern 时应重复出现，不去重。"""
    ctx = _ctx_with_log(["ad load success"])
    with patch("phone_pilot.script_api.logcat.time.sleep"):
        result = logcat_wait_for(ctx, "load", "success", timeout_s=1, interval_s=0.1)
    assert result["hit_lines"].count("ad load success") == 2


def test_hits_field_unchanged_structure():
    """回归：hits 字段本身结构不变（仍是 dict[pattern, list[str]]）。"""
    ctx = _ctx_with_log(["pattern1 hit"])
    with patch("phone_pilot.script_api.logcat.time.sleep"):
        result = logcat_wait_for(ctx, "pattern1", timeout_s=1, interval_s=0.1)
    assert isinstance(result["hits"], dict)
    assert result["hits"]["pattern1"] == ["pattern1 hit"]


def test_multiline_same_prefix_preserves_first_line_and_all_lines():
    """同时间戳多行同前缀日志：line 默认取首条（含 URL），lines 保留全部正序匹配行，支持 match_mode 切换。"""
    url_line = "11-10 14:30:00.123 100 200 I SDK: [FunnelWithoutInstance] lifecycle_stage url: https://ad.example.com/v1"
    fail_line = "11-10 14:30:00.123 100 200 E SDK: [FunnelWithoutInstance] lifecycle_stage report failed, code:0"
    ctx = _ctx_with_log(["11-10 14:29:59.000 100 200 D SDK: boot", url_line, fail_line])

    with patch("phone_pilot.script_api.logcat.time.sleep"):
        res_default = logcat_wait_for(
            ctx, "[FunnelWithoutInstance]", timeout_s=1, interval_s=0.1
        )
        res_last = logcat_wait_for(
            ctx,
            "[FunnelWithoutInstance]",
            timeout_s=1,
            interval_s=0.1,
            match_mode="last",
        )
        res_all = logcat_wait_for(
            ctx,
            "[FunnelWithoutInstance]",
            timeout_s=1,
            interval_s=0.1,
            match_mode="all",
        )

    assert res_default["ok"] is True
    assert res_default["line"] == url_line
    assert res_default["lines"] == [url_line, fail_line]
    assert res_default["match_mode"] == "first"

    assert res_last["ok"] is True
    assert res_last["line"] == fail_line
    assert res_last["lines"] == [url_line, fail_line]
    assert res_last["match_mode"] == "last"

    assert res_all["ok"] is True
    assert res_all["line"] == url_line
    assert res_all["lines"] == [url_line, fail_line]
    assert res_all["match_mode"] == "all"
