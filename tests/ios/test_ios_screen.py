"""
Tests for iOSScreenDriver (design §4.2). WDA client + pmd runner mocked.
"""

import base64
from unittest.mock import MagicMock, patch

from phone_pilot.ios.driver import iOSScreenDriver


SERIAL = "00008030-001234560A80802E"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _patch_bridge(client):
    return patch(
        "phone_pilot.ios.driver._wda_call",
        side_effect=lambda serial, fn: fn(client),
    )


class TestScreenshot:
    def test_screenshot_returns_png_bytes(self):
        client = MagicMock()
        client.screenshot.return_value = base64.b64encode(PNG).decode()
        with _patch_bridge(client):
            data = iOSScreenDriver(SERIAL).screenshot()
        assert isinstance(data, bytes)
        assert data[:4] == b"\x89PNG"

    def test_screenshot_accepts_raw_bytes(self):
        client = MagicMock()
        client.screenshot.return_value = PNG  # already bytes
        with _patch_bridge(client):
            data = iOSScreenDriver(SERIAL).screenshot()
        assert data[:4] == b"\x89PNG"

    def test_screenshot_fallback_simulator_when_wda_fails(self):
        import pathlib
        import subprocess

        def fake_run(cmd, **kw):
            # cmd: ["xcrun", "simctl", "io", SERIAL, "screenshot", tmp_path]
            pathlib.Path(cmd[-1]).write_bytes(PNG)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            patch(
                "phone_pilot.ios.driver._wda_call",
                return_value={"ok": False, "error": "wda_connection_refused"},
            ),
            patch(
                "phone_pilot.ios.driver.pmd_utils.detect_form_factor",
                return_value="simulator",
            ),
            patch(
                "phone_pilot.ios.driver.pmd_utils.simctl_prefix",
                return_value=["xcrun", "simctl"],
            ),
            patch("phone_pilot.ios.driver.pmd_runner.run", side_effect=fake_run) as m_run,
        ):
            data = iOSScreenDriver(SERIAL).screenshot()

        assert data == PNG
        cmd_used = m_run.call_args.args[0]
        assert cmd_used[:4] == ["xcrun", "simctl", "io", SERIAL]
        assert cmd_used[4] == "screenshot"

    def test_screenshot_fallback_real_device_when_wda_returns_empty(self):
        import pathlib
        import subprocess

        def fake_run(cmd, **kw):
            # cmd: ["pmd", "developer", "dvt", "screenshot", tmp_path, "--udid", SERIAL]
            pathlib.Path(cmd[4]).write_bytes(PNG)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with (
            patch("phone_pilot.ios.driver._wda_call", return_value=b""),
            patch(
                "phone_pilot.ios.driver.pmd_utils.detect_form_factor",
                return_value="real",
            ),
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable",
                return_value="pmd",
            ),
            patch("phone_pilot.ios.driver.pmd_runner.run", side_effect=fake_run) as m_run,
        ):
            data = iOSScreenDriver(SERIAL).screenshot()

        assert data == PNG
        cmd_used = m_run.call_args.args[0]
        assert cmd_used[:4] == ["pmd", "developer", "dvt", "screenshot"]
        assert cmd_used[-2:] == ["--udid", SERIAL]


class TestGetScreenSize:
    def test_applies_scale(self):
        """WDA points × scale → pixels (Plan T11). 390x844 @3 → 1170x2532."""
        client = MagicMock()
        client.window_size.return_value = (390, 844)
        client.scale = 3
        with _patch_bridge(client):
            size = iOSScreenDriver(SERIAL).get_screen_size()
        assert size == (1170, 2532)

    def test_scale_default_1_when_missing(self):
        client = MagicMock()
        client.window_size.return_value = (320, 480)
        # no scale attribute → default 1
        del client.scale
        with _patch_bridge(client):
            size = iOSScreenDriver(SERIAL).get_screen_size()
        assert size == (320, 480)

    def test_caches_result(self):
        client = MagicMock()
        client.window_size.return_value = (100, 200)
        client.scale = 1
        drv = iOSScreenDriver(SERIAL)
        with _patch_bridge(client):
            drv.get_screen_size()
            drv.get_screen_size()
        # second call served from cache → window_size called once
        assert client.window_size.call_count == 1


class TestScreenrecord:
    def test_start_real_device_not_supported(self):
        with patch(
            "phone_pilot.ios.driver.pmd_utils.detect_form_factor", return_value="real"
        ):
            res = iOSScreenDriver(SERIAL).start_screenrecord("/tmp/x.mp4")
        assert res["ok"] is False
        assert res["error"] == "not_supported"

    def test_start_simulator_spawns_process(self):
        fake_proc = MagicMock()
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.detect_form_factor",
                return_value="simulator",
            ),
            patch(
                "phone_pilot.ios.driver.pmd_utils.xcrun_executable",
                return_value="xcrun",
            ),
            patch("subprocess.Popen", return_value=fake_proc) as popen,
        ):
            res = iOSScreenDriver(SERIAL).start_screenrecord("/tmp/rec.mp4")
        assert res["ok"] is True
        popen.assert_called_once()

    def test_stop_already_exited_does_not_raise(self):
        """Plan T13: process already exited → SIGINT must not raise, still returns path."""
        drv = iOSScreenDriver(SERIAL)
        fake_proc = MagicMock()
        # simctl recordVideo finalizes on SIGINT; if the process is gone it raises.
        fake_proc.send_signal.side_effect = ProcessLookupError()
        drv._record_proc = fake_proc
        drv._record_path = "/tmp/rec.mp4"
        res = drv.stop_screenrecord()
        assert res["ok"] is True
        assert res["path"] == "/tmp/rec.mp4"

    def test_stop_sends_sigint_to_finalize(self):
        """Normal stop must SIGINT (not SIGTERM) so the MP4 moov atom is written."""
        import signal

        drv = iOSScreenDriver(SERIAL)
        fake_proc = MagicMock()
        drv._record_proc = fake_proc
        drv._record_path = "/tmp/rec.mp4"
        res = drv.stop_screenrecord()
        assert res["ok"] is True
        fake_proc.send_signal.assert_called_once_with(signal.SIGINT)

    def test_stop_without_active_recording(self):
        res = iOSScreenDriver(SERIAL).stop_screenrecord()
        assert res["ok"] is False
