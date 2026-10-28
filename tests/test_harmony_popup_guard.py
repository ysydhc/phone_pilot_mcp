"""Tests for HarmonyOS PopupGuard window layer detection and permission dialog rules."""

from __future__ import annotations

import unittest
from typing import Optional
from unittest.mock import MagicMock, patch

from phone_pilot.script_api.popup_guard import (
    DEFAULT_RULES,
    PopupGuard,
    PopupRule,
    _DIALOG_CLASS_PATTERNS,
    _DIALOG_RESOURCE_IDS,
    _DIALOG_WINDOW_PATTERNS_HARMONY,
)


class MockHarmonyUINode:
    """Mock UINode with HarmonyOS / ArkUI attributes for testing."""

    def __init__(
        self,
        text: str = "",
        content_desc: str = "",
        resource_id: str = "",
        class_name: str = "",
        package: str = "",
        bundle_name: str = "",
        bundleName: str = "",
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
        self.bundle_name = bundle_name
        self.bundleName = bundleName
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


def _make_mock_harmony_ctx(device_serial: str = "mock_harmony_serial") -> MagicMock:
    """Create a mock ScriptContext configured for HarmonyOS testing."""
    ctx = MagicMock()
    ctx.device_serial = device_serial
    ctx.driver.platform = "harmony"
    ctx.driver.ui.dump_ui_nodes.return_value = []
    ctx.driver.screen.get_screen_size.return_value = (1080, 2400)
    ctx.driver.input.tap.return_value = None
    return ctx


class TestHarmonyWindowLayerDetection(unittest.TestCase):
    """Test _check_window_harmony Window layer detection with hidumper WindowManagerService."""

    def setUp(self) -> None:
        self.ctx = _make_mock_harmony_ctx()
        self.pg = PopupGuard(self.ctx)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_permission_manager(self, mock_run: MagicMock) -> None:
        """_check_window_harmony returns True when hidumper output contains com.ohos.permissionmanager."""
        mock_proc = MagicMock()
        mock_proc.stdout = (
            "Focus window: Window [id=10, name=com.ohos.permissionmanager/GrantAbility, "
            "bundleName=com.ohos.permissionmanager, state=FOREGROUND]"
        )
        mock_run.return_value = mock_proc

        result = self.pg._check_window_harmony()
        self.assertTrue(result)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("hidumper", cmd)
        self.assertIn("WindowManagerService", cmd)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_window_type_dialog(self, mock_run: MagicMock) -> None:
        """_check_window_harmony returns True when hidumper output contains WINDOW_TYPE_DIALOG."""
        mock_proc = MagicMock()
        mock_proc.stdout = "Window [id=22, name=CustomSubWindow, type=WINDOW_TYPE_DIALOG]"
        mock_run.return_value = mock_proc

        self.assertTrue(self.pg._check_window_harmony())

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_dialog_keyword(self, mock_run: MagicMock) -> None:
        """_check_window_harmony returns True when hidumper output contains Dialog."""
        mock_proc = MagicMock()
        mock_proc.stdout = "Active window list:\n  - DialogComponent_552 [visible=true]"
        mock_run.return_value = mock_proc

        self.assertTrue(self.pg._check_window_harmony())

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_subwindow_and_is_dialog(self, mock_run: MagicMock) -> None:
        """_check_window_harmony returns True when hidumper output contains WINDOW_TYPE_APP_SUB_WINDOW or isDialog."""
        mock_proc = MagicMock()
        mock_proc.stdout = "Window [id=5, type=WINDOW_TYPE_APP_SUB_WINDOW, isDialog=true]"
        mock_run.return_value = mock_proc

        self.assertTrue(self.pg._check_window_harmony())

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_normal_main_window(self, mock_run: MagicMock) -> None:
        """_check_window_harmony returns False when output is normal main window."""
        mock_proc = MagicMock()
        mock_proc.stdout = (
            "Focus window: Window [id=1, name=com.example.app/EntryAbility, "
            "type=WINDOW_TYPE_APP_MAIN_WINDOW, state=FOREGROUND]"
        )
        mock_run.return_value = mock_proc

        self.assertFalse(self.pg._check_window_harmony())

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_empty_output(self, mock_run: MagicMock) -> None:
        """_check_window_harmony returns False when hidumper output is empty."""
        mock_proc = MagicMock()
        mock_proc.stdout = ""
        mock_run.return_value = mock_proc

        self.assertFalse(self.pg._check_window_harmony())

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_execution_exception_handled(self, mock_run: MagicMock) -> None:
        """_check_window_harmony handles execution exceptions gracefully returning False."""
        mock_run.side_effect = RuntimeError("HDC command execution failed")
        # Ensure driver.shell also fails or not available
        self.ctx.driver.shell.side_effect = RuntimeError("Driver shell failed")

        result = self.pg._check_window_harmony()
        self.assertFalse(result)

    @patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
    def test_check_window_harmony_fallback_to_driver_shell(self, mock_run: MagicMock) -> None:
        """Fallback to driver.shell if HdcCommandRunner returns empty."""
        mock_proc = MagicMock()
        mock_proc.stdout = ""
        mock_run.return_value = mock_proc

        self.ctx.driver.shell.return_value = {
            "stdout": "Window [id=3, name=com.ohos.permissionmanager/Notice]"
        }

        result = self.pg._check_window_harmony()
        self.assertTrue(result)
        self.ctx.driver.shell.assert_called_once()

    def test_check_window_layer_harmony_dispatch(self) -> None:
        """_check_window_layer calls _check_window_harmony when driver.platform is harmony."""
        with patch.object(self.pg, "_check_window_harmony", return_value=True) as mock_harmony:
            res = self.pg._check_window_layer()
            self.assertTrue(res)
            mock_harmony.assert_called_once()


class TestHarmonyUIStructureAnalysis(unittest.TestCase):
    """Test _analyze_ui_structure for ArkUI dialog components and permission manager."""

    def setUp(self) -> None:
        self.ctx = _make_mock_harmony_ctx()
        self.pg = PopupGuard(self.ctx)

    def test_detect_custom_dialog_class(self) -> None:
        """ArkUI CustomDialog is identified as popup."""
        nodes = [
            MockHarmonyUINode(class_name="CustomDialog", text="提示"),
            MockHarmonyUINode(text="确定", clickable=True, _center_x=500, _center_y=1000),
        ]
        info = self.pg._analyze_ui_structure(nodes)
        self.assertTrue(info["is_popup"])
        self.assertEqual(info["type"], "dialog_class")
        self.assertIn(nodes[0], info["dialog_nodes"])

    def test_detect_dialog_component_class(self) -> None:
        """ArkUI DialogComponent is identified as popup."""
        nodes = [
            MockHarmonyUINode(class_name="DialogComponent", text="系统通知"),
        ]
        info = self.pg._analyze_ui_structure(nodes)
        self.assertTrue(info["is_popup"])
        self.assertEqual(info["type"], "dialog_class")

    def test_detect_permission_manager_package(self) -> None:
        """ArkUI component with package matching com.ohos.permissionmanager is identified."""
        nodes = [
            MockHarmonyUINode(
                package="com.ohos.permissionmanager",
                text="允许应用访问此设备的位置信息吗？",
            ),
        ]
        info = self.pg._analyze_ui_structure(nodes)
        self.assertTrue(info["is_popup"])
        self.assertEqual(info["type"], "permission_dialog")

    def test_detect_permission_manager_bundle_name(self) -> None:
        """ArkUI component with bundle_name/bundleName matching com.ohos.permissionmanager is identified."""
        nodes = [
            MockHarmonyUINode(
                bundle_name="com.ohos.permissionmanager",
                text="允许应用录制音频？",
            ),
        ]
        info = self.pg._analyze_ui_structure(nodes)
        self.assertTrue(info["is_popup"])
        self.assertEqual(info["type"], "permission_dialog")

    def test_detect_permission_manager_resource_id(self) -> None:
        """ArkUI component with resource_id matching com.ohos.permissionmanager is identified."""
        nodes = [
            MockHarmonyUINode(
                resource_id="com.ohos.permissionmanager:id/permission_allow_button",
                clickable=True,
            ),
        ]
        info = self.pg._analyze_ui_structure(nodes)
        self.assertTrue(info["is_popup"])
        self.assertEqual(info["type"], "permission_dialog")


class TestHarmonyPermissionDialogDismissal(unittest.TestCase):
    """Test PopupGuard.check_and_dismiss() with HarmonyOS permission dialogs."""

    def setUp(self) -> None:
        self.ctx = _make_mock_harmony_ctx()
        self.pg = PopupGuard(self.ctx)

        # Mock HdcCommandRunner.run so tests do not invoke real hdc
        self.hdc_patcher = patch("phone_pilot.harmony.hdc.runner.HdcCommandRunner.run")
        self.mock_hdc_run = self.hdc_patcher.start()
        mock_proc = MagicMock()
        mock_proc.stdout = (
            "Focus window: Window [name=com.ohos.permissionmanager, type=WINDOW_TYPE_DIALOG]"
        )
        self.mock_hdc_run.return_value = mock_proc

        # Mock time.sleep to keep test execution fast
        self.sleep_patcher = patch("time.sleep")
        self.mock_sleep = self.sleep_patcher.start()

    def tearDown(self) -> None:
        self.sleep_patcher.stop()
        self.hdc_patcher.stop()

    def test_dismiss_permission_allow(self) -> None:
        """Detect and dismiss HarmonyOS permission dialog by '允许'."""
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockHarmonyUINode(
                package="com.ohos.permissionmanager",
                text="是否允许该应用获取位置信息？",
            ),
            MockHarmonyUINode(
                text="不允许",
                clickable=True,
                _center_x=300,
                _center_y=1200,
            ),
            MockHarmonyUINode(
                text="允许",
                clickable=True,
                _center_x=700,
                _center_y=1200,
            ),
        ]

        result = self.pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "system_permission")
        self.assertIn(result.get("dismiss_text"), ["允许", "仅本次允许"])
        self.ctx.driver.input.tap.assert_called_with(700, 1200)

    def test_dismiss_permission_allow_once(self) -> None:
        """Detect and dismiss HarmonyOS permission dialog by '仅本次允许'."""
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockHarmonyUINode(
                class_name="CustomDialog",
                package="com.ohos.permissionmanager",
                text="允许应用访问麦克风权限？",
            ),
            MockHarmonyUINode(
                text="仅本次允许",
                clickable=True,
                _center_x=540,
                _center_y=1300,
            ),
            MockHarmonyUINode(
                text="不允许",
                clickable=True,
                _center_x=540,
                _center_y=1400,
            ),
        ]

        result = self.pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "system_permission")
        self.assertEqual(result.get("dismiss_text"), "仅本次允许")
        self.ctx.driver.input.tap.assert_called_with(540, 1300)

    def test_dismiss_permission_by_resource_id(self) -> None:
        """Detect and dismiss by resource_id 'com.ohos.permissionmanager:id/permission_allow_button'."""
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockHarmonyUINode(
                package="com.ohos.permissionmanager",
                text="请授予剪贴板权限",
            ),
            MockHarmonyUINode(
                resource_id="com.ohos.permissionmanager:id/permission_allow_button",
                text="允许",
                clickable=True,
                _center_x=600,
                _center_y=1100,
            ),
        ]

        result = self.pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "system_permission")
        self.assertEqual(
            result.get("dismiss_resource_id"),
            "com.ohos.permissionmanager:id/permission_allow_button",
        )
        self.ctx.driver.input.tap.assert_called_with(600, 1100)

    def test_dismiss_permission_multilingual_english_allow(self) -> None:
        """Multi-language: English HarmonyOS permission dialog with 'Allow'."""
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockHarmonyUINode(
                package="com.ohos.permissionmanager",
                text="Allow app to access media and files?",
            ),
            MockHarmonyUINode(
                text="Don't allow",
                clickable=True,
                _center_x=300,
                _center_y=1200,
            ),
            MockHarmonyUINode(
                text="Allow",
                clickable=True,
                _center_x=700,
                _center_y=1200,
            ),
        ]

        result = self.pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "system_permission")
        self.assertEqual(result.get("dismiss_text"), "Allow")
        self.ctx.driver.input.tap.assert_called_with(700, 1200)

    def test_dismiss_permission_multilingual_english_only_this_time(self) -> None:
        """Multi-language: English HarmonyOS permission dialog with 'Only this time'."""
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockHarmonyUINode(
                package="com.ohos.permissionmanager",
                text="Allow Camera permission for this session?",
            ),
            MockHarmonyUINode(
                text="Only this time",
                clickable=True,
                _center_x=540,
                _center_y=1200,
            ),
            MockHarmonyUINode(
                text="Don't allow",
                clickable=True,
                _center_x=540,
                _center_y=1350,
            ),
        ]

        result = self.pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "system_permission")
        self.assertEqual(result.get("dismiss_text"), "Only this time")
        self.ctx.driver.input.tap.assert_called_with(540, 1200)

    def test_dismiss_permission_detected_by_media_and_files(self) -> None:
        """Permission dialog detected by '媒体和文件' keyword."""
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockHarmonyUINode(text="需要访问您的媒体和文件"),
            MockHarmonyUINode(
                text="仅在使用中允许",
                clickable=True,
                _center_x=500,
                _center_y=1000,
            ),
        ]

        result = self.pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "system_permission")
        self.assertEqual(result.get("dismiss_text"), "仅在使用中允许")
        self.ctx.driver.input.tap.assert_called_with(500, 1000)

    def test_dismiss_permission_detected_by_clipboard(self) -> None:
        """Permission dialog detected by '剪贴板' keyword."""
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockHarmonyUINode(text="应用申请读取剪贴板"),
            MockHarmonyUINode(
                text="允许",
                clickable=True,
                _center_x=500,
                _center_y=1000,
            ),
        ]

        result = self.pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "system_permission")
        self.assertEqual(result.get("dismiss_text"), "允许")
        self.ctx.driver.input.tap.assert_called_with(500, 1000)


if __name__ == "__main__":
    unittest.main()
