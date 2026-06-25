"""Tests for iOS routing in mcp/_helpers.py (design §5 / Plan C2)."""

from unittest.mock import patch

from phone_pilot.mcp import _helpers


UDID_REAL = "00008030-001234560A80802E"
UDID_SIM = "11111111-2222-3333-4444-555555555555"
UDID_40HEX = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


def _no_android_no_harmony():
    """Patch adb/hdc scans to return nothing, isolating iOS resolution."""
    return patch.object(
        _helpers,
        "_resolve_device_serial",
        wraps=_helpers._resolve_device_serial,
    )


class TestGetDriverIos:
    def test_get_driver_ios_returns_ios_driver(self):
        drv = _helpers.get_driver(UDID_REAL, platform="ios")
        assert drv.platform == "ios"
        assert drv.device_serial == UDID_REAL


class TestResolveIosSerial:
    def test_resolve_explicit_ios_serial(self):
        with (
            patch(
                "phone_pilot.mcp._helpers.ensure_adb_env",
                create=True,
                side_effect=lambda: None,
            ),
            patch.object(
                _helpers, "_scan_android_serials", return_value=[], create=True
            ),
            patch.object(
                _helpers, "_scan_harmony_serials", return_value=[], create=True
            ),
            patch.object(
                _helpers, "_scan_ios_serials", return_value=[UDID_REAL], create=True
            ),
        ):
            serial, plat, err = _helpers._resolve_device_serial(UDID_REAL, "ios")
        assert err is None
        assert serial == UDID_REAL
        assert plat == "ios"

    def test_udid_format_compatibility(self):
        for udid in (UDID_40HEX, UDID_REAL):
            with (
                patch.object(
                    _helpers, "_scan_android_serials", return_value=[], create=True
                ),
                patch.object(
                    _helpers, "_scan_harmony_serials", return_value=[], create=True
                ),
                patch.object(
                    _helpers, "_scan_ios_serials", return_value=[udid], create=True
                ),
            ):
                serial, plat, err = _helpers._resolve_device_serial(udid, "ios")
            assert err is None, f"failed for {udid}"
            assert plat == "ios"

    def test_ios_device_not_found_returns_available_ios(self):
        with (
            patch.object(
                _helpers, "_scan_android_serials", return_value=[], create=True
            ),
            patch.object(
                _helpers, "_scan_harmony_serials", return_value=[], create=True
            ),
            patch.object(
                _helpers, "_scan_ios_serials", return_value=[UDID_REAL], create=True
            ),
        ):
            serial, plat, err = _helpers._resolve_device_serial("ghost", "ios")
        assert err is not None
        assert err["ok"] is False
        assert UDID_REAL in err.get("available_ios", [])

    def test_auto_priority_android_then_harmony_then_ios(self):
        """auto with all platforms present → android wins (design §8 note)."""
        with (
            patch.object(
                _helpers,
                "_scan_android_serials",
                return_value=["ANDROID1"],
                create=True,
            ),
            patch.object(
                _helpers,
                "_scan_harmony_serials",
                return_value=["HARMONY1"],
                create=True,
            ),
            patch.object(
                _helpers, "_scan_ios_serials", return_value=[UDID_REAL], create=True
            ),
        ):
            serial, plat, err = _helpers._resolve_device_serial(None, "auto")
        assert err is None
        assert plat == "android"

    def test_auto_selects_ios_when_only_ios(self):
        with (
            patch.object(
                _helpers, "_scan_android_serials", return_value=[], create=True
            ),
            patch.object(
                _helpers, "_scan_harmony_serials", return_value=[], create=True
            ),
            patch.object(
                _helpers, "_scan_ios_serials", return_value=[UDID_REAL], create=True
            ),
        ):
            serial, plat, err = _helpers._resolve_device_serial(None, "auto")
        assert err is None
        assert plat == "ios"
        assert serial == UDID_REAL
