"""Tests for iOS PopupGuard window layer detection and permission dialog rules."""

from __future__ import annotations

import unittest
from typing import Optional
from unittest.mock import MagicMock

from phone_pilot.script_api.popup_guard import (
    DEFAULT_RULES,
    PopupGuard,
    PopupRule,
    _DIALOG_CLASS_PATTERNS,
)


class MockIOSUINode:
    """Mock UINode with iOS XCUITest attributes for testing."""

    def __init__(
        self,
        text: str = "",
        content_desc: str = "",
        resource_id: str = "",
        class_name: str = "",
        package: str = "",
        clickable: bool = False,
        bounds: str = "",
        _center_x: int = 0,
        _center_y: int = 0,
    ) -> None:
        self.text = text
        self.content_desc = content_desc
        self.resource_id = resource_id
        self.class_name = class_name
        self.package = package
        self.clickable = clickable
        self.bounds = bounds
        self._center_x = _center_x
        self._center_y = _center_y

    def center(self) -> Optional[tuple[int, int]]:
        if self._center_x and self._center_y:
            return (self._center_x, self._center_y)
        return None

    def bounds_tuple(self) -> Optional[tuple[int, int, int, int]]:
        if not self.bounds:
            return None
        import re

        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", self.bounds)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "content_desc": self.content_desc,
            "resource_id": self.resource_id,
            "class_name": self.class_name,
            "package": self.package,
            "clickable": self.clickable,
            "bounds": self.bounds,
        }


def _make_mock_ios_ctx(device_serial: str = "mock_ios_udid") -> MagicMock:
    """Create a mock ScriptContext configured for iOS testing."""
    ctx = MagicMock()
    ctx.device_serial = device_serial
    ctx.platform = "ios"
    ctx.driver.platform = "ios"
    ctx.driver.ui.dump_ui_nodes.return_value = []
    ctx.driver.ui.find_elements.return_value = []
    ctx.driver.screen.get_screen_size.return_value = (1170, 2532)
    ctx.driver.input.tap.return_value = None
    return ctx


class TestIOSWindowLayerDetection(unittest.TestCase):
    """Test _check_window_ios Window layer detection with WDA elements."""

    def setUp(self) -> None:
        self.ctx = _make_mock_ios_ctx()
        self.pg = PopupGuard(self.ctx)

    def test_check_window_ios_alert_detected(self) -> None:
        """_check_window_ios returns True when XCUIElementTypeAlert element exists."""
        self.ctx.driver.ui.find_elements.side_effect = lambda query: (
            [{"type": "XCUIElementTypeAlert"}] if query.get("class_name") == "XCUIElementTypeAlert" else []
        )
        self.assertTrue(self.pg._check_window_ios())

    def test_check_window_ios_sheet_detected(self) -> None:
        """_check_window_ios returns True when XCUIElementTypeSheet element exists."""
        self.ctx.driver.ui.find_elements.side_effect = lambda query: (
            [{"type": "XCUIElementTypeSheet"}] if query.get("class_name") == "XCUIElementTypeSheet" else []
        )
        self.assertTrue(self.pg._check_window_ios())

    def test_check_window_ios_no_popup(self) -> None:
        """_check_window_ios returns False when no alert or sheet exists."""
        self.ctx.driver.ui.find_elements.return_value = []
        self.assertFalse(self.pg._check_window_ios())

    def test_check_window_ios_exception_handled(self) -> None:
        """_check_window_ios returns False gracefully if find_elements raises."""
        self.ctx.driver.ui.find_elements.side_effect = RuntimeError("WDA connection lost")
        self.assertFalse(self.pg._check_window_ios())


