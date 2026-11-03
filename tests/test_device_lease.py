"""Unit tests for phone_pilot.core.device_lease."""

import os
import shutil
import tempfile
import time
import unittest
from phone_pilot.core.device_lease import (
    DeviceLeaseManager,
    device_lease,
)


class TestDeviceLease(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_device_locks_")
        self.manager = DeviceLeaseManager(lock_dir=self.test_dir)
        self.serial = "emulator-5554"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_acquire_and_release(self):
        # Initial status: unlocked
        status0 = self.manager.get_lock_status(self.serial)
        self.assertTrue(status0.get("ok"))
        self.assertFalse(status0.get("locked"))

        # Acquire lock
        res = self.manager.acquire_lock(
            serial=self.serial,
            session_id="agent-session-1",
            purpose="Testing checkout",
            ttl_s=10.0,
        )
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("acquired"))
        self.assertEqual(res.get("session_id"), "agent-session-1")

        # Status should now be locked
        status1 = self.manager.get_lock_status(self.serial)
        self.assertTrue(status1.get("ok"))
        self.assertTrue(status1.get("locked"))
        self.assertEqual(status1["lock_info"]["session_id"], "agent-session-1")
        self.assertGreater(status1["lock_info"]["remaining_s"], 0)

        # Release lock
        rel = self.manager.release_lock(self.serial, session_id="agent-session-1")
        self.assertTrue(rel.get("ok"))
        self.assertTrue(rel.get("released"))

        # Status back to unlocked
        status2 = self.manager.get_lock_status(self.serial)
        self.assertTrue(status2.get("ok"))
        self.assertFalse(status2.get("locked"))

    def test_contention_and_permission_denied(self):
        # Session 1 acquires lock
        res1 = self.manager.acquire_lock(
            serial=self.serial,
            session_id="session-1",
            ttl_s=30.0,
        )
        self.assertTrue(res1.get("ok"))

        # Session 2 tries to acquire without waiting -> busy
        res2 = self.manager.acquire_lock(
            serial=self.serial,
            session_id="session-2",
            wait_timeout_s=0.0,
        )
        self.assertFalse(res2.get("ok"))
        self.assertEqual(res2.get("error"), "device_busy")
        self.assertEqual(res2["current_holder"]["session_id"], "session-1")

        # Session 2 tries to release session 1's lock -> permission denied
        rel_fail = self.manager.release_lock(self.serial, session_id="session-2")
        self.assertFalse(rel_fail.get("ok"))
        self.assertEqual(rel_fail.get("error"), "permission_denied")

        # Session 2 forces release -> succeeds
        rel_force = self.manager.release_lock(self.serial, session_id="session-2", force=True)
        self.assertTrue(rel_force.get("ok"))
        self.assertTrue(rel_force.get("released"))

    def test_reentrant_renewal(self):
        # Same session acquiring again acts as reentrant update
        res1 = self.manager.acquire_lock(
            serial=self.serial,
            session_id="session-reentrant",
            ttl_s=10.0,
        )
        self.assertTrue(res1.get("ok"))

        # Re-acquire with same session_id succeeds
        res2 = self.manager.acquire_lock(
            serial=self.serial,
            session_id="session-reentrant",
            ttl_s=20.0,
        )
        self.assertTrue(res2.get("ok"))

        # Renew lock
        renew_res = self.manager.renew_lock(
            serial=self.serial,
            session_id="session-reentrant",
            ttl_s=50.0,
        )
        self.assertTrue(renew_res.get("ok"))
        self.assertTrue(renew_res.get("renewed"))
        self.assertGreater(renew_res["lock_info"]["remaining_s"], 40.0)

    def test_ttl_auto_expiration(self):
        # Acquire with short TTL (0.1s is clamped to 5s in acquire, so let's mock or set short ttl)
        # Direct write to meta file to test expiration logic
        now = time.time()
        import json
        from pathlib import Path
        meta_file = Path(self.test_dir) / f"{self.serial}.json"
        data = {
            "device_serial": self.serial,
            "session_id": "expired-session",
            "holder_pid": os.getpid(),
            "holder_name": "old_agent",
            "purpose": "old test",
            "acquired_at": now - 100,
            "expires_at": now - 10,  # expired 10s ago
            "ttl_s": 90.0,
        }
        with open(meta_file, "w") as f:
            json.dump(data, f)

        # Status check should detect expiration
        status = self.manager.get_lock_status(self.serial)
        self.assertTrue(status.get("ok"))
        self.assertFalse(status.get("locked"))
        self.assertEqual(status.get("stale_reason"), "ttl_expired")

        # New session can now acquire immediately
        res = self.manager.acquire_lock(self.serial, session_id="new-session", ttl_s=10.0)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("session_id"), "new-session")

    def test_context_manager(self):
        # Override global manager's lock dir temporarily
        import sys
        import phone_pilot.core.device_lease
        dl_mod = sys.modules["phone_pilot.core.device_lease"]
        orig_mgr = dl_mod._lease_manager
        dl_mod._lease_manager = self.manager
        try:
            teardown_called = []
            with device_lease(
                self.serial,
                session_id="ctx-session",
                purpose="ctx test",
                auto_teardown_fn=lambda s: teardown_called.append(s),
            ) as lock_res:
                self.assertTrue(lock_res.get("ok"))
                st = self.manager.get_lock_status(self.serial)
                self.assertTrue(st.get("locked"))

            # After exiting block
            self.assertEqual(teardown_called, [self.serial])
            st_after = self.manager.get_lock_status(self.serial)
            self.assertFalse(st_after.get("locked"))
        finally:
            dl_mod._lease_manager = orig_mgr


if __name__ == "__main__":
    unittest.main()
