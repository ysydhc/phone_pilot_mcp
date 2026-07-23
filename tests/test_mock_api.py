"""Tests for mock_delete_rule and mock_switch_rule public API."""

from __future__ import annotations

from phone_pilot.script_api.mock import mock_delete_rule, mock_switch_rule
from phone_pilot.script_api.context import ScriptContext


def _make_ctx() -> ScriptContext:
    ctx = ScriptContext(device_serial="fake", platform="android")
    return ctx


def test_mock_delete_rule_not_running():
    """mock_delete_rule returns error when server not running."""
    ctx = _make_ctx()
    result = mock_delete_rule(ctx, "GET:/api/test")
    assert result["ok"] is False
    assert "not_running" in result["error"]


def test_mock_switch_rule_not_running():
    """mock_switch_rule returns error when server not running."""
    ctx = _make_ctx()
    result = mock_switch_rule(ctx, "GET", "/api/test", {"data": 1})
    assert result["ok"] is False
    assert "not_running" in result["error"]
