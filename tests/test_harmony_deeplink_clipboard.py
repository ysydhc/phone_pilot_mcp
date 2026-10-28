#!/usr/bin/env python3
"""
Unit tests for HarmonyOS DeepLink and clipboard read capabilities (Task 1.3).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.harmony.device import utils as h_utils
from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.harmony.hdc.utils import hdc_prefix

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


class TestHarmonyDeepLinkValidation(unittest.TestCase):
    """Test parameter validation for open_deeplink in utils and HarmonyDriver."""

    SERIAL = "harmony-serial-001"

    def test_utils_open_deeplink_empty_uri(self) -> None:
        """utils.open_deeplink with empty/whitespace URI returns error."""
        res_empty = h_utils.open_deeplink(self.SERIAL, "")
        self.assertFalse(res_empty["ok"])
        self.assertEqual(res_empty["error"], "uri is required")

        res_spaces = h_utils.open_deeplink(self.SERIAL, "   \t\n  ")
        self.assertFalse(res_spaces["ok"])
        self.assertEqual(res_spaces["error"], "uri is required")

        res_none = h_utils.open_deeplink(self.SERIAL, None)  # type: ignore[arg-type]
        self.assertFalse(res_none["ok"])
        self.assertEqual(res_none["error"], "uri is required")

    def test_utils_open_deeplink_empty_device_serial(self) -> None:
        """utils.open_deeplink with empty device_serial returns error."""
        res_empty = h_utils.open_deeplink("", "myapp://page/home")
        self.assertFalse(res_empty["ok"])
        self.assertEqual(res_empty["error"], "device_serial is required")

        res_none = h_utils.open_deeplink(None, "myapp://page/home")
        self.assertFalse(res_none["ok"])
        self.assertEqual(res_none["error"], "device_serial is required")

    def test_driver_open_deeplink_empty_uri(self) -> None:
        """HarmonyDriver.open_deeplink with empty/whitespace URI returns error."""
        driver = HarmonyDriver(self.SERIAL)

        res_empty = driver.open_deeplink("")
        self.assertFalse(res_empty["ok"])
        self.assertEqual(res_empty["error"], "uri is required")
        self.assertEqual(res_empty["platform"], "harmony")

        res_spaces = driver.open_deeplink("   ")
        self.assertFalse(res_spaces["ok"])
        self.assertEqual(res_spaces["error"], "uri is required")

        res_none = driver.open_deeplink(None)  # type: ignore[arg-type]
        self.assertFalse(res_none["ok"])
        self.assertEqual(res_none["error"], "uri is required")


class TestHarmonyDeepLinkCommandAssembly(unittest.TestCase):
    """Test command escaping and assembly for utils.open_deeplink."""

    SERIAL = "harmony-serial-002"

    def test_open_deeplink_without_package(self) -> None:
        """open_deeplink without package executes aa start -U <uri> with shlex quoting."""
        uri = "myapp://detail?id=123&action=view;rm -rf /sdcard"
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stdout="start success", returncode=0),
        ) as mock_run:
            res = h_utils.open_deeplink(self.SERIAL, uri, wait_s=0.5)

            self.assertTrue(res["ok"])
            self.assertEqual(res["returncode"], 0)
            self.assertEqual(res["uri"], uri)
            self.assertIsNone(res["package"])
            self.assertEqual(res["platform"], "harmony")

            mock_run.assert_called_once()
            called_cmd = mock_run.call_args[0][0]
            # Expected prefix
            expected_prefix = hdc_prefix(self.SERIAL) + ["shell", "aa", "start", "-U"]
            self.assertEqual(called_cmd[: len(expected_prefix)], expected_prefix)
            # The URI argument must be quoted
            self.assertIn("'-U', \"'myapp://detail?id=123&action=view;rm -rf /sdcard'\"", str(called_cmd))
            # No -b argument when package is None
            self.assertNotIn("-b", called_cmd)

    def test_open_deeplink_with_package(self) -> None:
        """open_deeplink with package appends -b <package> with shlex quoting."""
        uri = "myapp://home?query=$(whoami)"
        pkg = "com.example.harmonyaid;echo 123"
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stdout="start ability ok", returncode=0),
        ) as mock_run:
            res = h_utils.open_deeplink(self.SERIAL, uri, package=pkg, wait_s=0.2)

            self.assertTrue(res["ok"])
            self.assertEqual(res["package"], pkg)
            self.assertEqual(res["platform"], "harmony")

            called_cmd = mock_run.call_args[0][0]
            self.assertIn("-b", called_cmd)
            idx_b = called_cmd.index("-b")
            self.assertEqual(called_cmd[idx_b + 1], f"'{pkg}'")
            idx_u = called_cmd.index("-U")
            self.assertEqual(called_cmd[idx_u + 1], f"'{uri}'")

    def test_open_deeplink_failed_returncode(self) -> None:
        """Nonzero returncode returns ok=False with error message."""
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stderr="error: ability not found", returncode=1),
        ):
            res = h_utils.open_deeplink(self.SERIAL, "myapp://fail")
            self.assertFalse(res["ok"])
            self.assertEqual(res["returncode"], 1)
            self.assertEqual(res["error"], "error: ability not found")

    def test_open_deeplink_runner_exception(self) -> None:
        """Exceptions raised by command runner are captured gracefully."""
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            side_effect=RuntimeError("hdc transport failure"),
        ):
            res = h_utils.open_deeplink(self.SERIAL, "myapp://crash")
            self.assertFalse(res["ok"])
            self.assertIn("hdc transport failure", res["error"])


class TestHarmonyDriverOpenDeeplink(unittest.TestCase):
    """Test HarmonyDriver.open_deeplink dispatch and fallback."""

    SERIAL = "harmony-serial-003"

    def test_driver_open_deeplink_prefers_hmdriver2(self) -> None:
        """When hmdriver2 has open_url, it is called first."""
        driver = HarmonyDriver(self.SERIAL)
        mock_hm = MagicMock()
        mock_hm.open_url = MagicMock()

        with patch("phone_pilot.harmony.driver._get_hm", return_value=mock_hm):
            with patch("phone_pilot.harmony.device.utils.open_deeplink") as mock_h_deeplink:
                res = driver.open_deeplink("myapp://page/detail", package="com.mock.app")

                self.assertTrue(res["ok"])
                self.assertEqual(res["engine"], "hmdriver2")
                self.assertEqual(res["uri"], "myapp://page/detail")
                self.assertEqual(res["package"], "com.mock.app")
                self.assertEqual(res["platform"], "harmony")

                mock_hm.open_url.assert_called_once_with("myapp://page/detail")
                mock_h_deeplink.assert_not_called()

    def test_driver_open_deeplink_fallback_when_open_url_fails(self) -> None:
        """When hmdriver2 open_url throws an exception, fall back to utils.open_deeplink."""
        driver = HarmonyDriver(self.SERIAL)
        mock_hm = MagicMock()
        mock_hm.open_url.side_effect = RuntimeError("daemon socket broken")

        with patch("phone_pilot.harmony.driver._get_hm", return_value=mock_hm):
            with patch(
                "phone_pilot.harmony.device.utils.open_deeplink",
                return_value={"ok": True, "engine": "hdc", "platform": "harmony"},
            ) as mock_h_deeplink:
                res = driver.open_deeplink(
                    "myapp://page/detail", package="com.mock.app", wait_s=0.4
                )

                self.assertTrue(res["ok"])
                self.assertEqual(res["engine"], "hdc")
                mock_h_deeplink.assert_called_once_with(
                    self.SERIAL, "myapp://page/detail", package="com.mock.app", wait_s=0.4
                )

    def test_driver_open_deeplink_fallback_when_no_hmdriver2(self) -> None:
        """When _get_hm fails (e.g. not installed), fall back to utils.open_deeplink."""
        driver = HarmonyDriver(self.SERIAL)

        with patch(
            "phone_pilot.harmony.driver._get_hm",
            side_effect=ImportError("No module named 'hmdriver2'"),
        ):
            with patch(
                "phone_pilot.harmony.device.utils.open_deeplink",
                return_value={"ok": True, "engine": "hdc", "platform": "harmony"},
            ) as mock_h_deeplink:
                res = driver.open_deeplink("myapp://home")

                self.assertTrue(res["ok"])
                mock_h_deeplink.assert_called_once_with(
                    self.SERIAL, "myapp://home", package=None, wait_s=0.8
                )

    def test_driver_open_deeplink_fallback_when_hm_lacks_open_url(self) -> None:
        """When hm object does not have open_url, fall back to utils.open_deeplink."""
        driver = HarmonyDriver(self.SERIAL)
        mock_hm = object()  # Has no open_url

        with patch("phone_pilot.harmony.driver._get_hm", return_value=mock_hm):
            with patch(
                "phone_pilot.harmony.device.utils.open_deeplink",
                return_value={"ok": True, "engine": "hdc", "platform": "harmony"},
            ) as mock_h_deeplink:
                res = driver.open_deeplink("myapp://home")

                self.assertTrue(res["ok"])
                mock_h_deeplink.assert_called_once()


class TestHarmonyClipboard(unittest.TestCase):
    """Test get_clipboard_text and MCP phone_read_clipboard on HarmonyOS."""

    SERIAL = "harmony-serial-004"

    def test_get_clipboard_text_prefers_hmdriver2(self) -> None:
        """When hmdriver2 has clipboard method, use it first."""
        mock_hm = MagicMock()
        mock_hm.get_clipboard_text.return_value = "hello from hmdriver2"

        with patch(
            "phone_pilot.harmony.hmdriver_bridge.get_hmdriver", return_value=mock_hm
        ):
            res = h_utils.get_clipboard_text(self.SERIAL)
            self.assertTrue(res["ok"])
            self.assertEqual(res["text"], "hello from hmdriver2")

    def test_get_clipboard_text_hmdriver2_dict_return(self) -> None:
        """When hmdriver2 returns a dict, it is returned directly."""
        mock_hm = MagicMock()
        # mock_hm does not have get_clipboard_text, but has clipboard returning dict
        del mock_hm.get_clipboard_text
        mock_hm.clipboard = {"ok": True, "text": "dict clipboard"}

        with patch(
            "phone_pilot.harmony.hmdriver_bridge.get_hmdriver", return_value=mock_hm
        ):
            res = h_utils.get_clipboard_text(self.SERIAL)
            self.assertTrue(res["ok"])
            self.assertEqual(res["text"], "dict clipboard")

    def test_get_clipboard_text_fallback_hidumper_formatted(self) -> None:
        """When hmdriver2 fails, fall back to hidumper -s PasteboardService parsing."""
        dump_output = """
