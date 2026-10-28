"""Tests for iOS script_api broadcast & ensure_app_alive."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.script_api.broadcast import (
    _pid_of,
    _pid_of_ios,
    ensure_app_alive,
    send_broadcast,
)


def _make_ios_ctx(serial: str = "mock_ios_serial") -> MagicMock:
    ctx = MagicMock()
    ctx.platform = "ios"
    ctx.device_serial = serial
    ctx.driver.platform = "ios"
    ctx.driver.device_serial = serial
    ctx.driver.shell.return_value = {"ok": False, "stdout": "", "stderr": "not_supported"}
    ctx.driver.ui.get_current_activity.return_value = {"ok": True, "package": "", "activity": ""}
    ctx.driver.app.force_stop.return_value = {"ok": True}
    ctx.driver.clear_log.return_value = {"ok": False, "error": "not_supported"}
    ctx.driver.logcat_time.return_value = "2026-10-28 17:00:00"
    ctx.driver.read_log.return_value = ""
    ctx.driver.open_deeplink.return_value = {"ok": True, "uri": "test://"}
    return ctx


class TestIOSPidOf(unittest.TestCase):
    """Test iOS process liveness detection."""

    def test_pid_of_ios_from_driver_shell(self):
        ctx = _make_ios_ctx()
        ctx.driver.shell.return_value = {
            "ok": True,
            "stdout": "PID TTY TIME CMD\n1234 ?? 0:01.00 com.example.iosapp\n",
        }
        pid = _pid_of(ctx, "com.example.iosapp")
        self.assertEqual(pid, 1234)

    @patch("phone_pilot.ios.system.execute_shell")
    def test_pid_of_ios_from_system_execute_shell(self, mock_exec):
        ctx = _make_ios_ctx()
        mock_exec.return_value = {
            "ok": True,
            "stdout": "UID   PID  PPID   C STIME TTY           TIME CMD\n501 56789     1   0 10:00 ??        0:00.50 /Applications/com.example.demo.app\n",
        }
        pid = _pid_of(ctx, "com.example.demo")
        self.assertEqual(pid, 56789)

    def test_pid_of_ios_from_current_activity_with_pid(self):
        ctx = _make_ios_ctx()
        ctx.driver.ui.get_current_activity.return_value = {
            "ok": True,
            "package": "com.example.activeapp",
            "pid": 9876,
        }
        pid = _pid_of(ctx, "com.example.activeapp")
        self.assertEqual(pid, 9876)

    def test_pid_of_ios_from_current_activity_without_pid(self):
        ctx = _make_ios_ctx()
        ctx.driver.ui.get_current_activity.return_value = {
            "ok": True,
            "package": "com.example.activeapp",
        }
        pid = _pid_of(ctx, "com.example.activeapp")
        self.assertEqual(pid, 1)

    def test_pid_of_ios_app_not_found(self):
        ctx = _make_ios_ctx()
        ctx.driver.ui.get_current_activity.return_value = {
            "ok": True,
            "package": "com.other.app",
        }
        pid = _pid_of(ctx, "com.example.activeapp")
        self.assertIsNone(pid)


class TestIOSSendBroadcast(unittest.TestCase):
    """Test send_broadcast behaviour on iOS."""

    def test_send_broadcast_standard_action_not_supported(self):
        ctx = _make_ios_ctx()
        res = send_broadcast(ctx, "com.example.ACTION")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "not_supported")
        self.assertEqual(res["reason"], "not_supported")

    def test_send_broadcast_deeplink_scheme_delegation(self):
        ctx = _make_ios_ctx()
        ctx.driver.open_deeplink.return_value = {"ok": True, "uri": "myapp://action?p=1"}
        res = send_broadcast(ctx, "myapp://action?p=1")
        self.assertTrue(res["ok"])
        ctx.driver.open_deeplink.assert_called_once_with("myapp://action?p=1")


class TestIOSEnsureAppAlive(unittest.TestCase):
    """Test ensure_app_alive on iOS."""

    def test_ensure_app_alive_already_running(self):
        ctx = _make_ios_ctx()
        ctx.driver.ui.get_current_activity.return_value = {
            "ok": True,
            "package": "com.example.myapp",
            "pid": 1234,
        }
        res = ensure_app_alive(ctx, "com.example.myapp", scheme_url="myapp://start")
        self.assertTrue(res["ok"])
        self.assertEqual(res["pid"], 1234)
        self.assertFalse(res["launched"])
        self.assertIsNone(res["ready_seen"])
        ctx.driver.open_deeplink.assert_not_called()

    def test_ensure_app_alive_dead_no_scheme(self):
        ctx = _make_ios_ctx()
        res = ensure_app_alive(ctx, "com.example.myapp")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "dead_no_launch_method")

    def test_ensure_app_alive_dead_cold_launch_with_ready_event(self):
        ctx = _make_ios_ctx()
        # Initially dead, then alive after launch
        ctx.driver.ui.get_current_activity.side_effect = [
            {"ok": True, "package": "com.other.app"},
            {"ok": True, "package": "com.example.myapp", "pid": 4321},
        ]
        ctx.driver.read_log.return_value = "2026-10-28 17:00:01 App[4321] event=APP_READY initialized\n"

        res = ensure_app_alive(
            ctx,
            "com.example.myapp",
            scheme_url="myapp://launch",
            ready_event="event=APP_READY",
            timeout_s=2,
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["launched"])
        self.assertTrue(res["ready_seen"])
        ctx.driver.open_deeplink.assert_called_once_with("myapp://launch")

    def test_ensure_app_alive_force_restart(self):
        ctx = _make_ios_ctx()
        ctx.driver.ui.get_current_activity.side_effect = [
            {"ok": True, "package": "com.example.myapp", "pid": 1111},
            {"ok": True, "package": "com.example.myapp", "pid": 2222},
        ]
        res = ensure_app_alive(
            ctx,
            "com.example.myapp",
            scheme_url="myapp://restart",
            force_restart=True,
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["launched"])
        ctx.driver.app.force_stop.assert_called_once_with("com.example.myapp")
        ctx.driver.clear_log.assert_called_once()
        ctx.driver.open_deeplink.assert_called_once_with("myapp://restart")


if __name__ == "__main__":
    unittest.main()
