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

import json
import logging
import os
import re
import time
from typing import Optional

from phone_pilot.ios.pmd import runner as pmd_runner
from phone_pilot.ios.pmd import utils as pmd_utils
from phone_pilot.ios.pmd.utils import probe_tunnel

logger = logging.getLogger(__name__)

# Cooldown window (seconds) after which a failed serial is automatically
# eligible for reconnection without manual reset_wda().
_FAILURE_COOLDOWN_S: float = 2.5


class _CooldownSet(set):
    """
    Set of failed device serials whose entries expire after ``_FAILURE_COOLDOWN_S``
    seconds so WDA can self-heal after transient disconnects.
    """

    def __init__(self) -> None:
        super().__init__()
        self._timestamps: dict[str, float] = {}

    def add(self, element: str) -> None:
        super().add(element)
        self._timestamps[element] = time.monotonic()

    def discard(self, element: str) -> None:
        super().discard(element)
        self._timestamps.pop(element, None)

    def remove(self, element: str) -> None:
        super().remove(element)
        self._timestamps.pop(element, None)

    def clear(self) -> None:
        super().clear()
        self._timestamps.clear()

    def is_expired(self, element: str, cooldown_s: Optional[float] = None) -> bool:
        if element not in self:
            return True
        ts = self._timestamps.get(element)
        if ts is None:
            return False
        limit = _FAILURE_COOLDOWN_S if cooldown_s is None else cooldown_s
        return (time.monotonic() - ts) >= limit


# Lazy-imported to avoid hard crash if facebook-wda is not installed
_WdaClient = None
_cache: dict[str, object] = {}
_failed_serials: _CooldownSet = _CooldownSet()
_serial_urls: dict[str, str] = {}

# Negative cache for simulator WDA runner installation check (30s TTL)
_WDA_RUNNER_ABSENT_TTL_S: float = 30.0
_wda_runner_absent_until: dict[str, float] = {}


def clear_wda_runner_cache(serial: Optional[str] = None) -> None:
    """Clear negative cache for simulator WebDriverAgentRunner presence."""
    if serial:
        _wda_runner_absent_until.pop(serial, None)
    else:
        _wda_runner_absent_until.clear()


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


def _normalize_wda_endpoint(raw: str) -> str:
    """Normalize a port number or URL string into a full HTTP base URL."""
    val = raw.strip()
    if re.fullmatch(r"\d+", val):
        return f"http://127.0.0.1:{val}"
    if not val.startswith(("http://", "https://")):
        return f"http://{val}"
    return val


def set_wda_url(serial: str, url: str) -> None:
    """Register or clear a dedicated WDA URL/port for a specific device serial."""
    if not serial:
        return
    if not url or not url.strip():
        _serial_urls.pop(serial, None)
    else:
        _serial_urls[serial] = _normalize_wda_endpoint(url)


def _lookup_env_serial_url(serial: str) -> Optional[str]:
    """Resolve per-serial WDA URL from environment variables if configured."""
    norm_key = f"PHONE_PILOT_WDA_URL_{serial.upper().replace('-', '_')}"
    exact_key = f"PHONE_PILOT_WDA_URL_{serial}"
    for key in (exact_key, norm_key):
        val = (os.environ.get(key) or "").strip()
        if val:
            return _normalize_wda_endpoint(val)

    for map_var in ("PHONE_PILOT_WDA_URLS", "PHONE_PILOT_WDA_PORTS"):
        raw_map = (os.environ.get(map_var) or "").strip()
        if not raw_map:
            continue
        if raw_map.startswith("{"):
            try:
                parsed = json.loads(raw_map)
                if isinstance(parsed, dict) and serial in parsed:
                    return _normalize_wda_endpoint(str(parsed[serial]))
            except Exception:
                pass
        for item in raw_map.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                if k.strip() == serial and v.strip():
                    return _normalize_wda_endpoint(v.strip())
    return None


def wda_base_url(serial: Optional[str] = None) -> str:
    """
    WDA base URL the client connects to.

    Resolution order when ``serial`` is provided:
      1. Programmatic mapping via ``set_wda_url(serial, url)`` (``_serial_urls``)
      2. ``PHONE_PILOT_WDA_URL_<SERIAL>`` or ``PHONE_PILOT_WDA_URLS`` / ``PHONE_PILOT_WDA_PORTS``
      3. ``PHONE_PILOT_WDA_URL`` environment variable
      4. Default ``http://127.0.0.1:8100`` (ADR-9 — bound to loopback only)
    """
    if serial:
        if serial in _serial_urls:
            return _serial_urls[serial]
        env_url = _lookup_env_serial_url(serial)
        if env_url:
            return env_url

    return _normalize_wda_endpoint(
        os.environ.get("PHONE_PILOT_WDA_URL") or "http://127.0.0.1:8100"
    )


