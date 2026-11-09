"""Tests for iOS routing in mcp/_helpers.py (design §5 / Plan C2)."""

from unittest.mock import MagicMock, patch
import pytest

from phone_pilot.mcp import _helpers


UDID_REAL = "00008030-001234560A80802E"
UDID_SIM = "11111111-2222-3333-4444-555555555555"
UDID_40HEX = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"


@pytest.fixture(autouse=True)
def _reset_mcp_device_cache():
    _helpers.invalidate_device_cache()
    yield
    _helpers.invalidate_device_cache()


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

    def test_get_driver_auto_resolves_ios(self):
        with (
            patch.object(_helpers, "_scan_android_serials", return_value=[]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_SIM]),
        ):
            drv = _helpers.get_driver(UDID_SIM, platform="auto")
        assert drv.platform == "ios"
        assert drv.device_serial == UDID_SIM

    def test_get_driver_auto_resolves_android(self):
        with (
            patch.object(_helpers, "_scan_android_serials", return_value=["emulator-5554"]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_SIM]),
        ):
            drv = _helpers.get_driver("emulator-5554", platform="auto")
        assert drv.platform == "android"
        assert drv.device_serial == "emulator-5554"


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


class TestNavToolsIos:
    @pytest.mark.asyncio
    async def test_phone_go_home_ios(self):
        from phone_pilot.mcp import _nav_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=[]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_REAL]),
            patch("phone_pilot.mcp._nav_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.go_home.return_value = {"ok": True, "engine": "wda"}
            mock_get_drv.return_value = mock_drv

            res = await _nav_tools.phone_go_home(device_serial=UDID_REAL, platform="ios")
            assert res["ok"] is True
            assert res["platform"] == "ios"
            assert res["device_serial"] == UDID_REAL
            mock_drv.go_home.assert_called_once()

    @pytest.mark.asyncio
    async def test_phone_go_back_ios(self):
        from phone_pilot.mcp import _nav_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=[]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_REAL]),
            patch("phone_pilot.mcp._nav_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.go_back.return_value = {"ok": True, "engine": "wda"}
            mock_get_drv.return_value = mock_drv

            res = await _nav_tools.phone_go_back(device_serial=UDID_REAL, platform="ios")
            assert res["ok"] is True
            assert res["platform"] == "ios"
            mock_drv.go_back.assert_called_once()

    @pytest.mark.asyncio
    async def test_phone_unlock_ios(self):
        from phone_pilot.mcp import _nav_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=[]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_REAL]),
            patch("phone_pilot.mcp._nav_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.unlock.return_value = {"ok": True, "engine": "wda"}
            mock_get_drv.return_value = mock_drv

            res = await _nav_tools.phone_unlock(device_serial=UDID_REAL, pin="", platform="ios")
            assert res["ok"] is True
            assert res["platform"] == "ios"
            mock_drv.unlock.assert_called_once_with(pin=None)

    @pytest.mark.asyncio
    async def test_phone_open_deeplink_ios(self):
        from phone_pilot.mcp import _nav_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=[]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_REAL]),
            patch("phone_pilot.mcp._nav_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.open_deeplink.return_value = {"ok": True, "engine": "wda"}
            mock_get_drv.return_value = mock_drv

            res = await _nav_tools.phone_open_deeplink(
                uri="myapp://home",
                package="com.example.app",
                device_serial=UDID_REAL,
                platform="ios",
            )
            assert res["ok"] is True
            assert res["uri"] == "myapp://home"
            assert res["platform"] == "ios"
            mock_drv.open_deeplink.assert_called_once_with("myapp://home", package="com.example.app")

    @pytest.mark.asyncio
    async def test_phone_clear_background_ios(self):
        from phone_pilot.mcp import _nav_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=[]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_REAL]),
            patch("phone_pilot.mcp._nav_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.clear_background.return_value = {"ok": False, "error": "not_supported"}
            mock_get_drv.return_value = mock_drv

            res = await _nav_tools.phone_clear_background(device_serial=UDID_REAL, platform="ios")
            assert res["ok"] is False
            assert res["error"] == "not_supported"
            assert res["platform"] == "ios"
            mock_drv.clear_background.assert_called_once()

    @pytest.mark.asyncio
    async def test_phone_clear_data_ios(self):
        from phone_pilot.mcp import _nav_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=[]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_REAL]),
            patch("phone_pilot.mcp._nav_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.app.clear_data.return_value = {"ok": False, "error": "not_supported"}
            mock_get_drv.return_value = mock_drv

            res = await _nav_tools.phone_clear_data(
                package="com.example.app", device_serial=UDID_REAL, platform="ios"
            )
            assert res["ok"] is False
            assert res["error"] == "not_supported"
            assert res["package"] == "com.example.app"
            assert res["platform"] == "ios"
            mock_drv.app.clear_data.assert_called_once_with("com.example.app")


class TestAutoPlatformMcpToolsIos:
    """Verify MCP tools infer platform='ios' when only iOS device_serial is passed."""

    @pytest.mark.asyncio
    async def test_phone_tap_and_tap_text_auto_ios(self):
        from phone_pilot.mcp import _input_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=["emulator-5554"]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_SIM]),
            patch("phone_pilot.mcp._input_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.platform = "ios"
            mock_drv.input.tap.return_value = {"ok": True, "engine": "wda"}
            mock_drv.ui.find_elements.return_value = [
                {"center_x": 120, "center_y": 240, "text": "Allow"}
            ]
            mock_get_drv.return_value = mock_drv

            tap_res = await _input_tools.phone_tap(device_serial=UDID_SIM, x=100, y=200)
            assert tap_res["ok"] is True
            assert tap_res["platform"] == "ios"
            assert tap_res["device_serial"] == UDID_SIM
            mock_get_drv.assert_called_with(UDID_SIM, "ios")

            tap_text_res = await _input_tools.phone_tap_text(
                device_serial=UDID_SIM, text="Allow", wait_s=0.0
            )
            assert tap_text_res["ok"] is True
            assert tap_text_res["platform"] == "ios"
            assert tap_text_res["device_serial"] == UDID_SIM
            mock_get_drv.assert_called_with(UDID_SIM, "ios")

    @pytest.mark.asyncio
    async def test_phone_launch_and_force_stop_auto_ios(self):
        from phone_pilot.mcp import _app_tools

        with (
            patch.object(_helpers, "_scan_android_serials", return_value=["emulator-5554"]),
            patch.object(_helpers, "_scan_harmony_serials", return_value=[]),
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_SIM]),
            patch("phone_pilot.mcp._app_tools.get_driver") as mock_get_drv,
        ):
            mock_drv = MagicMock()
            mock_drv.platform = "ios"
            mock_drv.app.launch_app.return_value = {
                "ok": True,
                "package": "com.example.demo",
                "engine": "simctl",
            }
            mock_drv.app.force_stop.return_value = {
                "ok": True,
                "package": "com.example.demo",
                "engine": "simctl",
            }
            mock_get_drv.return_value = mock_drv

            launch_res = await _app_tools.phone_launch_app(
                device_serial=UDID_SIM,
                package="com.example.demo",
                args=["--autotest", "1"],
            )
            assert launch_res["ok"] is True
            assert launch_res["platform"] == "ios"
            assert launch_res["device_serial"] == UDID_SIM
            mock_get_drv.assert_called_with(UDID_SIM, "ios")
            mock_drv.app.launch_app.assert_called_once_with(
                "com.example.demo", None, args=["--autotest", "1"]
            )

            stop_res = await _app_tools.phone_force_stop(
                device_serial=UDID_SIM, package="com.example.demo"
            )
            assert stop_res["ok"] is True
            assert stop_res["platform"] == "ios"
            assert stop_res["device_serial"] == UDID_SIM
            mock_get_drv.assert_called_with(UDID_SIM, "ios")

    def test_resolve_ios_skips_ensure_adb_env(self):
        with (
            patch("phone_pilot.android.adb.utils.ensure_adb_env") as mock_adb_env,
            patch.object(_helpers, "_scan_ios_serials", return_value=[UDID_SIM]),
        ):
            serial, plat, err = _helpers._resolve_device_serial(UDID_SIM, "ios")
        assert err is None
        assert serial == UDID_SIM
        assert plat == "ios"
        mock_adb_env.assert_not_called()

    def test_scan_ios_serials_ttl_cache_and_invalidation(self):
        with patch(
            "phone_pilot.ios.pmd.utils.list_ios_serials", return_value=[UDID_SIM]
        ) as mock_list:
            assert _helpers._scan_ios_serials() == [UDID_SIM]
            assert _helpers._scan_ios_serials() == [UDID_SIM]
            assert mock_list.call_count == 1

            _helpers.invalidate_device_cache()
            assert _helpers._scan_ios_serials() == [UDID_SIM]
            assert mock_list.call_count == 2

    def test_phone_screenshot_sync_rejects_empty_bytes(self, tmp_path):
        from phone_pilot.mcp._device_tools import _phone_screenshot_sync

        save_file = tmp_path / "empty.png"
        mock_drv = MagicMock()
        mock_drv.platform = "ios"
        mock_drv.screen.screenshot.return_value = b""

        with patch(
            "phone_pilot.mcp._device_tools._resolve_driver_and_platform",
            return_value=(mock_drv, UDID_SIM, "ios"),
        ):
            res = _phone_screenshot_sync(UDID_SIM, "ios", str(save_file))

        assert res == {
            "ok": False,
            "error": "screenshot_failed",
            "device_serial": UDID_SIM,
            "platform": "ios",
        }
        assert not save_file.exists()

