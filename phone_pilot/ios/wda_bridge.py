"""
Bridge layer between phone_pilot and facebook-wda (WebDriverAgent).

Manages WDA client lifecycle with caching and failure tracking, mirroring
harmony/hmdriver_bridge.py. Implements the design's keep-alive reconnect
(ADR-4) crossed with the tunnel diagnostic (ADR-3):

  with_reconnect(serial, fn):
    1. run fn(client)
    2. on failure → reset, but FIRST probe the tunnel:
         - tunnel down  → return {"error": "ios_tunnel_unavailable"}  (ADR-3)
         - tunnel up     → rebuild session, retry fn once
    3. retry still fails → return {"error": "wda_connection_refused"},
       and flag the serial in _failed_serials.

Error codes (FROZEN, Plan §1.2):
  ios_tunnel_unavailable | wda_session_lost | wda_connection_refused
"""

from __future__ import annotations

import logging

from phone_pilot.ios.pmd.utils import probe_tunnel

logger = logging.getLogger(__name__)

# Lazy-imported to avoid hard crash if facebook-wda is not installed
_WdaClient = None
_cache: dict[str, object] = {}
_failed_serials: set[str] = set()


# ---------------------------------------------------------------------------
# Lazy import guard (mirrors hmdriver_bridge._ensure_hmdriver_class)
# ---------------------------------------------------------------------------


def _ensure_wda_class() -> None:
    """Lazy import facebook-wda Client class."""
    global _WdaClient
    if _WdaClient is not None:
        return
    try:
        import wda  # type: ignore[import-untyped]

        _WdaClient = wda.Client
    except ImportError as exc:
        raise ImportError(
            "facebook-wda is required for iOS support. "
            "Install it with: pip install phone-pilot[ios]"
        ) from exc


def wda_base_url() -> str:
    """
    WDA base URL the client connects to.

    Defaults to the local forwarded port (127.0.0.1:8100, ADR-9 — bound to
    loopback only). Override with PHONE_PILOT_WDA_URL when WDA is forwarded to
    a different port.
    """
    import os

    return (os.environ.get("PHONE_PILOT_WDA_URL") or "http://127.0.0.1:8100").strip()


def _connect_client(serial: str):
    """
    Build a live WDA client for a serial. Override-seam for tests.

    Connects to the loopback-forwarded WDA HTTP server (ADR-9).
    """
    _ensure_wda_class()
    return _WdaClient(wda_base_url())  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Cache lifecycle
# ---------------------------------------------------------------------------


def get_wda(serial: str):
    """
    Get or create a cached WDA client for a device.

    Raises:
        ImportError: facebook-wda not installed.
        RuntimeError: serial previously failed (call reset_wda to retry),
            or connection attempt failed.
    """
    if serial in _cache:
        return _cache[serial]

    if serial in _failed_serials:
        raise RuntimeError(
            f"WDA previously failed to connect to device [{serial}]. "
            "Call reset_wda(serial) to retry."
        )

    try:
        client = _connect_client(serial)
        _cache[serial] = client
        logger.info("WDA connected to device [%s]", serial)
        return client
    except ImportError:
        raise
    except Exception as exc:
        _failed_serials.add(serial)
        raise RuntimeError(
            f"Failed to initialize WDA for device [{serial}]: {exc}"
        ) from exc


def reset_wda(serial: str) -> None:
    """Release the cached WDA client and clear the failure flag for a serial."""
    _failed_serials.discard(serial)
    if _cache.pop(serial, None) is not None:
        logger.info("WDA instance released for device [%s]", serial)


def is_wda_available(serial: str) -> bool:
    """Non-destructive probe: True if WDA can connect, False otherwise."""
    try:
        get_wda(serial)
        return True
    except Exception:
        return False


def release_all() -> None:
    """Release all cached WDA client instances."""
    for serial in list(_cache):
        reset_wda(serial)


# ---------------------------------------------------------------------------
# Error classification + keep-alive reconnect (ADR-4 ↔ ADR-3)
# ---------------------------------------------------------------------------


def _classify_error(exc: Exception) -> str:
    """
    Classify a WDA call failure into a frozen error code.

    Heuristic on the exception type/text:
      - session expiry / XCUITest errors → wda_session_lost
      - connection refused / reset / tunnel → wda_connection_refused
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    session_markers = (
        "session",
        "sessionnotcreated",
        "invalid session",
        "xcuitest",
        "unknown command",
    )
    if any(m in text for m in session_markers):
        return "wda_session_lost"
    return "wda_connection_refused"


def with_reconnect(serial: str, fn):
    """
    Execute ``fn(client)``; on failure, probe the tunnel and retry once.

    Returns the result of ``fn`` on success, or a structured error dict:
      {"ok": False, "error": "ios_tunnel_unavailable" | "wda_connection_refused", ...}
    Never raises.
    """
    # First attempt
    try:
        client = get_wda(serial)
        return fn(client)
    except Exception as exc:
        first_code = _classify_error(exc)
        logger.debug(
            "WDA call failed for [%s] (%s); probing tunnel before reconnect",
            serial,
            first_code,
        )

    # Probe-before-reconnect (ADR-3 takes precedence over session retry)
    tunnel = probe_tunnel(serial)
    if not tunnel.get("ok"):
        # Tunnel itself is down — do not blindly retry HTTP.
        return {
            "ok": False,
            "error": "ios_tunnel_unavailable",
            "hint": tunnel.get("hint", ""),
            "device_serial": serial,
        }

    # Tunnel is up → rebuild session and retry exactly once.
    try:
        reset_wda(serial)
        client = get_wda(serial)
        return fn(client)
    except Exception as exc2:
        code = _classify_error(exc2)
        _failed_serials.add(serial)
        return {
            "ok": False,
            "error": code,
            "detail": str(exc2),
            "device_serial": serial,
        }


__all__ = [
    "get_wda",
    "reset_wda",
    "is_wda_available",
    "with_reconnect",
    "release_all",
    "probe_tunnel",
    "_ensure_wda_class",
    "_connect_client",
    "_classify_error",
]
