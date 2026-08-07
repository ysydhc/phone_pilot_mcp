"""safe_tap 的 escalate_to 升级参数。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phone_pilot.script_api import safe_tap
from phone_pilot.script_api.context import ScriptContext


def _ctx():
    ctx = MagicMock(spec=ScriptContext)
    ctx.driver.go_back.return_value = None
    return ctx


def test_escalate_none_default_behavior_unchanged():
    ctx = _ctx()
    with patch(
        "phone_pilot.script_api.actions._current_focus",
        side_effect=[
            {"package": "com.app", "activity": ".Main"},
            {"package": "com.app", "activity": ".Main"},
        ],
    ), patch("phone_pilot.script_api.actions._input_tap"):
        result = safe_tap(ctx, 100, 200)
    assert result["ok"] is True
    assert "escalated" not in result or result["escalated"] is False


def test_escalate_restart_app_triggers_after_max_recover_exhausted():
    ctx = _ctx()
    with patch(
        "phone_pilot.script_api.actions._current_focus",
        side_effect=[
            {"package": "com.app", "activity": ".Main"},  # before
            {"package": "com.vending", "activity": ".Store"},  # after tap (mistap)
            {"package": "com.vending", "activity": ".Store"},  # after go_back (still wrong)
            {"package": "com.app", "activity": ".Main"},  # after restart_app_pkg
        ],
    ), patch("phone_pilot.script_api.actions._input_tap"), patch(
        "phone_pilot.script_api.actions.restart_app_pkg",
        return_value={"ok": True},
    ) as mock_restart:
        result = safe_tap(
            ctx, 100, 200, expect_package="com.app",
            escalate_to="restart_app", max_recover=1, settle_s=0,
        )
    mock_restart.assert_called_once_with(ctx, "com.app")
    assert result["escalated"] is True
    assert result["state_lost"] is True
    assert result["recovered"] is True
    assert result["escalate_action"] == "restart_app"


def test_escalate_not_triggered_if_recovered_on_first_try():
    ctx = _ctx()
    with patch(
        "phone_pilot.script_api.actions._current_focus",
        side_effect=[
            {"package": "com.app", "activity": ".Main"},
            {"package": "com.vending", "activity": ".Store"},
            {"package": "com.app", "activity": ".Main"},  # recovered after go_back
        ],
    ), patch("phone_pilot.script_api.actions._input_tap"), patch(
        "phone_pilot.script_api.actions.restart_app_pkg"
    ) as mock_restart:
        result = safe_tap(
            ctx, 100, 200, expect_package="com.app",
            escalate_to="restart_app", max_recover=1, settle_s=0,
        )
    mock_restart.assert_not_called()
    assert result["escalated"] is False
    assert result["state_lost"] is False


def test_focus_unavailable_treated_conservatively():
    """_current_focus 因设备无响应返回空值时，不应被误判为"已恢复"。"""
    ctx = _ctx()
    with patch(
        "phone_pilot.script_api.actions._current_focus",
        side_effect=[
            {"package": "com.app", "activity": ".Main"},
            {"package": "", "activity": ""},  # 设备瞬时无响应
            {"package": "", "activity": ""},
            {"package": "", "activity": ""},  # after restart_app_pkg, 仍无响应
        ],
    ), patch("phone_pilot.script_api.actions._input_tap"), patch(
        "phone_pilot.script_api.actions.restart_app_pkg",
        return_value={"ok": True},
    ):
        result = safe_tap(
            ctx, 100, 200, expect_package="com.app",
            escalate_to="restart_app", max_recover=1, settle_s=0,
        )
    assert result.get("focus_unavailable") is True
