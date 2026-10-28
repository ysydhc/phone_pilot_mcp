#!/usr/bin/env python3
"""
Unit tests for HarmonyOS shell execution security and MCP dispatch.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.harmony.device import utils as h_utils
from phone_pilot.harmony.hdc.utils import hdc_prefix

# Ensure MCP server is safely mocked if mcp package has version mismatch
if "phone_pilot.mcp.server" not in sys.modules:
    mock_srv = MagicMock()
    mock_srv.mcp.tool = lambda *a, **k: (lambda fn: fn)
    sys.modules["phone_pilot.mcp.server"] = mock_srv

from phone_pilot.mcp import _system_tools as mcp_system_tools


class TestHarmonyExecuteShell(unittest.TestCase):
    """Test restricted shell execution logic in HarmonyOS device utils."""

    SERIAL = "harmony-serial-001"

    def _mock_proc(
        self, stdout: str = "", stderr: str = "", returncode: int = 0
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["mock"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def test_execute_shell_empty_command(self) -> None:
        """Empty command should return error."""
        res = h_utils.execute_shell(self.SERIAL, "")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "command is required")

        res_spaces = h_utils.execute_shell(self.SERIAL, "    ")
        self.assertFalse(res_spaces["ok"])
        self.assertEqual(res_spaces["error"], "command is required")

    def test_execute_shell_whitelist_commands_allowed(self) -> None:
        """All whitelisted command prefixes should pass and execute via HdcCommandRunner."""
        for prefix in h_utils._SHELL_WHITELIST:
            cmd = f"{prefix} --help"
            with patch.object(
                h_utils.HdcCommandRunner,
                "run",
                return_value=self._mock_proc(stdout=f"help for {prefix}\n", returncode=0),
            ) as mock_run:
                res = h_utils.execute_shell(self.SERIAL, cmd, timeout_s=15.0)

                self.assertTrue(res["ok"])
                self.assertEqual(res["stdout"], f"help for {prefix}")
                self.assertEqual(res["stderr"], "")
                self.assertEqual(res["returncode"], 0)

                expected_cmd = hdc_prefix(self.SERIAL) + ["shell", prefix, "--help"]
                mock_run.assert_called_once_with(
                    expected_cmd, check=False, log_output=False, timeout_s=15.0
                )

    def test_execute_shell_whitelist_specific_param(self) -> None:
        """param get command executes and parses output correctly."""
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=self._mock_proc(stdout="ALN-AL00\n", returncode=0),
        ) as mock_run:
            res = h_utils.execute_shell(
                self.SERIAL, "param get const.product.model", timeout_s=10.0
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["stdout"], "ALN-AL00")
            self.assertEqual(res["returncode"], 0)
            mock_run.assert_called_once_with(
                hdc_prefix(self.SERIAL)
                + ["shell", "param", "get", "const.product.model"],
                check=False,
                log_output=False,
                timeout_s=10.0,
            )

    def test_execute_shell_non_whitelist_blocked(self) -> None:
        """Non-whitelisted commands should be rejected."""
        invalid_cmds = [
            ("curl https://example.com", "curl"),
            ("wget http://example.com", "wget"),
            ("python3 script.py", "python3"),
            ("bash -c whoami", "bash"),
            ("echo hello", "echo"),
            ("su", "su"),
        ]
        for cmd, first in invalid_cmds:
            res = h_utils.execute_shell(self.SERIAL, cmd)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], f"command prefix '{first}' not in whitelist")

    def test_execute_shell_forbidden_commands_blocked(self) -> None:
        """Forbidden command prefixes should be rejected."""
        forbidden_cases = [
            ("reboot", "reboot"),
            ("reboot now", "reboot"),
            ("shutdown", "shutdown"),
            ("mkfs.ext4 /dev/block", "mkfs"),
            ("dd if=/dev/zero of=/dev/block", "dd"),
        ]
        for cmd, prefix in forbidden_cases:
            res = h_utils.execute_shell(self.SERIAL, cmd)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], f"command prefix '{prefix}' is forbidden")

    def test_execute_shell_rm_rf_blocked(self) -> None:
        """rm -rf and rm -fr should be explicitly blocked."""
        for cmd in ["rm -rf /data", "rm -fr /data/local"]:
            res = h_utils.execute_shell(self.SERIAL, cmd)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "rm -rf is forbidden")

    def test_execute_shell_runner_exception_handled(self) -> None:
        """Subprocess or runner exceptions should be caught and returned as error."""
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            side_effect=RuntimeError("HDC runner failed"),
        ):
            res = h_utils.execute_shell(self.SERIAL, "param get const.product.model")
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "HDC runner failed")

    def test_execute_shell_nonzero_returncode(self) -> None:
        """Nonzero return codes should be preserved in result."""
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=self._mock_proc(
                stdout="", stderr="param: not found\n", returncode=127
            ),
        ):
            res = h_utils.execute_shell(self.SERIAL, "param get unknown.key")
            self.assertTrue(res["ok"])
            self.assertEqual(res["returncode"], 127)
            self.assertEqual(res["stderr"], "param: not found")


class TestMcpPhoneExecuteShellHarmony(unittest.TestCase):
    """Test MCP tool dispatch for HarmonyOS platform."""

    SERIAL = "harmony-serial-002"

    def test_mcp_phone_execute_shell_dispatches_to_harmony(self) -> None:
        """phone_execute_shell routes to HarmonyOS execute_shell when platform == 'harmony'."""
        with (
            patch.object(
                mcp_system_tools,
                "_resolve_device_serial",
                return_value=(self.SERIAL, "harmony", None),
            ),
            patch(
                "phone_pilot.harmony.device.utils.execute_shell",
                return_value={
                    "ok": True,
                    "stdout": "HUAWEI Mate 60",
                    "stderr": "",
                    "returncode": 0,
                },
            ) as mock_exec,
        ):
            result = asyncio.run(
                mcp_system_tools.phone_execute_shell(
                    command="param get const.product.model",
                    device_serial=self.SERIAL,
                    timeout_s=25.0,
                    platform="harmony",
                )
            )

            mock_exec.assert_called_once_with(
                self.SERIAL, "param get const.product.model", 25.0
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["stdout"], "HUAWEI Mate 60")
            self.assertEqual(result["device_serial"], self.SERIAL)
            self.assertEqual(result["platform"], "harmony")

    def test_mcp_phone_execute_shell_unsupported_platform(self) -> None:
        """phone_execute_shell returns error for platforms other than android, ios, harmony."""
        with patch.object(
            mcp_system_tools,
            "_resolve_device_serial",
            return_value=("fake-serial", "windows", None),
        ):
            result = asyncio.run(
                mcp_system_tools.phone_execute_shell(
                    command="cat /etc/hosts",
                    device_serial="fake-serial",
                    platform="windows",
                )
            )

            self.assertFalse(result["ok"])
            self.assertIn("supports Android, iOS, and HarmonyOS", result["error"])
            self.assertEqual(result["platform"], "windows")

    def test_mcp_phone_execute_shell_device_resolution_error(self) -> None:
        """phone_execute_shell forwards resolution errors."""
        err_dict = {"ok": False, "error": "device_not_found"}
        with patch.object(
            mcp_system_tools,
            "_resolve_device_serial",
            return_value=(None, None, err_dict),
        ):
            result = asyncio.run(
                mcp_system_tools.phone_execute_shell(
                    command="cat /etc/hosts",
                    device_serial="missing-serial",
                    platform="harmony",
                )
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "device_not_found")


if __name__ == "__main__":
    unittest.main()
