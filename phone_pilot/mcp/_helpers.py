"""Shared helpers used by multiple MCP tool modules."""

from __future__ import annotations

import os
import time
from typing import Optional

from phone_pilot.core.protocols import DeviceDriver
from phone_pilot.core.skills import DeviceSkills
from phone_pilot.android.driver import AndroidDriver
from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.script_api.context import ScriptContext

_SCAN_CACHE_TTL_S: float = 10.0
_scan_cache: dict[str, tuple[float, list[str]]] = {}


def invalidate_device_cache() -> None:
    """Clear short-TTL device topology caches in MCP helpers and iOS pmd utils."""
    _scan_cache.clear()
    try:
        from phone_pilot.ios.pmd.utils import (
            invalidate_device_cache as _inv_ios_cache,
        )

        _inv_ios_cache()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Driver Factory
# ---------------------------------------------------------------------------


def get_driver(device_serial: str, platform: str = "auto") -> DeviceDriver:
    """Get a device driver instance based on platform (supports 'auto')."""
    plat = (platform or "auto").strip().lower()
    if plat == "auto":
        resolved_serial, resolved_plat, _ = _resolve_device_serial(
            device_serial if device_serial and str(device_serial).strip() else None,
            "auto",
        )
        if resolved_plat:
            plat = resolved_plat
            if resolved_serial and not (device_serial and str(device_serial).strip()):
                device_serial = resolved_serial
        else:
            from phone_pilot.core.driver_factory import create_driver

            return create_driver(device_serial, "auto")
    if plat == "android":
        return AndroidDriver(device_serial)
    if plat == "ios":
        from phone_pilot.ios.driver import iOSDriver

        return iOSDriver(device_serial)
    if plat == "harmony":
        return HarmonyDriver(device_serial)
    raise ValueError(f"Unknown platform: {platform}")


def get_skills(device_serial: str, platform: str = "auto") -> DeviceSkills:
    """Get a DeviceSkills instance for the given device."""
    return DeviceSkills(get_driver(device_serial, platform))


def _resolve_driver_and_platform(
    device_serial: str,
    platform: str = "auto",
    get_driver_fn=None,
) -> tuple[DeviceDriver, str, str]:
    """Resolve concrete (driver, resolved_serial, resolved_platform).

    When platform is 'auto' (or empty), queries _resolve_device_serial first so
    that even when get_driver_fn is mocked in unit tests, the concrete platform
    ('android' | 'ios' | 'harmony') is accurately determined.
    """
    plat = (platform or "auto").strip().lower()
    serial = (device_serial or "").strip()
    if plat == "auto" or not serial:
        res_serial, res_plat, _ = _resolve_device_serial(serial or None, plat)
        if res_serial:
            serial = res_serial
        if res_plat:
            plat = res_plat

    factory = get_driver_fn or get_driver
    target_serial = serial or device_serial
    driver = factory(target_serial, plat)
    drv_plat = getattr(driver, "platform", None)
    if isinstance(drv_plat, str) and drv_plat in ("android", "ios", "harmony"):
        plat = drv_plat
    elif plat == "auto":
        plat = "android"
    return driver, target_serial, plat


def _make_script_context(device_serial: str, platform: str) -> ScriptContext:
    """Build minimal ScriptContext for MCP single-call usage."""
    return ScriptContext(
        device_serial=device_serial,
        platform=platform,
        auto_log=False,
        auto_screenshot=False,
        auto_dump_hprof=False,
        auto_meminfo=False,
        auto_report=False,
        popup_guard=False,
    )


# ---------------------------------------------------------------------------
# Common device resolution + error wrapper
# ---------------------------------------------------------------------------


def _with_device(
    fn,
    device_serial: str | None,
    platform: str,
    check_lock: bool = True,
) -> dict:
    """Common device resolution + error wrapping helper."""
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    # Concurrency guard: check if device is leased/locked by another session
    if check_lock and resolved:
        try:
            from phone_pilot.core.device_lease import get_device_lock_status

            st = get_device_lock_status(resolved)
            if st.get("locked"):
                lock_info = st.get("lock_info", {})
                lock_session = lock_info.get("session_id")
                caller_session = os.getenv("PHONE_PILOT_SESSION_ID")
                # Strict enforcement or session mismatch
                strict = os.getenv("PHONE_PILOT_STRICT_LOCK", "1").lower() in (
                    "1",
                    "true",
                    "yes",
                )
                if strict and (not caller_session or caller_session != lock_session):
                    return {
                        "ok": False,
                        "error": "device_busy_locked",
                        "device_serial": resolved,
                        "platform": plat,
                        "locked_by": lock_info.get("holder_name"),
                        "purpose": lock_info.get("purpose"),
                        "remaining_s": lock_info.get("remaining_s"),
                        "session_id": lock_session,
                        "note": (
                            f"设备 [{resolved}] 当前被 [{lock_info.get('holder_name')}] 独占锁定中 "
                            f"(剩余 {lock_info.get('remaining_s')}s，用途: {lock_info.get('purpose')})。"
                            f"请稍候重试或通过 phone_device_acquire_lock 排队申请设备锁。"
                        ),
                    }
        except Exception:
            pass

    try:
        result = fn(resolved, plat)
        result.setdefault("device_serial", resolved)
        result.setdefault("platform", plat)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