-------------------------------[PasteboardService]-------------------------------
PrimaryText: "copied_content_from_hidumper"
Record[0] type: text/plain
"""
        with patch(
            "phone_pilot.harmony.hmdriver_bridge.get_hmdriver",
            side_effect=RuntimeError("no hmdriver2 connection"),
        ):
            with patch.object(
                h_utils.HdcCommandRunner,
                "run",
                return_value=_mock_proc(stdout=dump_output, returncode=0),
            ) as mock_run:
                res = h_utils.get_clipboard_text(self.SERIAL)

                self.assertTrue(res["ok"])
                self.assertEqual(res["text"], "copied_content_from_hidumper")
                mock_run.assert_called_once()
                called_cmd = mock_run.call_args[0][0]
                self.assertEqual(
                    called_cmd,
                    hdc_prefix(self.SERIAL) + ["shell", "hidumper", "-s", "PasteboardService"],
                )

    def test_get_clipboard_text_fallback_hidumper_plain_text(self) -> None:
        """When hidumper returns unformatted text, fallback returns stripped stdout."""
        with patch(
            "phone_pilot.harmony.hmdriver_bridge.get_hmdriver",
            side_effect=RuntimeError("no hmdriver2 connection"),
        ):
            with patch.object(
                h_utils.HdcCommandRunner,
                "run",
                return_value=_mock_proc(stdout="plain clipboard text\n", returncode=0),
            ):
                res = h_utils.get_clipboard_text(self.SERIAL)
                self.assertTrue(res["ok"])
                self.assertEqual(res["text"], "plain clipboard text")

    def test_get_clipboard_text_fallback_hidumper_failure(self) -> None:
        """When hidumper returns nonzero code, return ok=False with error."""
        with patch(
            "phone_pilot.harmony.hmdriver_bridge.get_hmdriver",
            side_effect=RuntimeError("no hmdriver2 connection"),
        ):
            with patch.object(
                h_utils.HdcCommandRunner,
                "run",
                return_value=_mock_proc(stderr="Service PasteboardService not found", returncode=1),
            ):
                res = h_utils.get_clipboard_text(self.SERIAL)
                self.assertFalse(res["ok"])
                self.assertIn("Service PasteboardService not found", res["error"])

    def test_mcp_phone_read_clipboard_harmony_dispatch(self) -> None:
        """phone_read_clipboard routes to get_clipboard_text for harmony platform."""
        with patch.object(
            mcp_system_tools,
            "_resolve_device_serial",
            return_value=(self.SERIAL, "harmony", None),
        ):
            with patch(
                "phone_pilot.harmony.device.utils.get_clipboard_text",
                return_value={"ok": True, "text": "harmony text 123"},
            ) as mock_get_clip:
                result = asyncio.run(
                    mcp_system_tools.phone_read_clipboard(
                        device_serial=self.SERIAL,
                        platform="harmony",
                    )
                )

                mock_get_clip.assert_called_once_with(self.SERIAL)
                self.assertTrue(result["ok"])
                self.assertEqual(result["text"], "harmony text 123")
                self.assertEqual(result["device_serial"], self.SERIAL)
                self.assertEqual(result["platform"], "harmony")

    def test_mcp_phone_read_clipboard_unsupported_platform(self) -> None:
        """phone_read_clipboard returns error for unsupported platform."""
        with patch.object(
            mcp_system_tools,
            "_resolve_device_serial",
            return_value=("some-serial", "windows", None),
        ):
            result = asyncio.run(
                mcp_system_tools.phone_read_clipboard(
                    device_serial="some-serial",
                    platform="windows",
                )
            )

            self.assertFalse(result["ok"])
            self.assertIn("supports Android, iOS, and HarmonyOS", result["error"])
            self.assertEqual(result["platform"], "windows")


if __name__ == "__main__":
    unittest.main()
