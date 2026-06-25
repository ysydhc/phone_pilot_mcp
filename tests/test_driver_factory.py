"""Tests for phone_pilot.core.driver_factory."""

from unittest.mock import patch

import pytest

from phone_pilot.core import driver_factory
from phone_pilot.core.driver_factory import create_driver


class TestCreateDriver:
    def test_android_creates_android_driver(self):
        drv = create_driver("fake_serial", platform="android")
        assert drv.platform == "android"
        assert drv.device_serial == "fake_serial"

    def test_harmony_creates_harmony_driver(self):
        drv = create_driver("fake_serial", platform="harmony")
        assert drv.platform == "harmony"
        assert drv.device_serial == "fake_serial"

    def test_unknown_platform_raises(self):
        with pytest.raises(ValueError, match="Unknown platform"):
            create_driver("s", platform="foobar")

    def test_ios_creates_ios_driver(self):
        drv = create_driver("s", platform="ios")
        assert drv.platform == "ios"
        assert drv.device_serial == "s"

    def test_empty_serial_raises(self):
        with pytest.raises(ValueError, match="device_serial"):
            create_driver("", platform="android")


class TestIosDetection:
    UDID = "00008030-001234560A80802E"

    def test_auto_detect_routes_to_ios(self):
        with (
            patch.object(driver_factory, "_is_adb_device", return_value=False),
            patch.object(driver_factory, "_is_hdc_device", return_value=False),
            patch.object(driver_factory, "_is_ios_device", return_value=True),
        ):
            drv = driver_factory._auto_detect_driver(self.UDID)
        assert drv.platform == "ios"
        assert drv.device_serial == self.UDID

    def test_detect_device_finds_ios_after_adb_hdc(self):
        with (
            patch.object(driver_factory, "_first_adb_device", return_value=None),
            patch.object(driver_factory, "_first_hdc_device", return_value=None),
            patch.object(driver_factory, "_first_ios_device", return_value=self.UDID),
        ):
            serial, plat = driver_factory.detect_device()
        assert serial == self.UDID
        assert plat == "ios"

    def test_first_ios_device_empty_on_failure(self):
        with patch(
            "phone_pilot.ios.pmd.utils.list_ios_serials", side_effect=RuntimeError()
        ):
            assert driver_factory._first_ios_device() is None


class TestDriverProtocolConformance:
    """Verify both drivers expose Protocol-required attributes."""

    @pytest.fixture(params=["android", "harmony", "ios"])
    def driver(self, request):
        return create_driver("test_serial", platform=request.param)

    def test_has_input(self, driver):
        assert hasattr(driver, "input")
        inp = driver.input
        for method in ("tap", "swipe", "long_press", "input_text", "keyevent"):
            assert callable(getattr(inp, method))

    def test_has_screen(self, driver):
        assert hasattr(driver, "screen")
        scr = driver.screen
        for method in (
            "screenshot",
            "get_screen_size",
            "start_screenrecord",
            "stop_screenrecord",
        ):
            assert callable(getattr(scr, method))

    def test_has_ui(self, driver):
        assert hasattr(driver, "ui")
        ui = driver.ui
        for method in ("dump_ui_hierarchy", "find_elements", "get_current_activity"):
            assert callable(getattr(ui, method))

    def test_has_app(self, driver):
        assert hasattr(driver, "app")
        app = driver.app
        for method in ("list_packages", "launch_app", "force_stop", "clear_data"):
            assert callable(getattr(app, method))

    def test_has_device_control(self, driver):
        for method in (
            "go_home",
            "go_back",
            "unlock",
            "lock_screen",
            "clear_background",
            "device_info",
        ):
            assert callable(getattr(driver, method)), f"Missing {method}"

    def test_has_log(self, driver):
        for method in ("read_log", "clear_log", "dump_log"):
            assert callable(getattr(driver, method)), f"Missing {method}"

    def test_platform_identity(self, driver):
        assert driver.platform in ("android", "harmony", "ios")
        assert driver.device_serial == "test_serial"
