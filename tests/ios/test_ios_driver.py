"""
Tests for iOSDriver composite + device-level 17 entries (design §4.5/§4.6).
WDA client + pmd runner mocked.
"""

import subprocess
from unittest.mock import MagicMock, patch

from phone_pilot.ios.driver import (
    iOSDriver,
    iOSInputDriver,
    iOSScreenDriver,
    iOSUIDriver,
    iOSAppDriver,
)


SERIAL = "00008030-001234560A80802E"


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["mock"], returncode, stdout, stderr)


def _patch_wda(client):
    return patch(
        "phone_pilot.ios.driver._wda_call",
        side_effect=lambda serial, fn: fn(client),
    )


class TestComposite:
    def test_subdriver_property_types(self):
        drv = iOSDriver(SERIAL)
        assert isinstance(drv.input, iOSInputDriver)
        assert isinstance(drv.screen, iOSScreenDriver)
        assert isinstance(drv.ui, iOSUIDriver)
        assert isinstance(drv.app, iOSAppDriver)

    def test_identity(self):
        drv = iOSDriver(SERIAL)
        assert drv.platform == "ios"
        assert drv.device_serial == SERIAL

    def test_empty_serial_raises(self):
        import pytest

        with pytest.raises(ValueError, match="device_serial"):
            iOSDriver("")


class TestNavigation:
    def test_go_home(self):
        client = MagicMock()
        with _patch_wda(client):
            res = iOSDriver(SERIAL).go_home()
        assert res["ok"] is True
        client.home.assert_called_once()

    def test_lock_screen(self):
        client = MagicMock()
        with _patch_wda(client):
            res = iOSDriver(SERIAL).lock_screen()
        assert res["ok"] is True
        client.lock.assert_called_once()

    def test_unlock(self):
        client = MagicMock()
        with _patch_wda(client):
            res = iOSDriver(SERIAL).unlock()
        assert res["ok"] is True
        client.unlock.assert_called_once()


class TestGoBackDegraded:
    def test_go_back_state_changed_ok(self):
        """Activity changes after edge-swipe → plain ok."""
        client = MagicMock()
        # app_current returns different bundle before/after the swipe
        client.app_current.side_effect = [
            {"bundleId": "com.a"},
            {"bundleId": "com.b"},
        ]
        with _patch_wda(client):
            res = iOSDriver(SERIAL).go_back()
        assert res["ok"] is True
        assert "warning" not in res

    def test_go_back_state_unchanged_warns(self):
        """No change after edge-swipe → ok with warning (Plan T9b)."""
        client = MagicMock()
        client.app_current.side_effect = [
            {"bundleId": "com.a"},
            {"bundleId": "com.a"},
        ]
        with _patch_wda(client):
            res = iOSDriver(SERIAL).go_back()
        assert res["ok"] is True
        assert res["warning"] == "go_back_may_not_have_worked"


class TestNotSupportedEntries:
    def test_dump_memory_profile_not_supported(self):
        res = iOSDriver(SERIAL).dump_memory_profile("com.a")
        assert res["ok"] is False and res["error"] == "not_supported"

    def test_clear_background_not_supported(self):
        res = iOSDriver(SERIAL).clear_background()
        assert res["ok"] is False and res["error"] == "not_supported"

    def test_clear_log_not_supported(self):
        res = iOSDriver(SERIAL).clear_log()
        assert res["ok"] is False and res["error"] == "not_supported"


class TestDeviceInfo:
    def test_device_info_parses_lockdown(self):
        payload = (
            '{"ProductType": "iPhone14,2", "ProductVersion": "17.0", '
            '"DeviceName": "TestPhone"}'
        )
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.detect_form_factor",
                return_value="real",
            ),
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(stdout=payload),
            ),
        ):
            res = iOSDriver(SERIAL).device_info()
        assert res["ok"] is True
        assert res["model"] == "iPhone14,2"
        assert res["os_version"] == "17.0"


class TestOpenDeeplink:
    def test_open_deeplink_via_wda(self):
        client = MagicMock()
        with _patch_wda(client):
            res = iOSDriver(SERIAL).open_deeplink("myapp://page")
        assert res["ok"] is True
        client.open_url.assert_called_once_with("myapp://page")


class TestFileTransfer:
    def test_push_file(self, tmp_path):
        local = tmp_path / "f.txt"
        local.write_text("x")
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(returncode=0),
            ),
        ):
            res = iOSDriver(SERIAL).push_file(str(local), "/Documents/f.txt")
        assert res["ok"] is True

    def test_remove_remote_file(self):
        with (
            patch(
                "phone_pilot.ios.driver.pmd_utils.pmd_executable", return_value="pmd"
            ),
            patch(
                "phone_pilot.ios.driver.pmd_runner.run",
                return_value=_completed(returncode=0),
            ),
        ):
            res = iOSDriver(SERIAL).remove_remote_file("/Documents/f.txt")
        assert res["ok"] is True


class TestLaunchFromHome:
    def test_launch_from_home_delegates(self):
        with patch(
            "phone_pilot.ios.driver.finder.launch_from_home",
            return_value={"ok": True, "app_name": "Settings"},
        ) as f:
            res = iOSDriver(SERIAL).launch_from_home("Settings")
        assert res["ok"] is True
        f.assert_called_once()
