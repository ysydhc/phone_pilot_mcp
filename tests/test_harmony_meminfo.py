#!/usr/bin/env python3
"""
Unit tests for HarmonyOS meminfo capture and analysis (Task 3.4).
Tests parse_harmony_meminfo, capture_meminfo, diff/compare_meminfo, HarmonyDriver, and MCP tools.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.harmony.device import utils as h_utils
from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.memory_analyze.meminfo import (
    capture_meminfo,
    diff_meminfo,
    compare_meminfo,
)

# Ensure MCP server is safely mocked if mcp package has version mismatch
if "phone_pilot.mcp.server" not in sys.modules:
    mock_srv = MagicMock()
    mock_srv.mcp.tool = lambda *a, **k: (lambda fn: fn)
    sys.modules["phone_pilot.mcp.server"] = mock_srv

from phone_pilot.mcp import _script_tools as mcp_script_tools


def _mock_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["mock"], returncode=returncode, stdout=stdout, stderr=stderr
    )


RAW_HIDUMPER_TABLE = """
Processes
    PID    Total Pss    Total Vss    Total Rss    Total Uss       GL    Graph    Name
  12345        51200       204800       102400        40960     2048     1024    com.example.app
  67890        25600       102400        51200        20480     1024      512    com.other.app

Details:
ark ts heap: 16384
native heap: 20480
AnonPage: 8192
FilePage: 4096
stack: 2048
"""

RAW_HIDUMPER_KEYVALUE = """
Memory usage of process 12345 [com.example.app]:
Total Pss: 51200 kB
Total Vss: 204800 kB
Total Rss: 102400 kB
Total Uss: 40960 kB
Ark TS Heap: 16384 kB
Native Heap: 20480 kB
GL: 2048 kB
Graph: 1024 kB

