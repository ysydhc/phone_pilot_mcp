"""
Tests for the MCP recording flow on iOS (host-side recording, no device pull).
Verifies _phone_start_recording_sync / _phone_stop_recording_sync route iOS to
a direct host-path recording without pull_file/remove_remote_file.
"""

from unittest.mock import MagicMock, patch

from phone_pilot.mcp import _recording_tools as rt
from phone_pilot.mcp import _state


SERIAL = "00008030-001234560A80802E"


def _reset_state():
    with _state._state_lock:
        _state._recording_state.clear()
        _state._recording_driver_cache.clear()
        _state._recording_status.clear()


def test_ios_recording_is_host_side_no_pull(tmp_path):
    _reset_state()
    driver = MagicMock()
    driver.platform = "ios"
    driver.screen.start_screenrecord.return_value = {"ok": True, "engine": "simctl"}
    driver.screen.stop_screenrecord.return_value = {"ok": True}

    with patch.object(rt, "get_driver", return_value=driver):
        start = rt._phone_start_recording_sync(SERIAL, "rec", str(tmp_path), "ios")
        assert start["ok"] is True
        # start_screenrecord should be called with the LOCAL host path (.mp4 under tmp)
        called_path = driver.screen.start_screenrecord.call_args.kwargs.get("path")
        assert called_path.startswith(str(tmp_path))
        assert called_path.endswith(".mp4")

        stop = rt._phone_stop_recording_sync(SERIAL, "ios")

    assert stop["ok"] is True
    assert stop["local_path"].startswith(str(tmp_path))
    # iOS host-side flow must NOT pull or remove from device
    driver.pull_file.assert_not_called()
    driver.remove_remote_file.assert_not_called()
    _reset_state()


def test_android_recording_still_pulls(tmp_path):
    _reset_state()
    driver = MagicMock()
    driver.platform = "android"
    driver.screen.start_screenrecord.return_value = {
        "ok": True,
        "remote_path": "/sdcard/phone_pilot_rec_1.mp4",
    }
    driver.screen.stop_screenrecord.return_value = {"ok": True}

    with (
        patch.object(rt, "get_driver", return_value=driver),
        patch("phone_pilot.android.adb.utils.ensure_adb_env", lambda: None),
    ):
        start = rt._phone_start_recording_sync(SERIAL, "rec", str(tmp_path), "android")
        assert start["ok"] is True
        stop = rt._phone_stop_recording_sync(SERIAL, "android")

    assert stop["ok"] is True
    # Android device-side flow pulls then removes
    driver.pull_file.assert_called_once()
    driver.remove_remote_file.assert_called_once()
    _reset_state()
