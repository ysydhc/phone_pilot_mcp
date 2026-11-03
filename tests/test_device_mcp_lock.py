"""Unit tests for MCP device lock tools and _with_device concurrency guard."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from phone_pilot.core.device_lease import (
    DeviceLeaseManager,
    acquire_device_lock,
    release_device_lock,
)
from phone_pilot.mcp._helpers import _resolve_device_serial, _with_device
from phone_pilot.mcp._device_tools import (
    _phone_device_acquire_lock_sync,
    _phone_device_lock_status_sync,
    _phone_device_release_lock_sync,
    _phone_device_renew_lock_sync,
    _phone_list_devices_sync,
)


class TestMcpDeviceLock(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_mcp_locks_")
        self.orig_lock_dir = os.environ.get("PHONE_PILOT_LOCK_DIR")
        os.environ["PHONE_PILOT_LOCK_DIR"] = self.test_dir
        self.serial = "mock-serial-123"

        # Patch global lease manager to use the temporary lock dir
        import sys
        import phone_pilot.core.device_lease
        dl_mod = sys.modules["phone_pilot.core.device_lease"]
        self.mgr = DeviceLeaseManager(lock_dir=self.test_dir)
        self.orig_mgr = dl_mod._lease_manager
        dl_mod._lease_manager = self.mgr

    def tearDown(self):
        import sys
        dl_mod = sys.modules.get("phone_pilot.core.device_lease")
        if dl_mod and hasattr(self, "orig_mgr"):
            dl_mod._lease_manager = self.orig_mgr
        if self.orig_lock_dir is not None:
            os.environ["PHONE_PILOT_LOCK_DIR"] = self.orig_lock_dir
        else:
            os.environ.pop("PHONE_PILOT_LOCK_DIR", None)
        os.environ.pop("PHONE_PILOT_DEVICE_SERIAL", None)
        os.environ.pop("PHONE_PILOT_SESSION_ID", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("phone_pilot.mcp._helpers._scan_android_serials")
    def test_env_serial_binding(self, mock_scan):
        mock_scan.return_value = ["device-A", "device-B"]

        # Default without env -> picks first device
        s, p, err = _resolve_device_serial(None, "android")
        self.assertEqual(s, "device-A")

        # Set env var -> picks specified device
        os.environ["PHONE_PILOT_DEVICE_SERIAL"] = "device-B"
        s2, p2, err2 = _resolve_device_serial(None, "android")
        self.assertEqual(s2, "device-B")

    @patch("phone_pilot.mcp._device_tools._resolve_device_serial")
    def test_mcp_lock_lifecycle(self, mock_resolve):
        mock_resolve.return_value = (self.serial, "android", None)

        # Check status initially
        st = _phone_device_lock_status_sync(self.serial)
        self.assertTrue(st["ok"])
        self.assertFalse(st["locked"])

        # Acquire lock via tool sync
        acq = _phone_device_acquire_lock_sync(
            self.serial,
            session_id="agent-x",
            purpose="test mcp lock",
            ttl_s=20.0,
        )
        self.assertTrue(acq["ok"])
        self.assertTrue(acq["acquired"])

        # Check status again
        st2 = _phone_device_lock_status_sync(self.serial)
        self.assertTrue(st2["locked"])
        self.assertEqual(st2["lock_info"]["session_id"], "agent-x")

        # Renew lock
        ren = _phone_device_renew_lock_sync(self.serial, session_id="agent-x", ttl_s=60.0)
        self.assertTrue(ren["ok"])
        self.assertTrue(ren["renewed"])

        # Release lock
        rel = _phone_device_release_lock_sync(self.serial, session_id="agent-x")
        self.assertTrue(rel["ok"])
        self.assertTrue(rel["released"])

        # Check unlocked
        st3 = _phone_device_lock_status_sync(self.serial)
        self.assertFalse(st3["locked"])

    @patch("phone_pilot.mcp._helpers._resolve_device_serial")
    def test_with_device_concurrency_guard(self, mock_resolve):
        mock_resolve.return_value = (self.serial, "android", None)

        dummy_action_called = []

        def dummy_action(dev, plat):
            dummy_action_called.append((dev, plat))
            return {"ok": True, "action": "tapped"}

        # 1. When unlocked, action passes
        res1 = _with_device(dummy_action, self.serial, "android")
        self.assertTrue(res1["ok"])
        self.assertEqual(len(dummy_action_called), 1)

        # 2. Acquire lock with session-A
        acquire_device_lock(self.serial, session_id="session-A", ttl_s=30.0)

        # 3. Call without matching session_id -> guarded / blocked
        res2 = _with_device(dummy_action, self.serial, "android")
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["error"], "device_busy_locked")
        self.assertIn("独占锁定中", res2["note"])
        # Action was NOT executed
        self.assertEqual(len(dummy_action_called), 1)

        # 4. Call with matching session_id via env var -> allowed
        os.environ["PHONE_PILOT_SESSION_ID"] = "session-A"
        res3 = _with_device(dummy_action, self.serial, "android")
        self.assertTrue(res3["ok"])
        self.assertEqual(len(dummy_action_called), 2)

    @patch("phone_pilot.android.adb.runner.CommandRunner.run")
    def test_list_devices_shows_lock_status(self, mock_cmd_run):
        from unittest.mock import MagicMock
        mock_proc = MagicMock()
        mock_proc.stdout = "dev-1          device product:p1 model:m1 device:d1\n"
        mock_cmd_run.return_value = mock_proc

        # Initial list -> unlocked
        res1 = _phone_list_devices_sync()
        self.assertTrue(res1["ok"])
        devices = [d for d in res1.get("devices", []) if d.get("serial") == "dev-1"]
        self.assertTrue(len(devices) > 0)
        self.assertFalse(devices[0].get("locked"))

        # Lock dev-1
        acquire_device_lock("dev-1", session_id="session-list-test", purpose="testing list")

        # Second list -> locked=True
        res2 = _phone_list_devices_sync()
        devices2 = [d for d in res2.get("devices", []) if d.get("serial") == "dev-1"]
        self.assertTrue(devices2[0].get("locked"))
        self.assertEqual(devices2[0]["lock_info"]["session_id"], "session-list-test")


if __name__ == "__main__":
    unittest.main()
