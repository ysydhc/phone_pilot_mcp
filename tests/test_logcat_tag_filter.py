"""Tests for logcat tag filtering.

验证 logcat_find / logcat_wait_for / logcat_mark / logcat_since 的 tag 参数。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phone_pilot.script_api.logcat import (
    logcat_find,
    logcat_wait_for,
    logcat_mark,
    logcat_since,
)
from phone_pilot.script_api.context import ScriptContext


def _make_ctx() -> ScriptContext:
    ctx = MagicMock(spec=ScriptContext)
    ctx.auto_screenshot = False
    ctx._observe = MagicMock()
    return ctx


def test_logcat_find_accepts_tag_param():
    """logcat_find 接收 tag 参数并透传给 read_log。"""
    ctx = _make_ctx()
    ctx.driver.read_log.return_value = "02-09 10:00:00.000 1000 1000 D TPAdapterTest: loadCustomAd"

    result = logcat_find(ctx, "loadCustomAd", tag="TPAdapterTest")

    assert result["ok"] is True
    assert result["count"] == 1
    ctx.driver.read_log.assert_called_once()
    args = ctx.driver.read_log.call_args
    assert args[1].get("tag_filter") == "TPAdapterTest"


def test_logcat_find_no_tag_backward_compat():
    """logcat_find 不传 tag 时保持向后兼容。"""
    ctx = _make_ctx()
    ctx.driver.read_log.return_value = "02-09 10:00:00.000 1000 1000 D SomeTag: loadCustomAd"

    result = logcat_find(ctx, "loadCustomAd")

    assert result["ok"] is True
    ctx.driver.read_log.assert_called_once()
    args = ctx.driver.read_log.call_args
    assert args[1].get("tag_filter") is None


def test_logcat_wait_for_accepts_tag_param():
    """logcat_wait_for 接收 tag 参数并透传给 read_log。"""
    ctx = _make_ctx()
    ctx.driver.read_log.return_value = "02-09 10:00:00.000 1000 1000 D TPAdapterTest: loadCustomAd"

    with patch("phone_pilot.script_api.logcat.time.sleep"):
        result = logcat_wait_for(ctx, "loadCustomAd", tag="TPAdapterTest", timeout_s=1, interval_s=0.1)

    assert result["ok"] is True
    # 应该在第一次调用 read_log 时就命中
    ctx.driver.read_log.assert_called()
    # 检查某次调用有 tag_filter
    tag_calls = [c for c in ctx.driver.read_log.call_args_list if c[1].get("tag_filter") == "TPAdapterTest"]
    assert len(tag_calls) > 0


def test_logcat_mark_accepts_tag_param():
    """logcat_mark 接收 tag 参数，返回该 tag 最新日志的时间戳。"""
    ctx = _make_ctx()
    ctx.driver.read_log.return_value = "02-09 10:00:00.000 1000 1000 D TPAdapterTest: some log"
    ctx.driver.logcat_time.return_value = "02-09 10:00:00.000"

    mark = logcat_mark(ctx, tag="TPAdapterTest")

    assert mark is not None
    assert mark == "02-09 10:00:00.000"


def test_logcat_since_accepts_tag_param():
    """logcat_since 接收 tag 参数并透传给 read_log。"""
    ctx = _make_ctx()
    ctx.driver.read_log.return_value = "02-09 10:00:01.000 1000 1000 D TPAdapterTest: after mark"

    result = logcat_since(ctx, "02-09 10:00:00.000", pattern="after mark", tag="TPAdapterTest")

    assert result["ok"] is True
    assert result["count"] == 1
    ctx.driver.read_log.assert_called_once()
    args = ctx.driver.read_log.call_args
    assert args[1].get("tag_filter") == "TPAdapterTest"
    assert args[1].get("since") == "02-09 10:00:00.000"
