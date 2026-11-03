"""Unit tests for go_recents across Android, HarmonyOS, and iOS drivers and MCP tool."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from phone_pilot.android.driver import AndroidDriver
from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.ios.driver import iOSDriver
from phone_pilot.mcp._nav_tools import phone_go_recents


def test_android_driver_go_recents():
    driver = AndroidDriver("serial123")
    driver._input = MagicMock()
    driver._input.keyevent.return_value = {"ok": True, "key": "KEYCODE_APP_SWITCH"}

    res = driver.go_recents()
    assert res["ok"] is True
    driver._input.keyevent.assert_called_with("KEYCODE_APP_SWITCH")


def test_harmony_driver_go_recents_keyevent():
    driver = HarmonyDriver("harmony123")
    driver._input = MagicMock()
    driver._input.keyevent.return_value = {"ok": True, "key": "KEYCODE_APP_SWITCH"}

    res = driver.go_recents()
    assert res["ok"] is True
    driver._input.keyevent.assert_called_with("KEYCODE_APP_SWITCH")


def test_harmony_driver_go_recents_gesture_fallback():
    driver = HarmonyDriver("harmony123")
    driver._input = MagicMock()
    driver._input.keyevent.side_effect = RuntimeError("no keyevent")
    driver._screen = MagicMock()
    driver._screen.get_screen_size.return_value = (1080, 2400)
    driver._input.swipe.return_value = {"ok": True}

    res = driver.go_recents()
    assert res["ok"] is True
    assert res["engine"] == "gesture"
    driver._input.swipe.assert_called_with(540, 2390, 540, 1440, duration_ms=500)


def test_ios_driver_go_recents():
    driver = iOSDriver("ios-udid-123")
    driver._screen = MagicMock()
    driver._screen.get_screen_size.return_value = (1170, 2532)

    mock_client = MagicMock()
    with patch("phone_pilot.ios.driver._wda_call", side_effect=lambda serial, fn: fn(mock_client)):
        res = driver.go_recents()
        assert res["ok"] is True
        assert res["engine"] == "wda_gesture"
        mock_client.swipe.assert_called_with(585, 2522, 585, 1519, 0.5)


@pytest.mark.asyncio
async def test_mcp_phone_go_recents():
    mock_driver = MagicMock()
    mock_driver.go_recents.return_value = {"ok": True, "engine": "test"}

    def fake_with_device(fn, serial, plat):
        return fn(serial, plat)

    with patch("phone_pilot.mcp._nav_tools.get_driver", return_value=mock_driver), \
         patch("phone_pilot.mcp._nav_tools._with_device", side_effect=fake_with_device):
        res = await phone_go_recents("s1", platform="android")
        assert res["ok"] is True
        mock_driver.go_recents.assert_called_once()
