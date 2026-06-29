"""Tests for script_api action UI cache invalidation.

验证动作类 script_api 函数执行成功后失效 ui_snapshot_cache，
避免后续读取拿到旧的 UI 层级。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phone_pilot.core import ui_snapshot_cache
from phone_pilot.script_api.actions import (
    tap_xy,
    swipe,
    type_text,
    keyevent,
    restart_app_pkg,
    reset_home_screen,
    clear_background,
)


def _make_ctx(serial: str = "test_inv_serial", platform: str = "android"):
    ctx = MagicMock()
    ctx.device_serial = serial
    ctx.platform = platform
    ctx.driver.platform = platform
    ctx.driver.device_serial = serial
    ctx.screen_size = (1080, 2400)
    # Action functions check getattr(ctx, "auto_screenshot", False) etc.
    ctx.auto_screenshot = False
    ctx.auto_log = False
    ctx.session = None
    ctx.out_dir = None
    return ctx


@pytest.fixture(autouse=True)
def _clean_cache():
    ui_snapshot_cache.invalidate()
    yield
    ui_snapshot_cache.invalidate()


def test_tap_xy_invalidates_cache():
    ctx = _make_ctx()
    ctx.driver.input.tap.return_value = {"ok": True}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        tap_xy(ctx, 100, 200)

    mock_cache.invalidate_device_ui.assert_called_once_with(
        "test_inv_serial", platform="android"
    )


def test_swipe_invalidates_cache():
    ctx = _make_ctx()
    ctx.driver.input.swipe.return_value = {"ok": True}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        swipe(ctx, x1=100, y1=800, x2=100, y2=200)

    mock_cache.invalidate_device_ui.assert_called_once_with(
        "test_inv_serial", platform="android"
    )


def test_type_text_invalidates_cache():
    ctx = _make_ctx()
    ctx.driver.input.input_text.return_value = {"ok": True}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        type_text(ctx, "hello")

    mock_cache.invalidate_device_ui.assert_called_once_with(
        "test_inv_serial", platform="android"
    )


def test_keyevent_invalidates_cache():
    ctx = _make_ctx()
    ctx.driver.input.keyevent.return_value = {"ok": True}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        keyevent(ctx, "KEYCODE_BACK")

    mock_cache.invalidate_device_ui.assert_called_once_with(
        "test_inv_serial", platform="android"
    )


def test_restart_app_pkg_invalidates_cache():
    ctx = _make_ctx()
    ctx.driver.app.force_stop.return_value = {"ok": True}
    ctx.driver.app.launch_app.return_value = {"ok": True}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        restart_app_pkg(ctx, "com.example.app")

    mock_cache.invalidate_device_ui.assert_called_once_with(
        "test_inv_serial", platform="android"
    )


def test_reset_home_screen_invalidates_cache():
    ctx = _make_ctx()
    ctx.driver.go_home.return_value = {"ok": True}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        reset_home_screen(ctx)

    mock_cache.invalidate_device_ui.assert_called_with(
        "test_inv_serial", platform="android"
    )


def test_clear_background_invalidates_cache():
    ctx = _make_ctx()
    ctx.driver.clear_background.return_value = {"ok": True}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        clear_background(ctx)

    mock_cache.invalidate_device_ui.assert_called_once_with(
        "test_inv_serial", platform="android"
    )


def test_failed_tap_does_not_invalidate():
    ctx = _make_ctx()
    ctx.driver.input.tap.return_value = {"ok": False, "error": "device_offline"}

    with patch(
        "phone_pilot.script_api.actions.ui_snapshot_cache"
    ) as mock_cache:
        tap_xy(ctx, 100, 200)

    mock_cache.invalidate_device_ui.assert_not_called()
