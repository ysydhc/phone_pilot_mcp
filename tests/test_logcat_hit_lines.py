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