def _scan_android_serials() -> list[str]:
    """Return online ADB device serials (10s TTL cached; empty on any failure)."""
    now = time.monotonic()
    cached = _scan_cache.get("android")
    if cached is not None and (now - cached[0]) < _SCAN_CACHE_TTL_S:
        return list(cached[1])
    serials: list[str] = []
    try:
        from phone_pilot.android.adb.utils import adb_executable
        from phone_pilot.android.adb.runner import CommandRunner
        from phone_pilot.android.adb.parsers import parse_adb_devices

        proc = CommandRunner.run([adb_executable(), "devices", "-l"], check=False)
        for d in parse_adb_devices(proc.stdout or ""):
            if not d:
                continue
            state = d.get("status") or d.get("state")
            if state == "device" and d.get("serial"):
                serials.append(d.get("serial"))
    except Exception:
        pass
    _scan_cache["android"] = (now, list(serials))
    return serials


def _scan_harmony_serials() -> list[str]:
    """Return online HDC device serials (10s TTL cached; empty on any failure)."""
    now = time.monotonic()
    cached = _scan_cache.get("harmony")
    if cached is not None and (now - cached[0]) < _SCAN_CACHE_TTL_S:
        return list(cached[1])
    serials: list[str] = []
    try:
        from phone_pilot.harmony.hdc.utils import hdc_executable
        from phone_pilot.harmony.hdc.runner import HdcCommandRunner

        proc_h = HdcCommandRunner.run(
            [hdc_executable(), "list", "targets"], check=False, log_output=False
        )
        for raw in (proc_h.stdout or "").splitlines():
            line = raw.strip()
            if not line or "empty" in line.lower():
                continue
            serial = line.split()[0]
            if serial:
                serials.append(serial)
    except Exception:
        pass
    _scan_cache["harmony"] = (now, list(serials))
    return serials


def _scan_ios_serials() -> list[str]:
    """Return connected iOS UDIDs (real + booted simulators, 10s TTL cached); empty on failure."""
    now = time.monotonic()
    cached = _scan_cache.get("ios")
    if cached is not None and (now - cached[0]) < _SCAN_CACHE_TTL_S:
        return list(cached[1])
    try:
        from phone_pilot.ios.pmd.utils import list_ios_serials

        serials = list_ios_serials()
    except Exception:
        serials = []
    _scan_cache["ios"] = (now, list(serials))
    return serials


def _resolve_device_serial(
    device_serial: Optional[str], platform: str = "auto"
) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """Resolve device_serial and platform from adb/hdc/ios.

    Returns: (resolved_serial, resolved_platform, error_dict_or_none)
    """
    # Prioritize environment variable binding if device_serial is not passed
    if not device_serial or not str(device_serial).strip():
        env_serial = os.getenv("PHONE_PILOT_DEVICE_SERIAL")
        if env_serial and env_serial.strip():
            device_serial = env_serial.strip()

    platform = (platform or "auto").strip().lower()
    if platform == "auto":
        env_platform = os.getenv("PHONE_PILOT_PLATFORM")
        if env_platform and env_platform.strip():
            platform = env_platform.strip().lower()

    if platform in ("android", "auto"):
        from phone_pilot.android.adb.utils import ensure_adb_env

        ensure_adb_env()

    android_serials = _scan_android_serials() if platform in ("android", "auto") else []
    harmony_serials = _scan_harmony_serials() if platform in ("harmony", "auto") else []
    ios_serials = _scan_ios_serials() if platform in ("ios", "auto") else []

    if device_serial:
        if device_serial in android_serials:
            return device_serial, "android", None
        if device_serial in harmony_serials:
            return device_serial, "harmony", None
        if device_serial in ios_serials:
            return device_serial, "ios", None
        return (
            None,
            None,
            {
                "ok": False,
                "error": "device_not_found",
                "device_serial": device_serial,
                "note": "设备未找到。请确认设备已连接且授权调试。",
                "available_android": android_serials,
                "available_harmony": harmony_serials,
                "available_ios": ios_serials,
            },
        )

    # Auto-select: explicit platform first, then android > harmony > ios.
    if platform == "android" and android_serials:
        return android_serials[0], "android", None
    if platform == "harmony" and harmony_serials:
        return harmony_serials[0], "harmony", None
    if platform == "ios" and ios_serials:
        return ios_serials[0], "ios", None
    if android_serials:
        return android_serials[0], "android", None
    if harmony_serials:
        return harmony_serials[0], "harmony", None
    if ios_serials:
        return ios_serials[0], "ios", None

    return (
        None,
        None,
        {
            "ok": False,
            "error": "no_device_connected",
            "note": "未发现可用设备。请连接 Android / HarmonyOS / iOS 设备。",
        },
    )
