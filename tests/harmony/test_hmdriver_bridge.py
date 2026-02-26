"""
Tests for hmdriver_bridge module (caching and lifecycle).
"""

import unittest
from unittest.mock import patch

from phone_pilot.harmony.hmdriver_bridge import (
    get_hmdriver,
    has_hmdriver,
    reset_hmdriver,
    is_hmdriver_available,
    release_all,
    _cache,
    _failed_serials,
)


class MockDriver:
    def __init__(self, serial):
        self.serial = serial


class TestHmDriverBridge(unittest.TestCase):

    def setUp(self):
        """Clear cache before each test."""
        _cache.clear()
        _failed_serials.clear()

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", MockDriver)
    def test_get_hmdriver_creates_instance(self):
        drv = get_hmdriver("serial1")
        self.assertIsInstance(drv, MockDriver)
        self.assertEqual(drv.serial, "serial1")

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", MockDriver)
    def test_get_hmdriver_caches(self):
        drv1 = get_hmdriver("serial1")
        drv2 = get_hmdriver("serial1")
        self.assertIs(drv1, drv2)

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", MockDriver)
    def test_has_hmdriver(self):
        self.assertFalse(has_hmdriver("serial1"))
        get_hmdriver("serial1")
        self.assertTrue(has_hmdriver("serial1"))

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", MockDriver)
    def test_reset_hmdriver(self):
        get_hmdriver("serial1")
        self.assertTrue(has_hmdriver("serial1"))
        reset_hmdriver("serial1")
        self.assertFalse(has_hmdriver("serial1"))

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", side_effect=Exception("connect failed"))
    def test_connection_failure_marks_failed(self, _):
        with self.assertRaises(RuntimeError):
            get_hmdriver("bad_serial")
        self.assertIn("bad_serial", _failed_serials)
        # Second call should fail immediately
        with self.assertRaises(RuntimeError):
            get_hmdriver("bad_serial")

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", side_effect=Exception("fail"))
    def test_reset_clears_failure_flag(self, _):
        with self.assertRaises(RuntimeError):
            get_hmdriver("fail_serial")
        reset_hmdriver("fail_serial")
        self.assertNotIn("fail_serial", _failed_serials)

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", MockDriver)
    def test_is_hmdriver_available(self):
        self.assertTrue(is_hmdriver_available("serial1"))

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", side_effect=Exception("fail"))
    def test_is_hmdriver_available_false(self, _):
        self.assertFalse(is_hmdriver_available("bad"))

    @patch("phone_pilot.harmony.hmdriver_bridge._HmDriver", MockDriver)
    def test_release_all(self):
        get_hmdriver("s1")
        get_hmdriver("s2")
        self.assertEqual(len(_cache), 2)
        release_all()
        self.assertEqual(len(_cache), 0)


if __name__ == "__main__":
    unittest.main()
