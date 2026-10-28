#!/usr/bin/env python3
"""
Unit tests for HarmonyOS desktop launcher detection, profiles snapshot, and home launch.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import MagicMock, call, patch

from phone_pilot.harmony.device import utils as h_utils
from phone_pilot.harmony.driver import HarmonyDriver


def _mock_proc(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["mock"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestHarmonyLauncherUtils(unittest.TestCase):
    """Test suite for launcher component detection and launch_from_home helpers."""

    SERIAL = "harmony-device-test-01"

    def test_get_default_launcher_from_json_dump(self) -> None:
        """Resolve launcher component when bm dump -a returns JSON."""
        dump_json = [
            {
                "bundleName": "com.ohos.sceneboard",
                "mainElement": "MainAbility",
                "abilities": [
                    {
                        "name": "MainAbility",
                        "skills": [
                            {
                                "actions": ["action.system.home"],
                                "entities": ["entity.system.home"],
                            }
                        ],
                    }
                ],
            },
            {
                "bundleName": "com.example.regularapp",
                "mainElement": "EntryAbility",
                "abilities": [
                    {
                        "name": "EntryAbility",
                        "skills": [
                            {
                                "actions": ["action.system.entry"],
                            }
                        ],
                    }
                ],
            },
        ]

        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stdout=json.dumps(dump_json)),
        ):
            comp = h_utils.get_default_launcher_component(self.SERIAL)
            self.assertEqual(comp, "com.ohos.sceneboard/MainAbility")

    def test_get_default_launcher_from_text_dump(self) -> None:
        """Resolve launcher component when bm dump -a returns text."""
        raw_text = """
UserInfo:
  bundleName: com.ohos.launcher
  mainAbility: MainAbility
  abilities:
    name: MainAbility
    skills:
      actions: action.system.home
      entities: entity.system.home
