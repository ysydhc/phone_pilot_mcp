"""Unit tests for Android get_current_focus (dumpsys window displays) and PopupGuard ANR defense."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from phone_pilot.android.device.utils import get_current_focus
from phone_pilot.script_api.popup_guard import (
    DEFAULT_RULES,
    _DIALOG_RESOURCE_IDS,
    _DIALOG_WINDOW_PATTERNS,
)


class TestGetCurrentFocus(unittest.TestCase):
    """Verify get_current_focus queries dumpsys window displays and handles fallbacks."""

    @patch("phone_pilot.android.device.utils.CommandRunner.run")
    def test_uses_dumpsys_window_displays_and_parses_mCurrentFocus(
        self, mock_run: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = (
            "  mCurrentFocus=Window{d76810d u0 cc.admaster.android.demo/"
            "cc.admaster.android.demo.InterstitialActivity}\n"
            "  mFocusedApp=ActivityRecord{9008982 u0 cc.admaster.android.demo/"
            ".InterstitialActivity t1939}\n"
        )
        mock_run.return_value = mock_proc

        res = get_current_focus("98e516bf0922")
        self.assertEqual(
            res,
            {
                "package": "cc.admaster.android.demo",
                "activity": "cc.admaster.android.demo.InterstitialActivity",
            },
        )
        cmd = mock_run.call_args[0][0]
        self.assertTrue(
            any("dumpsys window displays" in part for part in cmd),
            f"Expected 'dumpsys window displays' in command: {cmd}",
        )

    @patch("phone_pilot.android.device.utils.CommandRunner.run")
    def test_falls_back_to_mFocusedApp_when_mCurrentFocus_is_null(
        self, mock_run: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = (
            "  mCurrentFocus=null\n"
            "  mFocusedApp=ActivityRecord{12ef83b u0 com.android.vending/"
            "com.google.android.finsky.activities.MarketDeepLinkHandlerActivity t1939}\n"
        )
        mock_run.return_value = mock_proc

        res = get_current_focus("98e516bf0922")
        self.assertEqual(
            res,
            {
                "package": "com.android.vending",
                "activity": "com.google.android.finsky.activities.MarketDeepLinkHandlerActivity",
            },
        )

    @patch("phone_pilot.android.device.utils.CommandRunner.run")
    def test_falls_back_to_anr_window_when_anr_dialog_focused(
        self, mock_run: MagicMock
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.stdout = (
            "  mCurrentFocus=Window{33c9487 u0 Application Not Responding: com.android.vending}\n"
            "  mFocusedApp=null\n"
        )
        mock_run.return_value = mock_proc

        res = get_current_focus("98e516bf0922")
        self.assertEqual(
            res,
            {
                "package": "com.android.vending",
                "activity": "ApplicationNotResponding",
            },
        )


class TestPopupGuardAnrRule(unittest.TestCase):
    """Verify PopupGuard includes system_anr_crash rule and detects Android ANR windows."""

    def test_system_anr_crash_rule_present(self) -> None:
        rules_by_name = {r.name: r for r in DEFAULT_RULES}
        self.assertIn("system_anr_crash", rules_by_name)
        rule = rules_by_name["system_anr_crash"]
        self.assertGreaterEqual(rule.priority, 100)
        self.assertIn("android:id/aerr_close", rule.dismiss_resource_ids)
        self.assertIn("android:id/aerr_close", _DIALOG_RESOURCE_IDS)

    def test_android_anr_window_pattern_matches(self) -> None:
        window_line = "  mCurrentFocus=Window{33c9487 u0 Application Not Responding: com.android.vending}"
        self.assertIsNotNone(_DIALOG_WINDOW_PATTERNS.search(window_line))


if __name__ == "__main__":
    unittest.main()
