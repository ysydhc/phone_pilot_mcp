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
    wda_bridge._serial_urls.clear()
    wda_bridge._wda_runner_absent_until.clear()
    yield
    wda_bridge._WdaClient = None
    wda_bridge._cache.clear()
    wda_bridge._failed_serials.clear()
    wda_bridge._serial_urls.clear()
    wda_bridge._wda_runner_absent_until.clear()


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


# ---------------------------------------------------------------------------
# Self-healing cooldown & per-serial URL / multi-simulator isolation
# ---------------------------------------------------------------------------


def test_with_reconnect_short_circuits_during_cooldown_and_heals_after_expiry():
    """Within the 2.5s cooldown window, with_reconnect short-circuits with cooldown=True; after expiry it self-heals."""
    wda_bridge._failed_serials.add(SERIAL)

    with (
        patch.object(wda_bridge, "_connect_client") as mock_connect,
        patch.object(wda_bridge, "probe_tunnel") as mock_probe,
    ):
        short_circuit = wda_bridge.with_reconnect(
            SERIAL, lambda c: {"ok": True, "client": c}
        )

    assert short_circuit == {
        "ok": False,
        "error": "wda_connection_refused",
        "cooldown": True,
        "device_serial": SERIAL,
    }
    mock_connect.assert_not_called()
    mock_probe.assert_not_called()

    # Expire cooldown -> next with_reconnect reconnects and succeeds
    wda_bridge._failed_serials._timestamps[SERIAL] -= (
        wda_bridge._FAILURE_COOLDOWN_S + 1.0
    )

    with (
        patch.object(wda_bridge, "_connect_client", return_value="RECOVERED_CLIENT"),
        patch.object(wda_bridge, "probe_tunnel", return_value={"ok": True}),
    ):
        result = wda_bridge.with_reconnect(SERIAL, lambda c: {"ok": True, "client": c})

    assert result == {"ok": True, "client": "RECOVERED_CLIENT"}
    assert SERIAL not in wda_bridge._failed_serials
    assert wda_bridge._cache[SERIAL] == "RECOVERED_CLIENT"


def test_simulator_wda_negative_cache_and_install_invalidation():
    """When WebDriverAgentRunner is absent on simulator, negative cache suppresses listapps for 30s until install_app."""
    import subprocess
    from phone_pilot.ios import system

    empty_listapps = '    "com.apple.Preferences" = {\n    };\n'
    with (
        patch(
            "phone_pilot.ios.wda_bridge.pmd_utils.detect_form_factor",
            return_value="simulator",
        ),
        patch(
            "phone_pilot.ios.wda_bridge.pmd_utils.simctl_prefix",
            return_value=["xcrun", "simctl"],
        ),
        patch(
            "phone_pilot.ios.wda_bridge.pmd_runner.run",
            return_value=subprocess.CompletedProcess(["simctl"], 0, empty_listapps, ""),
        ) as mock_run,
    ):
        assert wda_bridge._try_launch_simulator_wda(SERIAL) is False
        assert mock_run.call_count == 1
        # Second call within 30s hits negative cache -> no extra simctl subprocess
        assert wda_bridge._try_launch_simulator_wda(SERIAL) is False
        assert mock_run.call_count == 1

    # Installing an app clears the negative cache for that serial
    with (
        patch.object(system.pmd_utils, "detect_form_factor", return_value="simulator"),
        patch.object(system.pmd_utils, "simctl_prefix", return_value=["simctl"]),
        patch.object(
            system.pmd_runner,
            "run",
            return_value=subprocess.CompletedProcess(["simctl"], 0, "", ""),
        ),
    ):
        res = system.install_app(SERIAL, "/tmp/WebDriverAgentRunner-Runner.app")
    assert res["ok"] is True
    assert SERIAL not in wda_bridge._wda_runner_absent_until