def _verify_client_serial(client: object, serial: str) -> None:
    """
    Verify that the connected WDA instance belongs to ``serial`` when the client
    exposes device UDID in ``status()``. No-op if status is unavailable or omits UDID.
    """
    if not serial:
        return
    status_fn = getattr(client, "status", None)
    if not callable(status_fn):
        return
    try:
        info = status_fn()
    except Exception:
        return
    if not isinstance(info, dict):
        return
    ios_info = info.get("ios")
    actual_udid = info.get("udid") or (
        ios_info.get("udid") if isinstance(ios_info, dict) else None
    )
    if (
        isinstance(actual_udid, str)
        and actual_udid.strip()
        and actual_udid.strip().lower() != serial.strip().lower()
    ):
        raise RuntimeError(
            f"WDA target mismatch: expected [{serial}], got [{actual_udid.strip()}]"
        )


def _try_launch_simulator_wda(serial: str) -> bool:
    """
    Best-effort auto-launch of an installed WebDriverAgentRunner on a booted simulator.

    Uses a 30s negative cache when ``WebDriverAgentRunner`` is not installed on the
    simulator to avoid redundant ``simctl listapps`` calls; cleared by ``install_app``.
    Returns True if a launch command succeeded, False otherwise.
    """
    if not serial:
        return False
    now = time.monotonic()
    absent_until = _wda_runner_absent_until.get(serial, 0.0)
    if absent_until > now:
        return False
    try:
        if pmd_utils.detect_form_factor(serial) != "simulator":
            return False
        prefix = pmd_utils.simctl_prefix()
        proc = pmd_runner.run(prefix + ["listapps", serial], timeout_s=10.0)
        out = proc.stdout or ""
        candidates: list[str] = []
        for match in re.finditer(
            r'"?([A-Za-z0-9_.\-]*WebDriverAgentRunner[A-Za-z0-9_.\-]*)"?\s*=',
            out,
        ):
            bundle_id = match.group(1)
            if bundle_id not in candidates:
                candidates.append(bundle_id)
        if not candidates and "WebDriverAgentRunner" in out:
            candidates.append("com.facebook.WebDriverAgentRunner.xctrunner")
        if not candidates:
            if proc.returncode == 0:
                _wda_runner_absent_until[serial] = (
                    time.monotonic() + _WDA_RUNNER_ABSENT_TTL_S
                )
            return False
        _wda_runner_absent_until.pop(serial, None)
        for bundle_id in candidates:
            launch_proc = pmd_runner.run(
                prefix + ["launch", serial, bundle_id], timeout_s=10.0
            )
            if launch_proc.returncode == 0:
                logger.info(
                    "Auto-launched simulator WDA runner [%s] on [%s]",
                    bundle_id,
                    serial,
                )
                return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Auto-launch simulator WDA failed for [%s]: %s", serial, exc)
    return False


def _connect_client(serial: str):
    """
    Build a live WDA client for a serial. Override-seam for tests.

    Connects to the loopback-forwarded WDA HTTP server (ADR-9).
    """
    _ensure_wda_class()
    client = _WdaClient(wda_base_url(serial))  # type: ignore[misc]
    _verify_client_serial(client, serial)
    return client


# ---------------------------------------------------------------------------
# Cache lifecycle
# ---------------------------------------------------------------------------


def get_wda(serial: str):
    """
    Get or create a cached WDA client for a device.

    Raises:
        ImportError: facebook-wda not installed.
        RuntimeError: serial failed within cooldown window (call reset_wda to
            retry immediately), or connection attempt failed.
    """
    if serial in _cache:
        return _cache[serial]

    if serial in _failed_serials:
        if isinstance(_failed_serials, _CooldownSet) and _failed_serials.is_expired(
            serial
        ):
            _failed_serials.discard(serial)
        else:
            raise RuntimeError(
                f"WDA previously failed to connect to device [{serial}]. "
                "Call reset_wda(serial) to retry."
            )

    try:
        client = _connect_client(serial)
        _cache[serial] = client
        _failed_serials.discard(serial)
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
    _failed_serials.clear()
    _wda_runner_absent_until.clear()


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

    When ``serial`` is in ``_failed_serials`` and the cooldown window has not
    expired, immediately short-circuits with ``cooldown=True`` to prevent
    cascading reconnect storms. Once cooldown expires (or ``reset_wda`` is
    called), reconnect proceeds normally.

    Returns the result of ``fn`` on success, or a structured error dict:
      {"ok": False, "error": "ios_tunnel_unavailable" | "wda_connection_refused", ...}
    Never raises.
    """
    if serial in _failed_serials:
        if isinstance(_failed_serials, _CooldownSet) and _failed_serials.is_expired(
            serial
        ):
            _failed_serials.discard(serial)
        else:
            return {
                "ok": False,
                "error": "wda_connection_refused",
                "cooldown": True,
                "device_serial": serial,
            }

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

    if tunnel.get("form_factor") == "simulator":
        _try_launch_simulator_wda(serial)

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
    "wda_base_url",
    "set_wda_url",
    "clear_wda_runner_cache",
    "_ensure_wda_class",
    "_connect_client",
    "_classify_error",
    "_try_launch_simulator_wda",
]
