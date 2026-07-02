"""Tests for go_back robust handling (fullscreen ads, type errors)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phone_pilot.android.driver import AndroidDriver


def test_go_back_success():
    """go_back succeeds normally."""
    driver = AndroidDriver("fake_serial")
    with patch.object(
        driver._input, "keyevent", return_value={"ok": True, "returncode": 0, "key": "KEYCODE_BACK"}
    ):
        result = driver.go_back()

    assert result["ok"] is True


def test_go_back_fallback_on_failure():
    """go_back falls back to keyevent(4) when KEYCODE_BACK fails."""
    driver = AndroidDriver("fake_serial")
    with patch.object(
        driver._input,
        "keyevent",
        side_effect=[
            {"ok": False, "returncode": 1, "key": "KEYCODE_BACK", "stderr": "error"},
            {"ok": True, "returncode": 0, "key": "4"},
        ],
    ) as mock_keyevent:
        result = driver.go_back()

    assert result["ok"] is True
    # Verify fallback was called with numeric code
    assert mock_keyevent.call_count == 2
    assert mock_keyevent.call_args_list[0][0][0] == "KEYCODE_BACK"
    assert mock_keyevent.call_args_list[1][0][0] == "4"


def test_go_back_catches_exception():
    """go_back catches exception and tries fallback."""
    driver = AndroidDriver("fake_serial")
    with patch.object(
        driver._input,
        "keyevent",
        side_effect=[
            TypeError("unexpected type"),
            {"ok": True, "returncode": 0, "key": "4"},
        ],
    ):
        result = driver.go_back()

    assert result["ok"] is True


def test_go_back_returns_failure_when_both_fail():
    """go_back returns failure when both KEYCODE_BACK and fallback fail."""
    driver = AndroidDriver("fake_serial")
    with patch.object(
        driver._input,
        "keyevent",
        side_effect=[
            {"ok": False, "returncode": 1, "key": "KEYCODE_BACK"},
            {"ok": False, "returncode": 1, "key": "4"},
        ],
    ):
        result = driver.go_back()

    assert result["ok"] is False
