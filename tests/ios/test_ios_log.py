"""
Tests for iOS log capture (design §4.6 start/stop_log_capture + device/log_capture.py).
Subprocess mocked; covers the output_path → process mapping (mirrors Harmony).
"""

from unittest.mock import MagicMock, patch

from phone_pilot.ios.device import log_capture
from phone_pilot.ios.driver import iOSDriver


SERIAL = "00008030-001234560A80802E"


class TestLogCaptureModule:
    def setup_method(self):
        log_capture._log_captures.clear()

    def teardown_method(self):
        log_capture._log_captures.clear()

    def test_start_registers_process_mapping(self, tmp_path):
        out = str(tmp_path / "log.txt")
        fake_proc = MagicMock()
        fake_proc.pid = 4321
        with (
            patch(
                "phone_pilot.ios.device.log_capture.pmd_utils.detect_form_factor",
                return_value="real",
            ),
            patch(
                "phone_pilot.ios.device.log_capture.pmd_utils.pmd_executable",
                return_value="pmd",
            ),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            res = log_capture.start_log_capture(SERIAL, out)
        assert res["ok"] is True
        assert res["pid"] == 4321
        assert out in log_capture._log_captures

    def test_start_twice_same_path_errors(self, tmp_path):
        out = str(tmp_path / "log.txt")
        fake_proc = MagicMock()
        fake_proc.pid = 1
        with (
            patch(
                "phone_pilot.ios.device.log_capture.pmd_utils.detect_form_factor",
                return_value="simulator",
            ),
            patch(
                "phone_pilot.ios.device.log_capture.pmd_utils.xcrun_executable",
                return_value="xcrun",
            ),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            log_capture.start_log_capture(SERIAL, out)
            res2 = log_capture.start_log_capture(SERIAL, out)
        assert res2["ok"] is False

    def test_stop_unknown_path_errors(self):
        res = log_capture.stop_log_capture(SERIAL, "/no/such/path")
        assert res["ok"] is False

    def test_stop_terminates_and_counts_lines(self, tmp_path):
        out = tmp_path / "log.txt"
        out.write_text("line1\nline2\nline3\n", encoding="utf-8")
        fake_proc = MagicMock()
        log_capture._log_captures[str(out)] = {"proc": fake_proc, "fh": MagicMock()}
        res = log_capture.stop_log_capture(SERIAL, str(out))
        assert res["ok"] is True
        assert res["lines"] == 3
        fake_proc.terminate.assert_called_once()
        assert str(out) not in log_capture._log_captures

    def test_stop_tolerates_already_exited(self, tmp_path):
        out = tmp_path / "log.txt"
        out.write_text("x\n", encoding="utf-8")
        fake_proc = MagicMock()
        fake_proc.terminate.side_effect = ProcessLookupError()
        log_capture._log_captures[str(out)] = {"proc": fake_proc, "fh": MagicMock()}
        res = log_capture.stop_log_capture(SERIAL, str(out))
        assert res["ok"] is True


class TestDriverLogEntries:
    def test_clear_log_not_supported(self):
        res = iOSDriver(SERIAL).clear_log()
        assert res["ok"] is False
        assert res["error"] == "not_supported"

    def test_start_stop_log_capture_delegate(self, tmp_path):
        out = str(tmp_path / "d.txt")
        drv = iOSDriver(SERIAL)
        with patch(
            "phone_pilot.ios.driver.log_capture.start_log_capture",
            return_value={"ok": True, "pid": 9, "path": out},
        ) as start:
            res = drv.start_log_capture(out, tags="MyApp", level="I")
        assert res["ok"] is True
        start.assert_called_once()
        with patch(
            "phone_pilot.ios.driver.log_capture.stop_log_capture",
            return_value={"ok": True, "path": out, "lines": 0},
        ) as stop:
            res2 = drv.stop_log_capture(out)
        assert res2["ok"] is True
        stop.assert_called_once()
