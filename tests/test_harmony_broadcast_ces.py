#!/usr/bin/env python3
"""
Unit tests for HarmonyOS Common Event Service (CES) broadcast & app liveness detection.
Task 2.3 requirements verification.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

from phone_pilot.script_api.broadcast import _pid_of, ensure_app_alive, send_broadcast

MARK = "08-07 10:00:00.000"
HDR = "08-07 10:00:01.000 1234 5678 I C02b00/AdTest: "


def _make_harmony_ctx(shell_ok=True, log_lines=None, mark=MARK):
    driver = MagicMock()
    driver.shell.return_value = {
        "ok": shell_ok,
        "stdout": "",
        "stderr": "",
        "returncode": 0 if shell_ok else 1,
    }
    driver.logcat_time.return_value = mark
    driver.read_log.return_value = (
        "\n".join(HDR + ln for ln in (log_lines or [])) + "\n" if log_lines else ""
    )
    driver.clear_log.return_value = {"ok": True}
    driver.open_deeplink.return_value = {"ok": True}
    driver.app = MagicMock()
    return SimpleNamespace(driver=driver, platform="harmony")


class TestHarmonyBroadcastCES(unittest.TestCase):
    """Test send_broadcast and ensure_app_alive on platform='harmony'."""

    def test_ces_pub_basic_command(self):
        """ces pub basic action generation."""
        ctx = _make_harmony_ctx()
        r = send_broadcast(ctx, "usual.event.TEST")
        self.assertTrue(r["ok"])
        cmd = ctx.driver.shell.call_args[0][0]
        self.assertEqual(cmd, "ces pub -e usual.event.TEST")

    def test_ces_pub_package_and_flags(self):
        """ces pub with package (-p) and flags."""
        ctx = _make_harmony_ctx()
        r = send_broadcast(
            ctx,
            "usual.event.CUSTOM",
            package="com.example.harmonyapp",
            flags=["--sticky"],
        )
        self.assertTrue(r["ok"])
        cmd = ctx.driver.shell.call_args[0][0]
        self.assertIn("ces pub -e usual.event.CUSTOM", cmd)
        self.assertIn("-p com.example.harmonyapp", cmd)
        self.assertIn("--sticky", cmd)

    def test_ces_pub_extras_data(self):
        """ces pub with 'data' in extras maps to -d."""
        ctx = _make_harmony_ctx()
        r = send_broadcast(
            ctx,
            "usual.event.DATA_EVENT",
            extras={"data": "payload_content_123"},
        )
        self.assertTrue(r["ok"])
        cmd = ctx.driver.shell.call_args[0][0]
        self.assertEqual(cmd, "ces pub -e usual.event.DATA_EVENT -d payload_content_123")

    def test_ces_pub_extras_json_serialization(self):
        """ces pub with arbitrary extras serializes to JSON string in -d."""
        ctx = _make_harmony_ctx()
        extras = {"key1": "val1", "key2": "val2 with spaces"}
        r = send_broadcast(ctx, "usual.event.JSON_EVENT", extras=extras)
        self.assertTrue(r["ok"])
        cmd = ctx.driver.shell.call_args[0][0]
        self.assertIn("ces pub -e usual.event.JSON_EVENT -d", cmd)
        # Extract quoted argument after -d
        parts = cmd.split("-d ")
        self.assertEqual(len(parts), 2)
        raw_json = parts[1].strip("'\"")
        decoded = json.loads(raw_json)
        self.assertEqual(decoded, extras)

    def test_ces_pub_extras_code(self):
        """ces pub with digit code in extras passes -s <code>."""
        ctx = _make_harmony_ctx()
        r = send_broadcast(
            ctx,
            "usual.event.CODE_EVENT",
            extras={"data": "test_data", "code": "100"},
        )
        self.assertTrue(r["ok"])
        cmd = ctx.driver.shell.call_args[0][0]
        self.assertIn("-d test_data", cmd)
        self.assertIn("-s 100", cmd)

    def test_ces_pub_redact_keys(self):
        """redact_keys masks secret in cmd_logged and returned dict."""
        ctx = _make_harmony_ctx()
        r = send_broadcast(
            ctx,
            "usual.event.AUTH",
            extras={"token": "secret_token_val", "user": "alice"},
            redact_keys=["token"],
        )
        self.assertTrue(r["ok"])
        # Real executed shell command has real token
        real_cmd = ctx.driver.shell.call_args[0][0]
        self.assertIn("secret_token_val", real_cmd)

        # Returned cmd and dict have token masked as ***
        self.assertNotIn("secret_token_val", r["cmd"])
        self.assertIn("***", r["cmd"])
        self.assertIn("alice", r["cmd"])

        # Also test redaction when 'data' is the secret key
        ctx2 = _make_harmony_ctx()
        r2 = send_broadcast(
            ctx2,
            "usual.event.AUTH2",
            extras={"data": "confidential_data"},
            redact_keys=["data"],
        )
        self.assertNotIn("confidential_data", r2["cmd"])
        self.assertIn("***", r2["cmd"])

    def test_ces_pub_injection_prevention(self):
        """Injection characters in action, package, flags, or extras fail fast."""
        ctx = _make_harmony_ctx()

        # Bad action
        r = send_broadcast(ctx, "event; reboot")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "invalid_param")

        # Bad package
        r = send_broadcast(ctx, "valid.event", package="com.bad; rm -rf /")
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "invalid_param")

        # Bad flags
        r = send_broadcast(ctx, "valid.event", flags=["--flag; dangerous"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "invalid_param")

        # Bad extras key
        r = send_broadcast(ctx, "valid.event", extras={"bad key!": "v"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "invalid_param")

        ctx.driver.shell.assert_not_called()

    def test_ces_pub_wait_ack_seen(self):
        """wait_ack succeeds when ACK log arrives in window."""
        ctx = _make_harmony_ctx(log_lines=["event=CMD_ACK status=ok"])
        r = send_broadcast(
            ctx,
            "usual.event.ORDER",
            wait_ack="event=CMD_ACK",
            ack_timeout_s=0,
        )
        self.assertTrue(r["ok"])
        self.assertTrue(r["ack_seen"])
        self.assertIn("CMD_ACK", r["line"])
        self.assertIsNotNone(ctx.driver.read_log.call_args.kwargs.get("since"))

    def test_ces_pub_old_ack_not_matched(self):
        """When since-window is empty, old ACK is not matched."""
        ctx = _make_harmony_ctx(log_lines=[])
        r = send_broadcast(
            ctx,
            "usual.event.ORDER",
            wait_ack="event=CMD_ACK",
            ack_timeout_s=0,
        )
        self.assertFalse(r["ok"])
        self.assertFalse(r["ack_seen"])
        self.assertEqual(ctx.driver.read_log.call_args.kwargs.get("since"), MARK)

    def test_ces_pub_check_alive_blocks_send_when_dead(self):
        """check_alive=True blocks broadcast when process is dead."""
        ctx = _make_harmony_ctx()
        ctx.driver.shell.side_effect = [
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # pidof fails
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps -ef fails
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps -A fails
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps fails
        ]
        r = send_broadcast(
            ctx,
            "usual.event.TEST",
            package="com.example.harmonyapp",
            check_alive=True,
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "app_not_alive")
        # Ensure 'ces pub' was NEVER sent
        for call in ctx.driver.shell.call_args_list:
            self.assertNotIn("ces pub", call[0][0])

    def test_ces_pub_ack_timeout_dead_vs_alive(self):
        """ACK timeout sub-classifies into app_alive vs app_dead on Harmony."""
        # Case 1: Timeout with app alive
        ctx_alive = _make_harmony_ctx(log_lines=[])
        ctx_alive.driver.shell.side_effect = [
            {"ok": True, "stdout": "", "stderr": "", "returncode": 0},  # ces pub
            {"ok": True, "stdout": "9876", "stderr": "", "returncode": 0},  # pidof -> alive
        ]
        r_alive = send_broadcast(
            ctx_alive,
            "usual.event.TEST",
            package="com.example.harmonyapp",
            wait_ack="ACK_EXPECTED",
            ack_timeout_s=0,
        )
        self.assertFalse(r_alive["ok"])
        self.assertEqual(r_alive["reason"], "ack_timeout_app_alive")

        # Case 2: Timeout with app dead
        ctx_dead = _make_harmony_ctx(log_lines=[])
        ctx_dead.driver.shell.side_effect = [
            {"ok": True, "stdout": "", "stderr": "", "returncode": 0},  # ces pub
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # pidof dead
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps -ef dead
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps -A dead
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps dead
        ]
        r_dead = send_broadcast(
            ctx_dead,
            "usual.event.TEST",
            package="com.example.harmonyapp",
            wait_ack="ACK_EXPECTED",
            ack_timeout_s=0,
        )
        self.assertFalse(r_dead["ok"])
        self.assertEqual(r_dead["reason"], "ack_timeout_app_dead")


class TestHarmonyEnsureAppAlive(unittest.TestCase):
    """Test ensure_app_alive on platform='harmony'."""

    def test_already_alive_returns_immediately(self):
        """App already alive skips launch and ready wait."""
        ctx = _make_harmony_ctx()
        ctx.driver.shell.return_value = {
            "ok": True,
            "stdout": "54321",
            "stderr": "",
            "returncode": 0,
        }
        r = ensure_app_alive(
            ctx,
            "com.example.harmonyapp",
            scheme_url="harmony://open",
            ready_event="APP_READY",
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["pid"], 54321)
        self.assertFalse(r["launched"])
        self.assertIsNone(r["ready_seen"])
        ctx.driver.open_deeplink.assert_not_called()
        ctx.driver.read_log.assert_not_called()

    def test_dead_app_cold_launched_and_waits_ready(self):
        """Dead app is launched via open_deeplink and waits for ready_event."""
        ctx = _make_harmony_ctx(log_lines=["event=APP_READY version=1.0"])
        ctx.driver.shell.side_effect = [
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # pidof dead
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps -ef dead
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps -A dead
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # ps dead
            {"ok": True, "stdout": "9999", "stderr": "", "returncode": 0},  # final pid check
        ]
        r = ensure_app_alive(
            ctx,
            "com.example.harmonyapp",
            scheme_url="harmony://start",
            ready_event="event=APP_READY",
            timeout_s=0,
        )
        self.assertTrue(r["ok"])
        self.assertTrue(r["launched"])
        self.assertTrue(r["ready_seen"])
        self.assertEqual(r["pid"], 9999)
        ctx.driver.open_deeplink.assert_called_once_with("harmony://start")

    def test_force_restart_calls_force_stop_and_clear_log(self):
        """force_restart=True kills app, clears log, and relaunches."""
        ctx = _make_harmony_ctx(log_lines=["event=APP_READY"])
        ctx.driver.shell.side_effect = [
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},  # dead after stop
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},
            {"ok": False, "stdout": "", "stderr": "", "returncode": 1},
            {"ok": True, "stdout": "8888", "stderr": "", "returncode": 0},  # post-launch pid
        ]
        r = ensure_app_alive(
            ctx,
            "com.example.harmonyapp",
            scheme_url="harmony://restart",
            ready_event="event=APP_READY",
            force_restart=True,
            timeout_s=0,
        )
        self.assertTrue(r["ok"])
        self.assertTrue(r["launched"])
        ctx.driver.app.force_stop.assert_called_once_with("com.example.harmonyapp")
        ctx.driver.clear_log.assert_called_once()
        ctx.driver.open_deeplink.assert_called_once_with("harmony://restart")

    def test_pid_of_harmony_ps_ef_parsing(self):
        """_pid_of parses standard ps -ef output on HarmonyOS."""
        ctx = _make_harmony_ctx()
        ps_ef_out = (
            "UID        PID  PPID  C STIME TTY          TIME CMD\n"
            "root         1     0  0 00:00 ?        00:00:01 /init\n"
            "10024     7654     1  0 10:15 ?        00:00:02 com.example.harmonyapp\n"
        )
        ctx.driver.shell.side_effect = [
            {"ok": False, "stdout": "", "stderr": "pidof: not found", "returncode": 127},
            {"ok": True, "stdout": ps_ef_out, "stderr": "", "returncode": 0},
        ]
        pid = _pid_of(ctx, "com.example.harmonyapp")
        self.assertEqual(pid, 7654)


if __name__ == "__main__":
    unittest.main()
