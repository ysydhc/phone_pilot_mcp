"""
Tests for phone_pilot.ios.pmd — device discovery, form-factor, tunnel probe, UDID.

All subprocess calls are mocked; no real device / pymobiledevice3 / Xcode needed.
"""

import subprocess
from unittest.mock import patch

import pytest

from phone_pilot.ios.pmd import utils
from phone_pilot.ios.pmd import runner


# Two real iOS UDID formats (design §4 / Plan T4b)
UDID_40HEX = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
UDID_UUID = "00008030-001234560A80802E"
SIM_UDID = "11111111-2222-3333-4444-555555555555"


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["mock"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ---------------------------------------------------------------------------
# is_valid_udid (Plan T4b)
# ---------------------------------------------------------------------------


class TestIsValidUdid:
    def test_accepts_40hex(self):
        assert utils.is_valid_udid(UDID_40HEX) is True

    def test_accepts_uuid_style(self):
        assert utils.is_valid_udid(UDID_UUID) is True

    def test_rejects_garbage(self):
        assert utils.is_valid_udid("not-a-udid") is False
        assert utils.is_valid_udid("") is False
        assert utils.is_valid_udid("12345") is False


# ---------------------------------------------------------------------------
# detect_form_factor (Plan T10)
# ---------------------------------------------------------------------------


class TestDetectFormFactor:
    def test_simulator_when_in_booted_list(self):
        with (
            patch.object(utils, "_list_simulator_serials", return_value=[SIM_UDID]),
            patch.object(utils, "_list_real_serials", return_value=[]),
        ):
            assert utils.detect_form_factor(SIM_UDID) == "simulator"

    def test_real_when_in_usbmux_list(self):
        with (
            patch.object(utils, "_list_simulator_serials", return_value=[]),
            patch.object(utils, "_list_real_serials", return_value=[UDID_UUID]),
        ):
            assert utils.detect_form_factor(UDID_UUID) == "real"

    def test_unknown_when_absent(self):
        with (
            patch.object(utils, "_list_simulator_serials", return_value=[]),
            patch.object(utils, "_list_real_serials", return_value=[]),
        ):
            assert utils.detect_form_factor("ghost") == "unknown"

    def test_simulator_takes_precedence(self):
        # If a UDID somehow appears in both, simulator wins (design ADR-6 order).
        with (
            patch.object(utils, "_list_simulator_serials", return_value=[SIM_UDID]),
            patch.object(utils, "_list_real_serials", return_value=[SIM_UDID]),
        ):
            assert utils.detect_form_factor(SIM_UDID) == "simulator"


# ---------------------------------------------------------------------------
# probe_tunnel (Plan T7)
# ---------------------------------------------------------------------------


class TestProbeTunnel:
    def test_simulator_needs_no_tunnel(self):
        with patch.object(utils, "detect_form_factor", return_value="simulator"):
            res = utils.probe_tunnel(SIM_UDID)
        assert res["ok"] is True
        assert res.get("form_factor") == "simulator"

    def test_real_tunnel_present_ok(self):
        with (
            patch.object(utils, "detect_form_factor", return_value="real"),
            patch.object(utils, "pmd_executable", return_value="pymobiledevice3"),
            patch.object(
                runner, "run", return_value=_completed(stdout=UDID_UUID, returncode=0)
            ),
        ):
            res = utils.probe_tunnel(UDID_UUID)
        assert res["ok"] is True

    def test_real_tunnel_missing_returns_diagnostic(self):
        with (
            patch.object(utils, "detect_form_factor", return_value="real"),
            patch.object(utils, "pmd_executable", return_value="pymobiledevice3"),
            patch.object(
                runner, "run", return_value=_completed(stdout="", returncode=1)
            ),
        ):
            res = utils.probe_tunnel(UDID_UUID)
        assert res["ok"] is False
        assert res["error"] == "ios_tunnel_unavailable"
        assert "tunneld" in res.get("hint", "")

    def test_real_tunnel_probe_exception_returns_diagnostic(self):
        with (
            patch.object(utils, "detect_form_factor", return_value="real"),
            patch.object(utils, "pmd_executable", return_value="pymobiledevice3"),
            patch.object(
                runner, "run", side_effect=FileNotFoundError("pymobiledevice3")
            ),
        ):
            res = utils.probe_tunnel(UDID_UUID)
        assert res["ok"] is False
        assert res["error"] == "ios_tunnel_unavailable"


# ---------------------------------------------------------------------------
# list_ios_serials (merges real + simulator)
# ---------------------------------------------------------------------------


class TestListIosSerials:
    def test_merges_real_and_simulator(self):
        with (
            patch.object(utils, "_list_real_serials", return_value=[UDID_UUID]),
            patch.object(utils, "_list_simulator_serials", return_value=[SIM_UDID]),
        ):
            serials = utils.list_ios_serials()
        assert UDID_UUID in serials
        assert SIM_UDID in serials

    def test_empty_when_none_connected(self):
        with (
            patch.object(utils, "_list_real_serials", return_value=[]),
            patch.object(utils, "_list_simulator_serials", return_value=[]),
        ):
            assert utils.list_ios_serials() == []

    def test_real_serials_parses_json(self):
        payload = '[{"Identifier": "%s"}, {"Identifier": "%s"}]' % (
            UDID_UUID,
            UDID_40HEX,
        )
        with (
            patch.object(utils, "pmd_executable", return_value="pymobiledevice3"),
            patch.object(
                runner, "run", return_value=_completed(stdout=payload, returncode=0)
            ),
        ):
            serials = utils._list_real_serials()
        assert UDID_UUID in serials
        assert UDID_40HEX in serials

    def test_real_serials_falls_back_to_regex_on_nonjson(self):
        # Non-JSON table output → regex scan for UDID-shaped tokens.
        text = f"Device found: {UDID_UUID} (iPhone)\nanother line"
        with (
            patch.object(utils, "pmd_executable", return_value="pymobiledevice3"),
            patch.object(
                runner, "run", return_value=_completed(stdout=text, returncode=0)
            ),
        ):
            serials = utils._list_real_serials()
        assert UDID_UUID in serials

    def test_real_serials_empty_on_failure(self):
        with (
            patch.object(utils, "pmd_executable", return_value="pymobiledevice3"),
            patch.object(runner, "run", side_effect=FileNotFoundError()),
        ):
            assert utils._list_real_serials() == []

    def test_simulator_serials_parses_simctl_json(self):
        payload = (
            '{"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-17-0": '
            '[{"udid": "%s", "state": "Booted"}, '
            '{"udid": "99999999-0000-0000-0000-000000000000", "state": "Shutdown"}]}}'
        ) % SIM_UDID
        with (
            patch.object(utils, "xcrun_executable", return_value="xcrun"),
            patch.object(
                runner, "run", return_value=_completed(stdout=payload, returncode=0)
            ),
        ):
            serials = utils._list_simulator_serials()
        assert SIM_UDID in serials
        # Only booted devices are returned
        assert "99999999-0000-0000-0000-000000000000" not in serials

    def test_simulator_serials_empty_on_failure(self):
        with (
            patch.object(utils, "xcrun_executable", return_value="xcrun"),
            patch.object(runner, "run", side_effect=FileNotFoundError()),
        ):
            assert utils._list_simulator_serials() == []


# ---------------------------------------------------------------------------
# pmd_executable / xcrun_executable
# ---------------------------------------------------------------------------


class TestExecutableResolution:
    def test_pmd_executable_from_env(self, monkeypatch):
        monkeypatch.setenv("PYMOBILEDEVICE3_PATH", "/custom/pymobiledevice3")
        utils._PMD_CACHED = None
        assert utils.pmd_executable() == "/custom/pymobiledevice3"
        utils._PMD_CACHED = None

    def test_pmd_executable_from_which(self, monkeypatch):
        monkeypatch.delenv("PYMOBILEDEVICE3_PATH", raising=False)
        utils._PMD_CACHED = None
        with patch("shutil.which", return_value="/usr/local/bin/pymobiledevice3"):
            assert utils.pmd_executable() == "/usr/local/bin/pymobiledevice3"
        utils._PMD_CACHED = None

    def test_pmd_executable_not_found_raises_friendly(self, monkeypatch):
        monkeypatch.delenv("PYMOBILEDEVICE3_PATH", raising=False)
        utils._PMD_CACHED = None
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="phone-pilot\\[ios\\]"):
                utils.pmd_executable()
        utils._PMD_CACHED = None

    def test_xcrun_executable_from_which(self):
        utils._XCRUN_CACHED = None
        with patch("shutil.which", return_value="/usr/bin/xcrun"):
            assert utils.xcrun_executable() == "/usr/bin/xcrun"
        utils._XCRUN_CACHED = None


# ---------------------------------------------------------------------------
# runner.run
# ---------------------------------------------------------------------------


class TestRunner:
    def test_run_returns_completed_process(self):
        with patch(
            "subprocess.run", return_value=_completed(stdout="hi", returncode=0)
        ) as m:
            proc = runner.run(["echo", "hi"])
        assert proc.returncode == 0
        assert proc.stdout == "hi"
        m.assert_called_once()

    def test_run_timeout_returns_124(self):
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)
        ):
            proc = runner.run(["sleep", "100"], timeout_s=1)
        assert proc.returncode == 124

    def test_run_does_not_raise_on_nonzero(self):
        with patch("subprocess.run", return_value=_completed(returncode=2)):
            proc = runner.run(["false"])
        assert proc.returncode == 2