def test_get_wda_cooldown_expires_automatically():
    """Direct get_wda raises within cooldown window, then automatically retries after cooldown expires."""
    with patch.object(
        wda_bridge, "_connect_client", side_effect=_ConnRefused("refused")
    ):
        with pytest.raises(RuntimeError, match="Failed to initialize WDA"):
            wda_bridge.get_wda(SERIAL)

    assert SERIAL in wda_bridge._failed_serials

    # Immediately within cooldown -> still raises
    with pytest.raises(RuntimeError, match="reset_wda"):
        wda_bridge.get_wda(SERIAL)

    # Simulate cooldown expiration
    wda_bridge._failed_serials._timestamps[SERIAL] -= (
        wda_bridge._FAILURE_COOLDOWN_S + 1.0
    )

    with patch.object(wda_bridge, "_connect_client", return_value="HEALED_CLIENT"):
        client = wda_bridge.get_wda(SERIAL)

    assert client == "HEALED_CLIENT"
    assert SERIAL not in wda_bridge._failed_serials


def test_per_serial_url_resolution_and_cache_isolation(monkeypatch):
    """Different device serials resolve dedicated WDA URLs and isolate cached clients."""
    sim_a = "606174FC-4664-48B9-9A9B-061157DC2819"
    sim_b = "11112222-3333-4444-5555-666677778888"

    wda_bridge.set_wda_url(sim_a, "8101")
    monkeypatch.setenv(
        "PHONE_PILOT_WDA_URL_11112222_3333_4444_5555_666677778888",
        "http://127.0.0.1:8102",
    )

    assert wda_bridge.wda_base_url(sim_a) == "http://127.0.0.1:8101"
    assert wda_bridge.wda_base_url(sim_b) == "http://127.0.0.1:8102"
    assert wda_bridge.wda_base_url("other-serial") == "http://127.0.0.1:8100"

    created_urls = []

    def fake_wda_cls(url):
        created_urls.append(url)
        return {"url": url}

    wda_bridge._WdaClient = fake_wda_cls
    c_a = wda_bridge.get_wda(sim_a)
    c_b = wda_bridge.get_wda(sim_b)

    assert c_a == {"url": "http://127.0.0.1:8101"}
    assert c_b == {"url": "http://127.0.0.1:8102"}
    assert wda_bridge._cache[sim_a] is not wda_bridge._cache[sim_b]


def test_verify_client_serial_mismatch_raises():
    """If WDA status() reports a different device UDID, _connect_client raises RuntimeError."""

    class FakeClient:
        def __init__(self, url):
            self.url = url

        def status(self):
            return {"ios": {"udid": "DIFFERENT-UDID"}}

    wda_bridge._WdaClient = FakeClient
    with pytest.raises(RuntimeError, match="WDA target mismatch"):
        wda_bridge.get_wda(SERIAL)


def test_simulator_auto_launch_wda_on_reconnect():
    """When reconnect probes a simulator, it attempts to auto-launch installed WebDriverAgentRunner."""
    import subprocess

    listapps_out = '    "com.facebook.WebDriverAgentRunner.xctrunner" = {\n    };\n'
    with (
        patch(
            "phone_pilot.ios.wda_bridge.pmd_utils.detect_form_factor",
            return_value="simulator",
        ),
        patch(
            "phone_pilot.ios.wda_bridge.pmd_utils.simctl_prefix",
            return_value=["xcrun", "simctl"],
        ),
        patch(
            "phone_pilot.ios.wda_bridge.pmd_runner.run",
            side_effect=[
                subprocess.CompletedProcess(["simctl"], 0, listapps_out, ""),
                subprocess.CompletedProcess(
                    ["simctl"],
                    0,
                    "com.facebook.WebDriverAgentRunner.xctrunner: 1234",
                    "",
                ),
            ],
        ) as mock_run,
    ):
        launched = wda_bridge._try_launch_simulator_wda(SERIAL)

    assert launched is True
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[1].args[0] == [
        "xcrun",
        "simctl",
        "launch",
        SERIAL,
        "com.facebook.WebDriverAgentRunner.xctrunner",
    ]
