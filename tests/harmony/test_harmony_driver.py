"""
Tests for the HarmonyOS driver layer (with mocked hmdriver2).
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.harmony.driver import (
    HarmonyDriver,
    _resolve_keycode,
    _element_info_to_dict,
)


class MockHmDriver:
    """Mocked hmdriver2.driver.Driver."""

    def __init__(self, serial):
        self.serial = serial
        self.display_size = (1080, 2340)
        self.device_info = MagicMock(
            productName="MockProduct",
            model="MockModel",
            sdkVersion="12",
            sysVersion="4.0.0",
            cpuAbi="arm64-v8a",
            wlanIp="192.168.1.100",
            displaySize=(1080, 2340),
            displayRotation="ROTATION_0",
        )
        self.screenrecord = MagicMock()
        self.hdc = MagicMock()

    def click(self, x, y):
        pass

    def swipe(self, x1, y1, x2, y2, speed=1000):
        pass

    def long_click(self, x, y):
        pass

    def double_click(self, x, y):
        pass

    def input_text(self, text):
        pass

    def press_key(self, key):
        pass

    def screenshot(self, path, method="snapshot_display"):
        import pathlib
        # Create a fake PNG file
        pathlib.Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return path

    def dump_hierarchy(self):
        return {
            "attributes": {
                "text": "Root",
                "type": "View",
                "bounds": {"left": 0, "top": 0, "right": 1080, "bottom": 2340},
            },
            "children": [
                {
                    "attributes": {
                        "text": "Button1",
                        "type": "Button",
                        "clickable": True,
                        "bounds": {"left": 100, "top": 200, "right": 300, "bottom": 260},
                        "boundsCenter": {"x": 200, "y": 230},
                    },
                    "children": [],
                },
                {
                    "attributes": {
                        "text": "抖音",
                        "type": "Text",
                        "description": "Douyin",
                        "bounds": {"left": 400, "top": 500, "right": 600, "bottom": 560},
                        "boundsCenter": {"x": 500, "y": 530},
                    },
                    "children": [],
                },
            ],
        }

    def current_app(self):
        return ("com.example.app", "MainAbility")

    def start_app(self, package, ability=None):
        pass

    def stop_app(self, package):
        pass

    def clear_app(self, package):
        pass

    def list_apps(self, include_system_apps=False):
        return ["com.example.app", "com.test.app"]

    def install_app(self, path):
        pass

    def uninstall_app(self, package):
        pass

    def get_app_info(self, package):
        return {"packageName": package, "version": "1.0"}

    def has_app(self, package):
        return True

    def get_app_main_ability(self, package):
        return {"packageName": package, "mainAbility": "MainAbility"}

    def unlock(self):
        pass

    def screen_on(self):
        pass

    def screen_off(self):
        pass

    def open_url(self, url):
        pass

    def __call__(self, **kwargs):
        """Simulate hmdriver2's element selector."""
        return MagicMock(
            exists=MagicMock(return_value=True),
            find_component=MagicMock(return_value=MagicMock(
                to_dict=MagicMock(return_value={
                    "text": kwargs.get("text", ""),
                    "type": "Button",
                    "boundsCenter": {"x": 200, "y": 300},
                }),
                text=kwargs.get("text", ""),
            )),
            count=MagicMock(return_value=1),
            click=MagicMock(),
        )


def _mock_get_hmdriver(serial):
    return MockHmDriver(serial)


@patch("phone_pilot.harmony.driver._get_hm", side_effect=_mock_get_hmdriver)
@patch("phone_pilot.harmony.driver._hm_available", return_value=True)
class TestHarmonyDriver(unittest.TestCase):
    """Tests for HarmonyDriver with mocked hmdriver2."""

    def test_platform(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        self.assertEqual(driver.platform, "harmony")
        self.assertEqual(driver.device_serial, "test_serial")

    def test_input_tap(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        result = driver.input.tap(100, 200)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("engine"), "hmdriver2")
        self.assertEqual(result.get("x"), 100)
        self.assertEqual(result.get("y"), 200)

    def test_input_swipe(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        result = driver.input.swipe(100, 200, 300, 400, 500)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("engine"), "hmdriver2")

    def test_input_long_press(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        result = driver.input.long_press(100, 200)
        self.assertTrue(result.get("ok"))

    def test_input_text(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        result = driver.input.input_text("hello")
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("text"), "hello")

    def test_screenshot(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        data = driver.screen.screenshot()
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_get_screen_size(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        w, h = driver.screen.get_screen_size()
        self.assertEqual(w, 1080)
        self.assertEqual(h, 2340)

    def test_dump_ui_hierarchy(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        hierarchy_json = driver.ui.dump_ui_hierarchy()
        # Should return a JSON string
        hierarchy = json.loads(hierarchy_json)
        self.assertIsInstance(hierarchy, dict)
        self.assertIn("children", hierarchy)

    def test_find_elements(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        results = driver.ui.find_elements({"text": "Button1"})
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)

    def test_get_current_activity(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        result = driver.ui.get_current_activity()
        self.assertEqual(result.get("package"), "com.example.app")
        self.assertEqual(result.get("activity"), "MainAbility")

    def test_app_list_packages(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        pkgs = driver.app.list_packages()
        self.assertIn("com.example.app", pkgs)

    def test_app_launch(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        result = driver.app.launch_app("com.example.app")
        self.assertTrue(result.get("ok"))

    def test_app_force_stop(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        result = driver.app.force_stop("com.example.app")
        self.assertTrue(result.get("ok"))

    def test_device_info(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        info = driver.device_info()
        self.assertEqual(info.get("model"), "MockModel")
        self.assertEqual(info.get("productName"), "MockProduct")

    def test_go_home_and_back(self, *_mocks):
        driver = HarmonyDriver("test_serial")
        driver.go_home()
        driver.go_back()


class TestResolveKeycode(unittest.TestCase):
    """Test keycode resolution."""

    def test_direct_name(self):
        from hmdriver2.proto import KeyCode
        kc = _resolve_keycode("HOME", KeyCode)
        self.assertEqual(kc, KeyCode.HOME)

    def test_android_prefix(self):
        from hmdriver2.proto import KeyCode
        kc = _resolve_keycode("KEYCODE_BACK", KeyCode)
        self.assertEqual(kc, KeyCode.BACK)

    def test_integer_value(self):
        # Use a real enum that will fail name lookup, forcing integer fallback
        import enum
        class FakeEnum(enum.Enum):
            BACK = 2
        kc = _resolve_keycode("99", FakeEnum)
        self.assertEqual(kc, 99)


class TestElementInfoToDict(unittest.TestCase):
    """Test element info conversion."""

    def test_with_to_dict(self):
        info = MagicMock()
        info.to_dict.return_value = {"text": "hello", "type": "Button"}
        result = _element_info_to_dict(info)
        self.assertEqual(result["text"], "hello")

    def test_fallback(self):
        info = MagicMock(spec=[])
        info.text = "test"
        info.type = "Text"
        info.bounds = MagicMock(left=0, top=0, right=100, bottom=50)
        info.boundsCenter = MagicMock(x=50, y=25)
        result = _element_info_to_dict(info)
        self.assertIn("bounds", result)


if __name__ == "__main__":
    unittest.main()
