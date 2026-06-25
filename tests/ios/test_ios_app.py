"""
Tests for iOSAppDriver (design §4.4). pymobiledevice3 runner mocked.
"""

import subprocess
from unittest.mock import patch

import pytest

from phone_pilot.ios.driver import iOSAppDriver


SERIAL = "00008030-001234560A80802E"


@pytest.fixture(autouse=True)
def _force_real_device():
    """These tests target the real-device (pymobiledevice3) path; pin form factor
    to 'real' so detect_form_factor doesn't shell out to simctl/usbmux."""
    with patch(
        "phone_pilot.ios.driver.pmd_utils.detect_form_factor", return_value="real"
    ):
        yield


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["mock"], returncode, stdout, stderr)


class TestListPackages:
    def test_parses_json_bundle_ids(self):
        payload = '{"com.example.a": {}, "com.example.b": {}}'
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(stdout=payload),
            ),
        ):
            pkgs = iOSAppDriver(SERIAL).list_packages()
        assert "com.example.a" in pkgs and "com.example.b" in pkgs

    def test_empty_on_failure(self):
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run", side_effect=FileNotFoundError()
            ),
        ):
            assert iOSAppDriver(SERIAL).list_packages() == []


class TestLaunchForceStop:
    def test_launch_app_ok(self):
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(returncode=0),
            ),
        ):
            res = iOSAppDriver(SERIAL).launch_app("com.example.a")
        assert res["ok"] is True
        assert res["package"] == "com.example.a"

    def test_launch_app_ignores_activity(self):
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(returncode=0),
            ),
        ):
            res = iOSAppDriver(SERIAL).launch_app("com.example.a", activity="ignored")
        assert res["ok"] is True

    def test_force_stop_ok(self):
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(returncode=0),
            ),
        ):
            res = iOSAppDriver(SERIAL).force_stop("com.example.a")
        assert res["ok"] is True

    def test_launch_app_nonzero_returns_error(self):
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(returncode=1, stderr="boom"),
            ),
        ):
            res = iOSAppDriver(SERIAL).launch_app("com.example.a")
        assert res["ok"] is False


class TestNotSupported:
    def test_clear_data_not_supported(self):
        res = iOSAppDriver(SERIAL).clear_data("com.example.a")
        assert res["ok"] is False
        assert res["error"] == "not_supported"

    def test_uninstall_ok(self):
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(returncode=0),
            ),
        ):
            res = iOSAppDriver(SERIAL).uninstall("com.example.a")
        assert res["ok"] is True
