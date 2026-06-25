"""
Tests for iOSInputDriver (design §4.1). WDA client fully mocked.
"""

from unittest.mock import MagicMock, patch

from phone_pilot.ios.driver import iOSInputDriver


SERIAL = "00008030-001234560A80802E"


def _run_fn_directly(serial, fn):
    """Stub for with_reconnect: invoke fn against a MagicMock client."""
    return fn(_run_fn_directly.client)


def _patch_bridge(client):
    """Patch driver's with_reconnect to run fn(client) once (success path)."""
    _run_fn_directly.client = client
    return patch(
        "phone_pilot.ios.driver._wda_call",
        side_effect=lambda serial, fn: fn(client),
    )


class TestTap:
    def test_tap_calls_wda_and_returns_ok(self):
        client = MagicMock()
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).tap(10, 20)
        assert res["ok"] is True
        assert res["x"] == 10 and res["y"] == 20
        client.tap.assert_called_once_with(10, 20)

    def test_swipe(self):
        client = MagicMock()
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).swipe(1, 2, 3, 4, duration_ms=500)
        assert res["ok"] is True
        client.swipe.assert_called_once()

    def test_long_press(self):
        client = MagicMock()
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).long_press(5, 6, duration_ms=800)
        assert res["ok"] is True
        client.tap_hold.assert_called_once()


class TestInputText:
    def test_input_text_success(self):
        client = MagicMock()
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).input_text("hello")
        assert res["ok"] is True
        client.send_keys.assert_called_once_with("hello")

    def test_input_text_with_enter(self):
        client = MagicMock()
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).input_text("hi", enter=True)
        assert res["ok"] is True
        # enter sends a newline via send_keys after the text
        assert client.send_keys.call_count >= 1

    def test_input_text_not_focused_returns_error(self):
        """WDA raises on no active field → no_active_input_field, not a raise (Plan T12)."""
        client = MagicMock()
        client.send_keys.side_effect = RuntimeError("invalid element state")
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).input_text("x")
        assert res["ok"] is False
        assert res["error"] == "no_active_input_field"


class TestKeyevent:
    def test_keyevent_home_maps_to_pressbutton(self):
        client = MagicMock()
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).keyevent("KEYCODE_HOME")
        assert res["ok"] is True
        client.press.assert_called_once()

    def test_keyevent_unmapped_returns_not_supported(self):
        client = MagicMock()
        with _patch_bridge(client):
            res = iOSInputDriver(SERIAL).keyevent("KEYCODE_NONEXISTENT_XYZ")
        assert res["ok"] is False
        assert res["error"] == "not_supported"
