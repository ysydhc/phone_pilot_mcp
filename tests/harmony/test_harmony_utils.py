import unittest

from phone_pilot.harmony.device import utils as h_utils


class TestHarmonyDeviceUtils(unittest.TestCase):
    def test_input_text_unicode_rejected(self) -> None:
        res = h_utils.input_text("dummy", "中文")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "unicode_input_not_supported")

    def test_input_tap_requires_device(self) -> None:
        res = h_utils.input_tap(None, 10, 20)
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "device_serial is required")

    def test_list_packages_without_device(self) -> None:
        pkgs = h_utils.list_installed_packages(None)
        self.assertEqual(pkgs, [])

    def test_get_screen_size_without_device(self) -> None:
        self.assertIsNone(h_utils.get_screen_size(None))

    def test_execute_shell_empty(self) -> None:
        res = h_utils.execute_shell("dummy", "")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "command is required")

    def test_execute_shell_whitelist_rejection(self) -> None:
        res = h_utils.execute_shell("dummy", "curl http://example.com")
        self.assertFalse(res.get("ok"))
        self.assertIn("not in whitelist", res.get("error", ""))

    def test_execute_shell_forbidden_rejection(self) -> None:
        res = h_utils.execute_shell("dummy", "reboot")
        self.assertFalse(res.get("ok"))
        self.assertIn("is forbidden", res.get("error", ""))

    def test_execute_shell_rm_rf_rejection(self) -> None:
        res = h_utils.execute_shell("dummy", "rm -rf /")
        self.assertFalse(res.get("ok"))
        self.assertEqual(res.get("error"), "rm -rf is forbidden")
