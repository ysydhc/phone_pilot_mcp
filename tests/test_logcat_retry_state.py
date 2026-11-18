"""MCP adapter regression: failed log capture stops remain registered for retry."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

# Import server first so its circular tool-registration imports are initialized.
from phone_pilot.mcp import server as _server  # noqa: F401
from phone_pilot.mcp import _logcat_tools as tools


@pytest.fixture
def adapter(monkeypatch, tmp_path):
    serial = "offline-retry-device"
    path = str(tmp_path / "capture.log")
    driver = Mock()
    monkeypatch.setattr(
        tools, "_resolve_device_serial", lambda *args: (serial, "android", None)
    )
    monkeypatch.setattr(tools, "get_driver", lambda *args: driver)
    with tools._state_lock:
        previous = tools._logcat_state.get(serial)
        tools._logcat_state[serial] = path
    yield SimpleNamespace(serial=serial, path=path, driver=driver)
    with tools._state_lock:
        if previous is None:
            tools._logcat_state.pop(serial, None)
        else:
            tools._logcat_state[serial] = previous


def registered(serial):
    with tools._state_lock:
        return tools._logcat_state.get(serial)


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("failure", ["envelope", "exception"])
@pytest.mark.parametrize("async_adapter", [False, True])
def test_failed_stop_blocks_restart_and_allows_implicit_retry(
    adapter, explicit, failure, async_adapter
):
    path = adapter.path if explicit else ""

    def first_stop(stopped_path):
        assert stopped_path == adapter.path
        # Registration is present throughout the underlying stop, not just restored.
        assert registered(adapter.serial) == adapter.path
        if failure == "exception":
            raise OSError("cannot stop capture")
        return {"ok": False, "error": "cannot stop capture"}

    adapter.driver.stop_log_capture.side_effect = first_stop
    if async_adapter:
        result = asyncio.run(tools.phone_stop_logcat(adapter.serial, path, "android"))
    else:
        result = tools._phone_stop_logcat_sync(adapter.serial, path, "android")
    assert result == {
        "ok": False, "error": "cannot stop capture",
        "path": adapter.path, "device_serial": adapter.serial,
    }
    assert registered(adapter.serial) == adapter.path
    start = tools._phone_start_logcat_sync(
        adapter.serial, "another.log", "", "", "", "", ".", "android"
    )
    assert start["error"] == "already_capturing"
    adapter.driver.start_log_capture.assert_not_called()
    adapter.driver.stop_log_capture.side_effect = None
    adapter.driver.stop_log_capture.return_value = {"ok": True, "lines": 1}
    retry = tools._phone_stop_logcat_sync(adapter.serial, "", "android")
    assert retry["ok"]
    assert retry["path"] == adapter.path
    assert registered(adapter.serial) is None
    assert adapter.driver.stop_log_capture.call_count == 2


@pytest.mark.parametrize("stop_ok", [False, True])
def test_explicit_other_path_does_not_unregister_active_capture(adapter, stop_ok):
    adapter.driver.stop_log_capture.return_value = {"ok": stop_ok}
    result = tools._phone_stop_logcat_sync(adapter.serial, "other.log", "android")
    assert result["ok"] is stop_ok
    assert result["path"] == "other.log"
    assert registered(adapter.serial) == adapter.path


@pytest.mark.parametrize("terminal,stopped", [(True, True), (True, False), (False, True)])
def test_only_confirmed_terminal_failure_allows_restart(adapter, terminal, stopped):
    from pathlib import Path

    Path(adapter.path).write_text("retained raw evidence\n")
    adapter.driver.stop_log_capture.return_value = {
        "ok": False, "error": "filter drain failed", "terminal": terminal, "stopped": stopped,
    }
    result = asyncio.run(tools.phone_stop_logcat(adapter.serial, "", "android"))
    assert not result["ok"]
    assert result["path"] == adapter.path
    adapter.driver.start_log_capture.return_value = {"ok": True, "pid": 123}
    restart = tools._phone_start_logcat_sync(
        adapter.serial, "replacement.log", "", "", "", "", ".", "android"
    )
    if terminal and stopped:
        assert restart["ok"]
        adapter.driver.start_log_capture.assert_called_once()
    else:
        assert restart["error"] == "already_capturing"
        adapter.driver.start_log_capture.assert_not_called()
    assert Path(adapter.path).read_text() == "retained raw evidence\n"


def test_success_does_not_remove_replaced_registration(adapter):
    def stop_and_replace(path):
        with tools._state_lock:
            tools._logcat_state[adapter.serial] = "replacement.log"
        return {"ok": True}

    adapter.driver.stop_log_capture.side_effect = stop_and_replace
    assert tools._phone_stop_logcat_sync(adapter.serial, "", "android")["ok"]
    assert registered(adapter.serial) == "replacement.log"
