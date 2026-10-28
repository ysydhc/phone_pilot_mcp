#!/usr/bin/env python3
"""
Unit tests for HarmonyOS system tools (Task 3.3).
Tests for set_wifi_enabled, set_airplane_mode, get_notifications in utils, driver, and MCP tools.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.harmony.device import utils as h_utils
from phone_pilot.harmony.driver import HarmonyDriver

# Ensure MCP server is safely mocked if mcp package has version mismatch
if "phone_pilot.mcp.server" not in sys.modules:
    mock_srv = MagicMock()
    mock_srv.mcp.tool = lambda *a, **k: (lambda fn: fn)
    sys.modules["phone_pilot.mcp.server"] = mock_srv

from phone_pilot.mcp import _system_tools as mcp_system_tools


def _mock_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["mock"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestHarmonyWifiControl(unittest.TestCase):
    """Test set_wifi_enabled utility."""

    SERIAL = "harmony-serial-001"

    def test_empty_serial(self) -> None:
        res = h_utils.set_wifi_enabled("", True)
        self.assertFalse(res["ok"])
        self.assertIn("device_serial", res["error"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_enable_via_wifi_tool_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _mock_proc(stdout="enable wifi success\n", returncode=0)
        res = h_utils.set_wifi_enabled(self.SERIAL, True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["wifi_on"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_disable_via_wifi_tool_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _mock_proc(stdout="disable wifi success\n", returncode=0)
        res = h_utils.set_wifi_enabled(self.SERIAL, False)
        self.assertTrue(res["ok"])
        self.assertFalse(res["wifi_on"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_fallback_to_param_settings_when_wifi_tool_fails(
        self, mock_run: MagicMock
    ) -> None:
        # 1st call (wifi_tool) fails, 2nd call (param set) succeeds, 3rd call (settings put) succeeds
        mock_run.side_effect = [
            _mock_proc(stdout="", stderr="wifi_tool: not found", returncode=127),
            _mock_proc(stdout="Set persist.wifi.state successfully\n", returncode=0),
            _mock_proc(stdout="", returncode=0),
        ]
        res = h_utils.set_wifi_enabled(self.SERIAL, True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["wifi_on"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_failure_when_both_fail(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _mock_proc(stdout="", stderr="wifi_tool failed", returncode=1),
            _mock_proc(stdout="", stderr="param set failed", returncode=1),
            _mock_proc(stdout="", stderr="settings put failed", returncode=1),
        ]
        res = h_utils.set_wifi_enabled(self.SERIAL, True)
        self.assertFalse(res["ok"])
        self.assertIn("error", res)


class TestHarmonyAirplaneControl(unittest.TestCase):
    """Test set_airplane_mode utility."""

    SERIAL = "harmony-serial-001"

    def test_empty_serial(self) -> None:
        res = h_utils.set_airplane_mode("", True)
        self.assertFalse(res["ok"])
        self.assertIn("device_serial", res["error"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_enable_airplane_mode_success(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _mock_proc(stdout="Set persist.radio.airplane_mode_on successfully\n", returncode=0),
            _mock_proc(stdout="", returncode=0),
            _mock_proc(stdout="Send event usual.event.AIRPLANE_MODE_CHANGED success\n", returncode=0),
        ]
        res = h_utils.set_airplane_mode(self.SERIAL, True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["airplane_on"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_disable_airplane_mode_success(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _mock_proc(stdout="Set persist.radio.airplane_mode_on successfully\n", returncode=0),
            _mock_proc(stdout="", returncode=0),
            _mock_proc(stdout="Send event usual.event.AIRPLANE_MODE_CHANGED success\n", returncode=0),
        ]
        res = h_utils.set_airplane_mode(self.SERIAL, False)
        self.assertTrue(res["ok"])
        self.assertFalse(res["airplane_on"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_partial_success_still_ok(self, mock_run: MagicMock) -> None:
        # param set fails, but settings put succeeds
        mock_run.side_effect = [
            _mock_proc(stdout="", stderr="permission denied", returncode=1),
            _mock_proc(stdout="", returncode=0),
            _mock_proc(stdout="", returncode=0),
        ]
        res = h_utils.set_airplane_mode(self.SERIAL, True)
        self.assertTrue(res["ok"])
        self.assertTrue(res["airplane_on"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_all_fail_returns_error(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _mock_proc(stdout="", stderr="param set error", returncode=1),
            _mock_proc(stdout="", stderr="settings error", returncode=1),
            _mock_proc(stdout="", stderr="ces error", returncode=1),
        ]
        res = h_utils.set_airplane_mode(self.SERIAL, True)
        self.assertFalse(res["ok"])


class TestHarmonyNotifications(unittest.TestCase):
    """Test get_notifications parsing and query."""

    SERIAL = "harmony-serial-001"

    def test_empty_serial(self) -> None:
        res = h_utils.get_notifications("")
        self.assertFalse(res["ok"])
        self.assertIn("device_serial", res["error"])

    def test_parse_json_format(self) -> None:
        raw = """
        [
            {
                "bundleName": "com.example.chat",
                "title": "Alice",
                "text": "Hello there!",
                "id": 101
            },
            {
                "bundleName": "com.example.news",
                "title": "Breaking News",
                "text": "OpenHarmony update released",
                "id": 102
            }
        ]
        """
        notifs = h_utils._parse_harmony_notifications(raw)
        self.assertEqual(len(notifs), 2)
        self.assertEqual(notifs[0]["package"], "com.example.chat")
        self.assertEqual(notifs[0]["title"], "Alice")
        self.assertEqual(notifs[0]["text"], "Hello there!")
        self.assertEqual(notifs[1]["package"], "com.example.news")

    def test_parse_record_blocks(self) -> None:
        raw = """
        NotificationRecord:
          bundleName: com.ohos.systemui
          title: Battery Warning
          text: Battery level is 15%
          id: 1
        -------------------------
        NotificationRequest:
          bundleName: com.example.email
          title: Work Mail
          text: Meeting at 3pm
          id: 2
        """
        notifs = h_utils._parse_harmony_notifications(raw)
        self.assertEqual(len(notifs), 2)
        self.assertEqual(notifs[0]["package"], "com.ohos.systemui")
        self.assertEqual(notifs[0]["title"], "Battery Warning")
        self.assertEqual(notifs[0]["text"], "Battery level is 15%")
        self.assertEqual(notifs[1]["package"], "com.example.email")
        self.assertEqual(notifs[1]["title"], "Work Mail")
        self.assertEqual(notifs[1]["text"], "Meeting at 3pm")

    def test_parse_dashed_blocks_with_equals(self) -> None:
        raw = """
        ----------------------------------------
        bundleName = com.example.music
        contentTitle = Now Playing
        contentText = Song Title - Artist
        id = 42
        ----------------------------------------
        """
        notifs = h_utils._parse_harmony_notifications(raw)
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0]["package"], "com.example.music")
        self.assertEqual(notifs[0]["title"], "Now Playing")
        self.assertEqual(notifs[0]["text"], "Song Title - Artist")

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_get_notifications_service_fallback(self, mock_run: MagicMock) -> None:
        # 1st service fails, 2nd succeeds with output
        mock_run.side_effect = [
            _mock_proc(stdout="", stderr="Can't find service", returncode=1),
            _mock_proc(
                stdout="""
                NotificationRecord:
                  bundleName: com.example.shop
                  title: Flash Sale
                  text: 50% discount on shoes
                """,
                returncode=0,
            ),
        ]
        res = h_utils.get_notifications(self.SERIAL)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["notifications"]), 1)
        self.assertEqual(res["notifications"][0]["package"], "com.example.shop")
        self.assertEqual(res["notifications"][0]["title"], "Flash Sale")


class TestHarmonyDriverSystemMethods(unittest.TestCase):
    """Test get_notifications, set_wifi_enabled, set_airplane_mode in HarmonyDriver."""

    SERIAL = "harmony-serial-001"

    @patch("phone_pilot.harmony.device.utils.set_wifi_enabled")
    def test_driver_set_wifi(self, mock_wifi: MagicMock) -> None:
        mock_wifi.return_value = {"ok": True, "wifi_on": True}
        driver = HarmonyDriver(self.SERIAL)
        res = driver.set_wifi_enabled(True)
        self.assertTrue(res["ok"])
        mock_wifi.assert_called_once_with(self.SERIAL, True)

    @patch("phone_pilot.harmony.device.utils.set_airplane_mode")
    def test_driver_set_airplane(self, mock_airplane: MagicMock) -> None:
        mock_airplane.return_value = {"ok": True, "airplane_on": False}
        driver = HarmonyDriver(self.SERIAL)
        res = driver.set_airplane_mode(False)
        self.assertTrue(res["ok"])
        mock_airplane.assert_called_once_with(self.SERIAL, False)

    @patch("phone_pilot.harmony.device.utils.get_notifications")
    def test_driver_get_notifications(self, mock_notif: MagicMock) -> None:
        mock_notif.return_value = {"ok": True, "notifications": []}
        driver = HarmonyDriver(self.SERIAL)
        res = driver.get_notifications()
        self.assertTrue(res["ok"])
        mock_notif.assert_called_once_with(self.SERIAL)


class TestHarmonyMCPSystemTools(unittest.IsolatedAsyncioTestCase):
    """Test MCP tool routing for HarmonyOS in _system_tools.py."""

    SERIAL = "harmony-serial-001"

    @patch("phone_pilot.mcp._system_tools._resolve_device_serial")
    @patch("phone_pilot.harmony.device.utils.get_notifications")
    async def test_mcp_get_notifications_harmony(
        self, mock_get: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_resolve.return_value = (self.SERIAL, "harmony", None)
        mock_get.return_value = {"ok": True, "notifications": [{"package": "com.test", "title": "T", "text": "M"}]}

        res = await mcp_system_tools.phone_get_notifications(self.SERIAL, platform="harmony")
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "harmony")
        self.assertEqual(res["device_serial"], self.SERIAL)
        self.assertEqual(len(res["notifications"]), 1)

    @patch("phone_pilot.mcp._system_tools._resolve_device_serial")
    @patch("phone_pilot.harmony.device.utils.set_wifi_enabled")
    async def test_mcp_toggle_wifi_harmony(
        self, mock_wifi: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_resolve.return_value = (self.SERIAL, "harmony", None)
        mock_wifi.return_value = {"ok": True, "wifi_on": True}

        res = await mcp_system_tools.phone_toggle_wifi(True, self.SERIAL, platform="harmony")
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "harmony")
        self.assertEqual(res["device_serial"], self.SERIAL)

    @patch("phone_pilot.mcp._system_tools._resolve_device_serial")
    @patch("phone_pilot.harmony.device.utils.set_airplane_mode")
    async def test_mcp_toggle_airplane_harmony(
        self, mock_airplane: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_resolve.return_value = (self.SERIAL, "harmony", None)
        mock_airplane.return_value = {"ok": True, "airplane_on": True}

        res = await mcp_system_tools.phone_toggle_airplane(True, self.SERIAL, platform="harmony")
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "harmony")
        self.assertEqual(res["device_serial"], self.SERIAL)

    @patch("phone_pilot.mcp._system_tools._resolve_device_serial")
    async def test_mcp_unsupported_platform(self, mock_resolve: MagicMock) -> None:
        mock_resolve.return_value = ("ios-001", "ios", None)
        res_notif = await mcp_system_tools.phone_get_notifications("ios-001", platform="ios")
        self.assertFalse(res_notif["ok"])
        self.assertIn("Android and HarmonyOS", res_notif["error"])

        res_wifi = await mcp_system_tools.phone_toggle_wifi(True, "ios-001", platform="ios")
        self.assertFalse(res_wifi["ok"])
        self.assertIn("Android and HarmonyOS", res_wifi["error"])

        res_air = await mcp_system_tools.phone_toggle_airplane(True, "ios-001", platform="ios")
        self.assertFalse(res_air["ok"])
        self.assertIn("Android and HarmonyOS", res_air["error"])


if __name__ == "__main__":
    unittest.main()
