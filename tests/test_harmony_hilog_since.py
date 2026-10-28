#!/usr/bin/env python3
"""
Unit tests for HiLog precise timestamp slicing & incremental reading.
Task 2.4 requirements verification:
- device_hilog_time (HDC date command execution and fallback)
- read_hilog(since=...) timestamp filtering & multiline handling
- HarmonyDriver logcat_time and read_log(since=...)
- logcat_mark / logcat_since on platform="harmony"
- MCP phone_logcat_since on platform="harmony"
"""

from __future__ import annotations

import re
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.harmony.hdc.logcat import (
    _compare_ts,
    _norm_ts,
    device_hilog_time,
    read_hilog,
)
from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.script_api.logcat import (
    _bump_logcat_ts,
    _merge_logcat_lines,
    logcat_mark,
    logcat_since,
)

# Ensure MCP server is safely mocked if mcp package has version mismatch
if "phone_pilot.mcp.server" not in sys.modules:
    mock_srv = MagicMock()
    mock_srv.mcp.tool = lambda *a, **k: (lambda fn: fn)
    sys.modules["phone_pilot.mcp.server"] = mock_srv

from phone_pilot.mcp._logcat_tools import _phone_logcat_since_sync


def _mock_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["mock"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestDeviceHilogTime(unittest.TestCase):
    """Test device_hilog_time HDC command execution and host fallback."""

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_device_hilog_time_success(self, mock_run):
        mock_run.return_value = _mock_proc(stdout="08-07 14:30:15.000\n", returncode=0)

        ts = device_hilog_time("harmony-device-01")
        self.assertEqual(ts, "08-07 14:30:15.000")

        # Verify command arguments
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("-t", called_cmd)
        self.assertIn("harmony-device-01", called_cmd)
        self.assertIn("shell", called_cmd)
        self.assertIn("date", called_cmd)
        self.assertIn("+%m-%d %H:%M:%S.000", called_cmd)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_device_hilog_time_without_serial(self, mock_run):
        mock_run.return_value = _mock_proc(stdout="08-07 14:30:15.000\n", returncode=0)

        ts = device_hilog_time(None)
        self.assertEqual(ts, "08-07 14:30:15.000")

        called_cmd = mock_run.call_args[0][0]
        self.assertNotIn("-t", called_cmd)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_device_hilog_time_fallback_on_nonzero_returncode(self, mock_run):
        mock_run.return_value = _mock_proc(stdout="", stderr="device offline", returncode=1)

        ts = device_hilog_time("harmony-device-01")
        self.assertIsNotNone(ts)
        # Should fallback to current host time in MM-DD HH:MM:SS.000 format
        pattern = r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.000$"
        self.assertRegex(ts, pattern)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_device_hilog_time_fallback_on_exception(self, mock_run):
        mock_run.side_effect = RuntimeError("HDC connection timeout")

        ts = device_hilog_time("harmony-device-01")
        self.assertIsNotNone(ts)
        pattern = r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.000$"
        self.assertRegex(ts, pattern)


class TestReadHilogSinceFiltering(unittest.TestCase):
    """Test read_hilog incremental slicing with since parameter."""

    def test_norm_ts_and_compare_ts(self):
        """Test timestamp normalizer and comparison logic."""
        # Standard format
        self.assertEqual(_norm_ts("08-07 10:15:30.000"), "08-07 10:15:30.000")
        # 4-digit year format normalized
        self.assertEqual(_norm_ts("2026-08-07 10:15:30.000"), "08-07 10:15:30.000")
        # Microseconds truncated to milliseconds
        self.assertEqual(_norm_ts("08-07 10:15:30.123456"), "08-07 10:15:30.123")
        # No milliseconds padded
        self.assertEqual(_norm_ts("08-07 10:15:30"), "08-07 10:15:30.000")

        # Comparisons
        self.assertTrue(_compare_ts("08-07 10:15:30.000", "08-07 10:15:30.000"))
        self.assertTrue(_compare_ts("08-07 10:15:30.001", "08-07 10:15:30.000"))
        self.assertFalse(_compare_ts("08-07 10:15:29.999", "08-07 10:15:30.000"))

        # Year prefix comparisons
        self.assertTrue(_compare_ts("2026-08-07 10:15:30.001", "08-07 10:15:30.000"))
        self.assertTrue(_compare_ts("2026-08-07 10:15:30.001", "2026-08-07 10:15:30.000"))
        self.assertFalse(_compare_ts("2026-08-07 10:15:29.999", "2026-08-07 10:15:30.000"))

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_read_hilog_no_since_returns_raw(self, mock_run):
        raw = "08-07 10:00:00.000 1 2 I Tag: line1\n08-07 10:00:01.000 1 2 I Tag: line2\n"
        mock_run.return_value = _mock_proc(stdout=raw, returncode=0)

        res = read_hilog("serial-01", lines=50)
        self.assertEqual(res, raw)

        called_cmd = mock_run.call_args[0][0]
        self.assertIn("-t", called_cmd)
        self.assertIn("50", called_cmd)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_read_hilog_since_expands_query_lines(self, mock_run):
        mock_run.return_value = _mock_proc(stdout="", returncode=0)

        read_hilog("serial-01", lines=100, since="08-07 10:00:00.000")
        called_cmd = mock_run.call_args[0][0]
        # Query lines expanded from 100 to at least 2000
        self.assertIn("-t", called_cmd)
        self.assertIn("2000", called_cmd)

        read_hilog("serial-01", lines=5000, since="08-07 10:00:00.000")
        called_cmd2 = mock_run.call_args[0][0]
        self.assertIn("5000", called_cmd2)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_read_hilog_since_filtering_and_multiline(self, mock_run):
        raw = (
            "08-07 10:15:28.000 100 200 I Tag: before 1\n"
            "08-07 10:15:29.500 100 200 E Tag: before error\n"
            "    stack frame old\n"
            "08-07 10:15:30.000 100 200 I Tag: exact mark line\n"
            "08-07 10:15:31.200 100 200 E Tag: after error\n"
            "    stack frame new 1\n"
            "    stack frame new 2\n"
            "08-07 10:15:32.000 100 200 I Tag: after 2\n"
        )
        mock_run.return_value = _mock_proc(stdout=raw, returncode=0)

        filtered = read_hilog("serial-01", since="08-07 10:15:30.000")

        # Lines before 10:15:30.000 and their stack frames are dropped
        self.assertNotIn("before 1", filtered)
        self.assertNotIn("before error", filtered)
        self.assertNotIn("stack frame old", filtered)

        # Lines at or after 10:15:30.000 and their stack frames are kept
        self.assertIn("exact mark line", filtered)
        self.assertIn("after error", filtered)
        self.assertIn("stack frame new 1", filtered)
        self.assertIn("stack frame new 2", filtered)
        self.assertIn("after 2", filtered)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_read_hilog_tag_and_level_filter(self, mock_run):
        mock_run.return_value = _mock_proc(stdout="", returncode=0)

        read_hilog("serial-01", tag_filter="MyTag", level="Warn")
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("-T", called_cmd)
        self.assertIn("MyTag", called_cmd)
        self.assertIn("-L", called_cmd)
        self.assertIn("W", called_cmd)


class TestHarmonyDriverLogMethods(unittest.TestCase):
    """Test HarmonyDriver logcat_time and read_log methods."""

    @patch("phone_pilot.harmony.hdc.logcat.device_hilog_time")
    def test_driver_logcat_time(self, mock_device_hilog_time):
        mock_device_hilog_time.return_value = "08-07 12:00:00.000"
        driver = HarmonyDriver("harmony-serial-02")
        t = driver.logcat_time()
        self.assertEqual(t, "08-07 12:00:00.000")
        mock_device_hilog_time.assert_called_once_with("harmony-serial-02")

    @patch("phone_pilot.harmony.hdc.logcat.read_hilog")
    def test_driver_read_log_passes_since(self, mock_read_hilog):
        mock_read_hilog.return_value = "log output"
        driver = HarmonyDriver("harmony-serial-02")
        res = driver.read_log(
            lines=300,
            since="08-07 12:00:00.000",
            tag_filter="CustomTag",
            level="E",
        )
        self.assertEqual(res, "log output")
        mock_read_hilog.assert_called_once_with(
            "harmony-serial-02",
            lines=300,
            since="08-07 12:00:00.000",
            tag_filter="CustomTag",
            level="E",
        )


class TestScriptApiHarmonyLogcat(unittest.TestCase):
    """Test script_api functions logcat_mark, logcat_since, and log helpers."""

    def test_bump_logcat_ts(self):
        """_bump_logcat_ts handles both standard and 4-digit year timestamps."""
        # Standard
        self.assertEqual(_bump_logcat_ts("08-07 10:15:30.123"), "08-07 10:15:30.124")
        self.assertEqual(_bump_logcat_ts("08-07 10:15:30.999"), "08-07 10:15:31.000")
        # With 4-digit year
        self.assertEqual(
            _bump_logcat_ts("2026-08-07 10:15:30.123"), "2026-08-07 10:15:30.124"
        )
        self.assertEqual(
            _bump_logcat_ts("2026-08-07 10:15:30.999"), "2026-08-07 10:15:31.000"
        )

    def test_merge_logcat_lines_harmony_headers(self):
        """_merge_logcat_lines supports HarmonyOS hilog domain and year headers."""
        raw = (
            "08-07 10:15:30.123 1234 5678 I C02b00/MyApp: message 1\n"
            "    stack frame 1\n"
            "2026-08-07 10:15:31.456 1234 5678 D MyApp/domain: message 2\n"
            "08-07 10:15:32.789 1234 5678 W [MyApp]: message 3\n"
        )
        merged = _merge_logcat_lines(raw)
        self.assertEqual(len(merged), 3)
        self.assertIn("stack frame 1", merged[0])
        self.assertEqual(merged[1], "2026-08-07 10:15:31.456 1234 5678 D MyApp/domain: message 2")
        self.assertEqual(merged[2], "08-07 10:15:32.789 1234 5678 W [MyApp]: message 3")

    def test_logcat_mark_and_since_harmony(self):
        """logcat_mark and logcat_since on HarmonyOS context."""
        driver = MagicMock()
        driver.logcat_time.return_value = "08-07 10:00:00.000"
        driver.read_log.return_value = (
            "08-07 10:00:01.000 100 200 I Tag: event=SUCCESS\n"
        )
        ctx = SimpleNamespace(driver=driver, platform="harmony")

        mark = logcat_mark(ctx)
        self.assertEqual(mark, "08-07 10:00:00.000")

        res = logcat_since(ctx, mark, pattern="event=SUCCESS")
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 1)
        self.assertIn("event=SUCCESS", res["hits"][0])