class TestIOSUIStructureAnalysis(unittest.TestCase):
    """Test _analyze_ui_structure for iOS UI nodes."""

    def setUp(self) -> None:
        self.ctx = _make_mock_ios_ctx()
        self.pg = PopupGuard(self.ctx)

    def test_dialog_class_patterns_contains_ios_classes(self) -> None:
        """Verify _DIALOG_CLASS_PATTERNS contains XCUIElementTypeAlert and sheets."""
        patterns = [p.lower() for p in _DIALOG_CLASS_PATTERNS]
        self.assertIn("xcuielementtypealert", patterns)
        self.assertIn("xcuielementtypesheet", patterns)
        self.assertIn("xcuielementtypeactionsheet", patterns)

    def test_analyze_ui_structure_detects_alert_node(self) -> None:
        alert_node = MockIOSUINode(class_name="XCUIElementTypeAlert", text="系统提示")
        other_node = MockIOSUINode(class_name="XCUIElementTypeButton", text="按钮")
        info = self.pg._analyze_ui_structure([alert_node, other_node])
        self.assertTrue(info["is_popup"])
        self.assertEqual(info["type"], "dialog_class")
        self.assertIn(alert_node, info["dialog_nodes"])

    def test_analyze_ui_structure_detects_sheet_node(self) -> None:
        sheet_node = MockIOSUINode(class_name="XCUIElementTypeActionSheet", text="选择操作")
        info = self.pg._analyze_ui_structure([sheet_node])
        self.assertTrue(info["is_popup"])
        self.assertEqual(info["type"], "dialog_class")
        self.assertIn(sheet_node, info["dialog_nodes"])


class TestIOSPopupRulesAndDismiss(unittest.TestCase):
    """Test standard popup rules against iOS system dialogs."""

    def setUp(self) -> None:
        self.ctx = _make_mock_ios_ctx()
        self.pg = PopupGuard(self.ctx)

    def test_dismiss_cellular_wifi_permission_dialog(self) -> None:
        """iOS '无线局域网与蜂窝网络' permission alert should be matched and dismissed."""
        title_node = MockIOSUINode(
            class_name="XCUIElementTypeAlert",
            text="是否允许“App”使用无线局域网与蜂窝网络？",
        )
        allow_button = MockIOSUINode(
            class_name="XCUIElementTypeButton",
            text="无线局域网与蜂窝网络",
            clickable=True,
            _center_x=300,
            _center_y=1200,
        )
        self.ctx.driver.ui.dump_ui_nodes.return_value = [title_node, allow_button]

        dismissed = self.pg.check_and_dismiss()
        self.assertTrue(dismissed)
        self.ctx.driver.input.tap.assert_called_with(300, 1200)

    def test_dismiss_location_permission_dialog_english(self) -> None:
        """iOS English permission dialog ('Allow While Using App') should be dismissed."""
        title_node = MockIOSUINode(
            class_name="XCUIElementTypeAlert",
            text="Allow “App” to access your location?",
        )
        allow_button = MockIOSUINode(
            class_name="XCUIElementTypeButton",
            text="Allow While Using App",
            clickable=True,
            _center_x=350,
            _center_y=1100,
        )
        self.ctx.driver.ui.dump_ui_nodes.return_value = [title_node, allow_button]

        dismissed = self.pg.check_and_dismiss()
        self.assertTrue(dismissed)
        self.ctx.driver.input.tap.assert_called_with(350, 1100)

    def test_dismiss_notification_permission_dialog(self) -> None:
        """iOS Notification permission alert ('好' / 'Allow') should be dismissed."""
        title_node = MockIOSUINode(
            class_name="XCUIElementTypeAlert",
            text="“App”想向您发送通知",
        )
        allow_button = MockIOSUINode(
            class_name="XCUIElementTypeButton",
            text="允许",
            clickable=True,
            _center_x=400,
            _center_y=1300,
        )
        self.ctx.driver.ui.dump_ui_nodes.return_value = [title_node, allow_button]

        dismissed = self.pg.check_and_dismiss()
        self.assertTrue(dismissed)
        self.ctx.driver.input.tap.assert_called_with(400, 1300)


if __name__ == "__main__":
    unittest.main()