Details:
ark ts heap: 16384
native heap: 20480
AnonPage: 8192
FilePage: 4096
stack: 2048
"""


class TestParseHarmonyMeminfo(unittest.TestCase):
    """Test parse_harmony_meminfo parsing logic."""

    def test_parse_table_format(self) -> None:
        parsed = h_utils.parse_harmony_meminfo(
            RAW_HIDUMPER_TABLE, package_name="com.example.app"
        )
        self.assertEqual(parsed["pid"], 12345)
        self.assertEqual(parsed["name"], "com.example.app")

        summary = parsed["summary"]
        self.assertEqual(summary["total_pss_kb"], 51200)
        self.assertEqual(summary["total_pss_mb"], 50.0)
        self.assertEqual(summary["total_vss_kb"], 204800)
        self.assertEqual(summary["total_rss_kb"], 102400)
        self.assertEqual(summary["total_uss_kb"], 40960)
        self.assertEqual(summary["graphics_kb"], 3072)  # 2048 + 1024
        self.assertEqual(summary["graphics_mb"], 3.0)

        # Details
        details = parsed["details"]
        self.assertEqual(details["ark ts heap"]["pss"], 16384)
        self.assertEqual(details["native heap"]["pss"], 20480)
        self.assertEqual(details["AnonPage"]["pss"], 8192)
        self.assertEqual(details["FilePage"]["pss"], 4096)
        self.assertEqual(details["stack"]["pss"], 2048)

        # Aliases for Android cross-compatibility
        self.assertEqual(summary["ark_ts_heap_kb"], 16384)
        self.assertEqual(summary["dalvik_heap_kb"], 16384)
        self.assertEqual(summary["java_heap_kb"], 16384)
        self.assertEqual(summary["native_heap_kb"], 20480)

    def test_parse_keyvalue_format(self) -> None:
        parsed = h_utils.parse_harmony_meminfo(
            RAW_HIDUMPER_KEYVALUE, package_name="com.example.app"
        )
        summary = parsed["summary"]
        self.assertEqual(summary["total_pss_kb"], 51200)
        self.assertEqual(summary["total_pss_mb"], 50.0)
        self.assertEqual(summary["total_vss_kb"], 204800)
        self.assertEqual(summary["native_heap_kb"], 20480)
        self.assertEqual(summary["ark_ts_heap_kb"], 16384)
        self.assertEqual(summary["dalvik_heap_kb"], 16384)
        self.assertEqual(summary["java_heap_kb"], 16384)

    def test_parse_empty_output(self) -> None:
        parsed = h_utils.parse_harmony_meminfo("", package_name="com.example.app")
        self.assertEqual(parsed["summary"]["total_pss_kb"], 0)
        self.assertEqual(parsed["details"], {})


class TestCaptureHarmonyMeminfo(unittest.TestCase):
    """Test capture_meminfo in harmony.device.utils."""

    SERIAL = "harmony-serial-001"

    def test_validation_errors(self) -> None:
        res_no_serial = h_utils.capture_meminfo("", "com.example.app")
        self.assertFalse(res_no_serial["ok"])
        self.assertIn("device_serial", res_no_serial["error"])

        res_no_pkg = h_utils.capture_meminfo(self.SERIAL, "")
        self.assertFalse(res_no_pkg["ok"])
        self.assertIn("package_name", res_no_pkg["error"])

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_capture_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _mock_proc(stdout=RAW_HIDUMPER_TABLE, returncode=0)
        res = h_utils.capture_meminfo(self.SERIAL, "com.example.app")
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "harmony")
        self.assertEqual(res["package"], "com.example.app")
        self.assertEqual(res["summary"]["total_pss_kb"], 51200)

    @patch("phone_pilot.harmony.device.utils.HdcCommandRunner.run")
    def test_capture_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _mock_proc(
            stdout="", stderr="No such process", returncode=1
        )
        res = h_utils.capture_meminfo(self.SERIAL, "com.example.app")
        self.assertFalse(res["ok"])
        self.assertIn("hidumper --mem failed", res["error"])
        self.assertEqual(res["platform"], "harmony")


class TestUnifiedMeminfoModule(unittest.TestCase):
    """Test memory_analyze.meminfo capture and diff functions."""

    SERIAL = "device-serial-001"

    @patch("phone_pilot.harmony.device.utils.capture_meminfo")
    def test_capture_meminfo_routes_harmony(self, mock_h_cap: MagicMock) -> None:
        mock_h_cap.return_value = {
            "ok": True,
            "platform": "harmony",
            "package": "com.example.app",
            "summary": {"total_pss_kb": 10240, "total_pss_mb": 10.0},
        }
        res = capture_meminfo(
            self.SERIAL, "com.example.app", platform="harmony"
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "harmony")
        mock_h_cap.assert_called_once_with(
            self.SERIAL, "com.example.app", timeout_s=30.0
        )

    @patch("phone_pilot.memory_analyze.meminfo.CommandRunner.run")
    def test_capture_meminfo_routes_android(self, mock_adb_run: MagicMock) -> None:
        mock_adb_run.return_value = _mock_proc(
            stdout="TOTAL PSS: 12345 Native Heap: 4567 Dalvik Heap: 2345",
            returncode=0,
        )
        res = capture_meminfo(
            self.SERIAL, "com.example.app", platform="android"
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "android")

    def test_diff_meminfo_harmony(self) -> None:
        before = {
            "platform": "harmony",
            "package": "com.example.app",
            "captured_at": "2026-10-28 10:00:00",
            "summary": {
                "total_pss_kb": 51200,
                "total_pss_mb": 50.0,
                "native_heap_kb": 20480,
                "native_heap_mb": 20.0,
                "dalvik_heap_kb": 16384,
                "dalvik_heap_mb": 16.0,
                "java_heap_kb": 16384,
                "java_heap_mb": 16.0,
                "graphics_kb": 3072,
                "graphics_mb": 3.0,
            },
        }
        after = {
            "platform": "harmony",
            "package": "com.example.app",
            "captured_at": "2026-10-28 10:05:00",
            "summary": {
                "total_pss_kb": 61440,
                "total_pss_mb": 60.0,
                "native_heap_kb": 25600,
                "native_heap_mb": 25.0,
                "dalvik_heap_kb": 18432,
                "dalvik_heap_mb": 18.0,
                "java_heap_kb": 18432,
                "java_heap_mb": 18.0,
                "graphics_kb": 4096,
                "graphics_mb": 4.0,
            },
        }
        diff = diff_meminfo(before, after)
        self.assertTrue(diff["ok"])
        self.assertEqual(diff["before"]["platform"], "harmony")
        self.assertEqual(diff["after"]["platform"], "harmony")
        self.assertEqual(diff["diff"]["total_pss_mb"], 10.0)
        self.assertEqual(diff["diff"]["native_heap_mb"], 5.0)

    def test_compare_meminfo_details(self) -> None:
        before = {
            "platform": "harmony",
            "package": "com.example.app",
            "summary": {"total_pss_kb": 50000},
            "details": {
                "ark ts heap": {"pss": 10000},
                "native heap": {"pss": 20000},
                "AnonPage": {"pss": 5000},
            },
        }
        after = {
            "platform": "harmony",
            "package": "com.example.app",
            "summary": {"total_pss_kb": 65000},
            "details": {
                "ark ts heap": {"pss": 15000},
                "native heap": {"pss": 22000},
                "AnonPage": {"pss": 4000},
                "stack": {"pss": 1000},
            },
        }
        res = compare_meminfo(before, after)
        self.assertTrue(res["ok"])
        details_diff = res["details_diff"]
        self.assertEqual(details_diff["ark ts heap"]["delta_pss_kb"], 5000)
        self.assertEqual(details_diff["native heap"]["delta_pss_kb"], 2000)
        self.assertEqual(details_diff["AnonPage"]["delta_pss_kb"], -1000)
        self.assertEqual(details_diff["stack"]["delta_pss_kb"], 1000)


class TestHarmonyDriverMeminfo(unittest.TestCase):
    """Test get_meminfo in HarmonyDriver."""

    SERIAL = "harmony-serial-001"

    @patch("phone_pilot.harmony.device.utils.capture_meminfo")
    def test_driver_get_meminfo(self, mock_cap: MagicMock) -> None:
        mock_cap.return_value = {
            "ok": True,
            "platform": "harmony",
            "package": "com.example.app",
            "summary": {"total_pss_kb": 51200},
        }
        driver = HarmonyDriver(self.SERIAL)
        res = driver.get_meminfo("com.example.app")
        self.assertTrue(res["ok"])
        mock_cap.assert_called_once_with(
            self.SERIAL, "com.example.app", timeout_s=30.0
        )


class TestHarmonyMCPMemorySnapshot(unittest.IsolatedAsyncioTestCase):
    """Test phone_memory_snapshot MCP tool with HarmonyOS."""

    SERIAL = "harmony-serial-001"

    @patch("phone_pilot.mcp._helpers._resolve_device_serial")
    @patch("phone_pilot.memory_analyze.meminfo.capture_meminfo")
    async def test_mcp_memory_snapshot_harmony(
        self, mock_cap: MagicMock, mock_resolve: MagicMock
    ) -> None:
        mock_resolve.return_value = (self.SERIAL, "harmony", None)
        mock_cap.return_value = {
            "ok": True,
            "platform": "harmony",
            "package": "com.example.app",
            "summary": {"total_pss_kb": 51200},
        }

        res = await mcp_script_tools.phone_memory_snapshot(
            package="com.example.app",
            device_serial=self.SERIAL,
            platform="harmony",
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["platform"], "harmony")
        mock_cap.assert_called_once_with(
            self.SERIAL, "com.example.app", platform="harmony"
        )

    @patch("phone_pilot.mcp._helpers._resolve_device_serial")
    async def test_mcp_memory_snapshot_unsupported(
        self, mock_resolve: MagicMock
    ) -> None:
        mock_resolve.return_value = ("ios-001", "ios", None)
        res = await mcp_script_tools.phone_memory_snapshot(
            package="com.example.app",
            device_serial="ios-001",
            platform="ios",
        )
        self.assertFalse(res["ok"])
        self.assertIn("Android and HarmonyOS", res["error"])


if __name__ == "__main__":
    unittest.main()