class TestMcpLogcatSinceHarmony(unittest.TestCase):
    """Test MCP phone_logcat_since tool on platform='harmony'."""

    @patch("phone_pilot.mcp._logcat_tools.get_driver")
    @patch("phone_pilot.mcp._logcat_tools._resolve_device_serial")
    def test_phone_logcat_since_harmony_with_since(self, mock_resolve, mock_get_driver):
        mock_resolve.return_value = ("harmony-serial-99", "harmony", None)
        mock_driver = MagicMock()
        mock_driver.read_log.return_value = (
            "08-07 10:00:01.000 1 2 I Tag: Step 1 completed\n"
            "08-07 10:00:02.000 1 2 I Tag: Step 2 in progress\n"
        )
        mock_get_driver.return_value = mock_driver

        res = _phone_logcat_since_sync(
            device_serial="harmony-serial-99",
            since="08-07 10:00:00.000",
            pattern="Step 1",
            regex=False,
            platform="harmony",
        )

        self.assertTrue(res["ok"])
        self.assertEqual(res["since"], "08-07 10:00:00.000")
        self.assertEqual(res["count"], 1)
        self.assertIn("Step 1 completed", res["matches"][0])
        mock_get_driver.assert_called_once_with("harmony-serial-99", "harmony")
        mock_driver.read_log.assert_called_once_with(since="08-07 10:00:00.000")

    @patch("phone_pilot.mcp._logcat_tools.get_driver")
    @patch("phone_pilot.mcp._logcat_tools._resolve_device_serial")
    def test_phone_logcat_since_harmony_mark_mode(self, mock_resolve, mock_get_driver):
        mock_resolve.return_value = ("harmony-serial-99", "harmony", None)
        mock_driver = MagicMock()
        mock_driver.logcat_time.return_value = "08-07 10:30:00.000"
        mock_driver.read_log.return_value = ""
        mock_get_driver.return_value = mock_driver

        res = _phone_logcat_since_sync(
            device_serial="harmony-serial-99",
            since="",
            pattern="",
            regex=False,
            platform="harmony",
        )

        self.assertTrue(res["ok"])
        self.assertEqual(res["since"], "08-07 10:30:00.000")
        mock_driver.logcat_time.assert_called_once()


if __name__ == "__main__":
    unittest.main()
