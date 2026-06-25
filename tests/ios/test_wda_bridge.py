"""
Tests for phone_pilot.ios.wda_bridge — WDA client lifecycle, keep-alive reconnect,
and probe-before-reconnect error-code triage (design ADR-4 ↔ ADR-3).

All WDA HTTP and tunnel probes are mocked; no real device / facebook-wda needed.
"""

from unittest.mock import patch

import pytest

from phone_pilot.ios import wda_bridge


SERIAL = "00008030-001234560A80802E"


class _SessionLost(Exception):
    """Stand-in for a WDA XCUITest session-expiry error."""


class _ConnRefused(Exception):
    """Stand-in for a TCP connection-refused (WDA process gone / tunnel down)."""


@pytest.fixture(autouse=True)
def _clean_bridge_state():
    """Reset module-level cache/flags before and after each test."""
    wda_bridge._WdaClient = object  # pretend the class is importable
    wda_bridge._cache.clear()
    wda_bridge._failed_serials.clear()
    yield
    wda_bridge._WdaClient = None
    wda_bridge._cache.clear()
    wda_bridge._failed_serials.clear()


# ---------------------------------------------------------------------------
# with_reconnect — keep-alive (Plan T5)
# ---------------------------------------------------------------------------


def test_reconnect_on_session_lost():
    """First call fails with session-lost; after reset+rebuild the retry succeeds."""
    calls = {"n": 0}

    def fn(client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _SessionLost("sessionNotCreated")
        return {"ok": True, "tapped": True}

    with (
        patch.object(wda_bridge, "_classify_error", return_value="wda_session_lost"),
        patch.object(wda_bridge, "probe_tunnel", return_value={"ok": True}),
        patch.object(wda_bridge, "get_wda", return_value=object()),
    ):
        result = wda_bridge.with_reconnect(SERIAL, fn)

    assert result == {"ok": True, "tapped": True}
    assert calls["n"] == 2  # retried exactly once


def test_mid_session_tunnel_drop_returns_tunnel_error():
    """If reconnect's tunnel probe fails, return ios_tunnel_unavailable, not session_lost."""

    def fn(client):
        raise _ConnRefused("connection refused")

    with (
        patch.object(
            wda_bridge, "_classify_error", return_value="wda_connection_refused"
        ),
        patch.object(
            wda_bridge,
            "probe_tunnel",
            return_value={"ok": False, "error": "ios_tunnel_unavailable", "hint": "x"},
        ),
        patch.object(wda_bridge, "get_wda", return_value=object()),
    ):
        result = wda_bridge.with_reconnect(SERIAL, fn)

    assert result["ok"] is False
    assert result["error"] == "ios_tunnel_unavailable"
    # tunnel error must NOT be misreported as a session loss
    assert "session" not in str(result.get("error", ""))


def test_unavailable_returns_error_and_marks_failed():
    """Tunnel OK but WDA still refuses after retry → wda_connection_refused, serial flagged."""

    def fn(client):
        raise _ConnRefused("connection refused")

    with (
        patch.object(
            wda_bridge, "_classify_error", return_value="wda_connection_refused"
        ),
        patch.object(wda_bridge, "probe_tunnel", return_value={"ok": True}),
        patch.object(wda_bridge, "get_wda", return_value=object()),
    ):
        result = wda_bridge.with_reconnect(SERIAL, fn)

    assert result["ok"] is False
    assert result["error"] == "wda_connection_refused"
    assert SERIAL in wda_bridge._failed_serials


def test_success_first_try_no_reconnect():
    """Happy path: fn succeeds on first call, no reset/probe."""

    def fn(client):
        return {"ok": True}

    with (
        patch.object(wda_bridge, "probe_tunnel") as probe,
        patch.object(wda_bridge, "get_wda", return_value=object()),
    ):
        result = wda_bridge.with_reconnect(SERIAL, fn)

    assert result == {"ok": True}
    probe.assert_not_called()


# ---------------------------------------------------------------------------
# get_wda / reset_wda / is_wda_available
# ---------------------------------------------------------------------------


def test_get_wda_caches_instance():
    with patch.object(wda_bridge, "_connect_client", return_value="CLIENT"):
        c1 = wda_bridge.get_wda(SERIAL)
        c2 = wda_bridge.get_wda(SERIAL)
    assert c1 == "CLIENT"
    assert c1 is c2  # cached


def test_get_wda_failed_serial_raises_until_reset():
    wda_bridge._failed_serials.add(SERIAL)
    with pytest.raises(RuntimeError, match="reset_wda"):
        wda_bridge.get_wda(SERIAL)


def test_reset_wda_clears_cache_and_failed_flag():
    wda_bridge._cache[SERIAL] = "CLIENT"
    wda_bridge._failed_serials.add(SERIAL)
    wda_bridge.reset_wda(SERIAL)
    assert SERIAL not in wda_bridge._cache
    assert SERIAL not in wda_bridge._failed_serials


def test_is_wda_available_non_destructive():
    with patch.object(wda_bridge, "get_wda", side_effect=RuntimeError("nope")):
        assert wda_bridge.is_wda_available(SERIAL) is False
    with patch.object(wda_bridge, "get_wda", return_value=object()):
        assert wda_bridge.is_wda_available(SERIAL) is True


# ---------------------------------------------------------------------------
# import guard (Plan A3 / mirrors hmdriver_bridge)
# ---------------------------------------------------------------------------


def test_ensure_wda_class_import_error_message():
    wda_bridge._WdaClient = None
    with patch.dict("sys.modules", {"wda": None}):
        with pytest.raises(ImportError, match=r"phone-pilot\[ios\]"):
            wda_bridge._ensure_wda_class()
