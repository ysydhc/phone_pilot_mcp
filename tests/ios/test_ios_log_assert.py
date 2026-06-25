"""
Tests for iOS log time-point assertion support (logcat_time + read_log since/tag_filter)
and the recording flow contract. Subprocess mocked; no real device.
"""

import subprocess
from unittest.mock import patch

from phone_pilot.ios.driver import iOSDriver


SERIAL = "00008030-001234560A80802E"

# Representative iOS `log show --style syslog` output (two lines).
IOS_LOG = (
    "Timestamp                       (process)[PID]\n"
    "2026-06-25 12:07:06.960454+0800  localhost Maps[86]: [com.apple.Maps] hello\n"
    "2026-06-25 12:07:07.007393+0800  localhost Maps[86]: [com.apple.Maps] world\n"
)


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["mock"], returncode, stdout, stderr)


def _sim():
    return patch(
        "phone_pilot.ios.driver.pmd_utils.detect_form_factor", return_value="simulator"
    )


def _simctl_prefix():
    return patch(
        "phone_pilot.ios.driver.pmd_utils.simctl_prefix", return_value=["simctl"]
    )


class TestLogcatTime:
    def test_logcat_time_returns_ios_timestamp(self):
        drv = iOSDriver(SERIAL)
        ts = drv.logcat_time()
        # iOS marker format: "YYYY-MM-DD HH:MM:SS" (date portion present)
        assert isinstance(ts, str)
        assert len(ts) >= 19
        assert ts[4] == "-" and ts[7] == "-"


class TestReadLogSince:
    def test_read_log_since_uses_start_flag(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return _completed(stdout=IOS_LOG)

        with (
            _sim(),
            _simctl_prefix(),
            patch("phone_pilot.ios.driver.pmd_runner.run", side_effect=fake_run),
        ):
            out = iOSDriver(SERIAL).read_log(since="2026-06-25 12:07:00")
        assert "world" in out
        # must pass --start with the since value
        assert "--start" in captured["cmd"]
        assert "2026-06-25 12:07:00" in captured["cmd"]

    def test_read_log_tag_filter_filters_lines(self):
        log = (
            "2026-06-25 12:07:06.96+0800 localhost Maps[86]: AAA hit\n"
            "2026-06-25 12:07:07.00+0800 localhost Maps[86]: BBB miss\n"
        )
        with (
            _sim(),
            _simctl_prefix(),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(stdout=log),
            ),
        ):
            out = iOSDriver(SERIAL).read_log(tag_filter="AAA")
        assert "AAA" in out
        assert "BBB" not in out

    def test_read_log_lines_tail(self):
        with (
            _sim(),
            _simctl_prefix(),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(stdout=IOS_LOG),
            ),
        ):
            out = iOSDriver(SERIAL).read_log(lines=1)
        # only the last line retained
        assert out.strip().endswith("world")
        assert "hello" not in out


class TestLogcatAssertSequenceIntegration:
    """logcat_assert_sequence should work over the iOS driver via ScriptContext."""

    def test_sequence_matches_in_order(self):
        from phone_pilot.script_api.logcat import logcat_assert_sequence
        from phone_pilot.script_api.context import ScriptContext

        seq_log = (
            "2026-06-25 12:07:06.96+0800 localhost App[1]: STEP_A done\n"
            "2026-06-25 12:07:06.98+0800 localhost App[1]: noise\n"
            "2026-06-25 12:07:07.00+0800 localhost App[1]: STEP_B done\n"
        )
        drv = iOSDriver(SERIAL)
        with (
            _sim(),
            _simctl_prefix(),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(stdout=seq_log),
            ),
        ):
            ctx = ScriptContext(
                device_serial=SERIAL,
                platform="ios",
                auto_log=False,
                auto_screenshot=False,
                auto_report=False,
                popup_guard=False,
                _driver=drv,
            )
            res = logcat_assert_sequence(
                ctx, ["STEP_A", "STEP_B"], timeout_s=2, interval_s=0.1
            )
        assert res["ok"] is True
        assert res["matched"] == 2


class TestRecordingFlow:
    def test_start_screenrecord_reports_local_no_remote(self):
        """On simulator, recording writes a host-side file (no remote pull needed)."""
        fake_proc = type(
            "P",
            (),
            {
                "pid": 1,
                "terminate": lambda self: None,
                "wait": lambda self, timeout=0: None,
            },
        )()
        with (
            _sim(),
            _simctl_prefix(),
            patch("subprocess.Popen", return_value=fake_proc),
        ):
            r = iOSDriver(SERIAL).screen.start_screenrecord("/tmp/r.mp4")
        assert r["ok"] is True
        # iOS records to the host path directly; engine is simctl
        assert r.get("engine") == "simctl"
        assert r.get("path") == "/tmp/r.mp4"