"""
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stdout=raw_text),
        ):
            comp = h_utils.get_default_launcher_component(self.SERIAL)
            self.assertEqual(comp, "com.ohos.launcher/MainAbility")

    def test_get_default_launcher_fallback_installed(self) -> None:
        """Fallback to known launcher bundles if bm dump has no home skills."""
        # bm dump returns nothing matching home
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stdout="No launcher found"),
        ):
            with patch.object(
                h_utils,
                "list_installed_packages",
                return_value=["com.example.app", "com.ohos.sceneboard"],
            ):
                comp = h_utils.get_default_launcher_component(self.SERIAL)
                self.assertEqual(comp, "com.ohos.sceneboard/MainAbility")

    def test_get_default_launcher_unresolved(self) -> None:
        """Return None if no launcher matches and none of known bundles is installed."""
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stdout=""),
        ):
            with patch.object(
                h_utils,
                "list_installed_packages",
                return_value=["com.example.app"],
            ):
                comp = h_utils.get_default_launcher_component(self.SERIAL)
                self.assertIsNone(comp)

    def test_list_all_launcher_components(self) -> None:
        """list_all_launcher_components groups entries by bundle."""
        dump_data = [
            {
                "bundleName": "com.example.app1",
                "mainElement": "EntryAbility",
                "abilities": [
                    {
                        "name": "EntryAbility",
                        "skills": [{"actions": ["action.system.entry"]}],
                    },
                    {
                        "name": "DetailAbility",
                        "skills": [{"actions": ["action.detail"]}],
                    },
                ],
            },
            {
                "bundleName": "com.ohos.sceneboard",
                "mainElement": "MainAbility",
                "abilities": [
                    {
                        "name": "MainAbility",
                        "skills": [{"actions": ["action.system.home"]}],
                    }
                ],
            },
        ]
        with patch.object(
            h_utils.HdcCommandRunner,
            "run",
            return_value=_mock_proc(stdout=json.dumps(dump_data)),
        ):
            comps = h_utils.list_all_launcher_components(self.SERIAL)
            self.assertIn("com.example.app1", comps)
            self.assertIn("com.ohos.sceneboard", comps)
            self.assertEqual(comps["com.example.app1"], ["com.example.app1/EntryAbility"])
            self.assertEqual(comps["com.ohos.sceneboard"], ["com.ohos.sceneboard/MainAbility"])

    def test_build_launch_profiles_snapshot(self) -> None:
        """build_launch_profiles_snapshot generates snapshot records."""
        comp_map = {
            "com.example.app1": ["com.example.app1/EntryAbility"],
            "com.example.app2": ["com.example.app2/MainAbility"],
        }
        with patch.object(
            h_utils,
            "list_all_launcher_components",
            return_value=comp_map,
        ):
            profiles = h_utils.build_launch_profiles_snapshot(self.SERIAL)
            self.assertEqual(len(profiles), 2)

            p1 = next(p for p in profiles if p["package_name"] == "com.example.app1")
            self.assertEqual(p1["activity"], "EntryAbility")
            self.assertEqual(p1["component"], "com.example.app1/EntryAbility")
            self.assertEqual(
                p1["adb_cmd"],
                f"hdc -t {self.SERIAL} shell aa start -b com.example.app1 -a EntryAbility",
            )
            self.assertEqual(p1["source"], "bm_dump")

    def test_build_launch_profiles_snapshot_no_serial(self) -> None:
        """build_launch_profiles_snapshot without serial omits -t."""
        comp_map = {
            "com.example.app1": ["com.example.app1/EntryAbility"],
        }
        with patch.object(
            h_utils,
            "list_all_launcher_components",
            return_value=comp_map,
        ):
            profiles = h_utils.build_launch_profiles_snapshot(None)
            self.assertEqual(len(profiles), 1)
            self.assertEqual(
                profiles[0]["adb_cmd"],
                "hdc shell aa start -b com.example.app1 -a EntryAbility",
            )

    def test_launch_from_home_success(self) -> None:
        """launch_from_home sends HOME keyevent (1) then starts the app."""
        with patch.object(
            h_utils,
            "input_keyevent",
            return_value={"ok": True},
        ) as mock_key, patch.object(
            h_utils,
            "launch_app",
            return_value={"ok": True},
        ) as mock_launch:
            res = h_utils.launch_from_home(
                self.SERIAL, "com.example.mybundle", "EntryAbility"
            )

            mock_key.assert_called_once_with(self.SERIAL, "1")
            mock_launch.assert_called_once_with(
                self.SERIAL, "com.example.mybundle", activity="EntryAbility"
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["bundle"], "com.example.mybundle")
            self.assertEqual(res["ability"], "EntryAbility")

    def test_launch_from_home_failure(self) -> None:
        """launch_from_home returns error when launch_app fails."""
        with patch.object(
            h_utils,
            "input_keyevent",
            return_value={"ok": True},
        ), patch.object(
            h_utils,
            "launch_app",
            return_value={"ok": False, "stderr": "Ability does not exist"},
        ):
            res = h_utils.launch_from_home(
                self.SERIAL, "com.example.mybundle", "NonExistentAbility"
            )
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "Ability does not exist")


class TestHarmonyDriverLauncherIntegration(unittest.TestCase):
    """Test suite for launcher methods on HarmonyDriver and HarmonyAppDriver."""

    SERIAL = "harmony-driver-test-02"

    def setUp(self) -> None:
        self.driver = HarmonyDriver(self.SERIAL)

    def test_driver_get_default_launcher_component(self) -> None:
        """HarmonyDriver.get_default_launcher_component delegates to utils."""
        with patch(
            "phone_pilot.harmony.device.utils.get_default_launcher_component",
            return_value="com.ohos.sceneboard/MainAbility",
        ) as mock_get:
            comp = self.driver.get_default_launcher_component()
            self.assertEqual(comp, "com.ohos.sceneboard/MainAbility")
            mock_get.assert_called_once_with(self.SERIAL)

    def test_driver_list_all_launcher_components(self) -> None:
        """HarmonyDriver.list_all_launcher_components delegates to utils."""
        expected = {"com.example.app": ["com.example.app/EntryAbility"]}
        with patch(
            "phone_pilot.harmony.device.utils.list_all_launcher_components",
            return_value=expected,
        ) as mock_list:
            res = self.driver.list_all_launcher_components()
            self.assertEqual(res, expected)
            mock_list.assert_called_once_with(self.SERIAL)

    def test_driver_build_launch_profiles(self) -> None:
        """HarmonyDriver.build_launch_profiles delegates to utils."""
        expected = [{"package_name": "com.app", "component": "com.app/Main"}]
        with patch(
            "phone_pilot.harmony.device.utils.build_launch_profiles_snapshot",
            return_value=expected,
        ) as mock_build:
            res = self.driver.build_launch_profiles()
            self.assertEqual(res, expected)
            mock_build.assert_called_once_with(self.SERIAL)

    def test_driver_launch_from_home_bundle(self) -> None:
        """HarmonyDriver.launch_from_home(package, activity) delegates to utils."""
        with patch(
            "phone_pilot.harmony.device.utils.launch_from_home",
            return_value={"ok": True, "bundle": "com.app", "ability": "Main"},
        ) as mock_launch:
            res = self.driver.launch_from_home("com.app", "Main")
            self.assertTrue(res["ok"])
            mock_launch.assert_called_once_with(self.SERIAL, "com.app", "Main")

    def test_driver_launch_from_home_finder_backward_compatible(self) -> None:
        """HarmonyDriver.launch_from_home(app_name, reset_home=True) delegates to finder."""
        with patch(
            "phone_pilot.harmony.ui.finder.launch_from_home",
            return_value={"ok": True, "app_name": "Settings"},
        ) as mock_finder:
            res = self.driver.launch_from_home("Settings", reset_home=True, max_swipes=3)
            self.assertTrue(res["ok"])
            mock_finder.assert_called_once_with(
                self.SERIAL, "Settings", reset_home=True, max_swipes=3
            )

    def test_app_driver_delegates(self) -> None:
        """HarmonyAppDriver also delegates launcher methods."""
        app_drv = self.driver.app
        with patch(
            "phone_pilot.harmony.device.utils.get_default_launcher_component",
            return_value="com.ohos.sceneboard/MainAbility",
        ), patch(
            "phone_pilot.harmony.device.utils.list_all_launcher_components",
            return_value={"pkg": ["pkg/Main"]},
        ), patch(
            "phone_pilot.harmony.device.utils.build_launch_profiles_snapshot",
            return_value=[{"package_name": "pkg"}],
        ), patch(
            "phone_pilot.harmony.device.utils.launch_from_home",
            return_value={"ok": True},
        ):
            self.assertEqual(
                app_drv.get_default_launcher_component(),
                "com.ohos.sceneboard/MainAbility",
            )
            self.assertEqual(app_drv.list_all_launcher_components(), {"pkg": ["pkg/Main"]})
            self.assertEqual(app_drv.build_launch_profiles(), [{"package_name": "pkg"}])
            self.assertTrue(app_drv.launch_from_home("pkg", "Main")["ok"])


if __name__ == "__main__":
    unittest.main()
