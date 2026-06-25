"""Shared helpers used by multiple MCP tool modules."""

from __future__ import annotations

from typing import Optional

from phone_pilot.core.protocols import DeviceDriver
from phone_pilot.core.skills import DeviceSkills
from phone_pilot.android.driver import AndroidDriver
from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.script_api.context import ScriptContext


# ---------------------------------------------------------------------------
# Driver Factory
# ---------------------------------------------------------------------------


def get_driver(device_serial: str, platform: str = "android") -> DeviceDriver:
    """Get a device driver instance based on platform."""
    platform = (platform or "android").strip().lower()
    if platform == "android":
        return AndroidDriver(device_serial)
    if platform == "ios":
        from phone_pilot.ios.driver import iOSDriver

        return iOSDriver(device_serial)
    if platform == "harmony":
        return HarmonyDriver(device_serial)
    raise ValueError(f"Unknown platform: {platform}")


def get_skills(device_serial: str, platform: str = "android") -> DeviceSkills:
    """Get a DeviceSkills instance for the given device."""
    return DeviceSkills(get_driver(device_serial, platform))


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
) -> dict:
    """Common device resolution + error wrapping helper."""
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err
    try:
        result = fn(resolved, plat)
        result.setdefault("device_serial", resolved)
        result.setdefault("platform", plat)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


def _scan_android_serials() -> list[str]:
    """Return online ADB device serials (empty on any failure)."""
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
    return serials


def _scan_harmony_serials() -> list[str]:
    """Return online HDC device serials (empty on any failure)."""
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
    return serials


def _scan_ios_serials() -> list[str]:
    """Return connected iOS UDIDs (real + booted simulators); empty on failure."""
    try:
        from phone_pilot.ios.pmd.utils import list_ios_serials

        return list_ios_serials()
    except Exception:
        return []


def _resolve_device_serial(
    device_serial: Optional[str], platform: str = "auto"
) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """Resolve device_serial and platform from adb/hdc/ios.

    Returns: (resolved_serial, resolved_platform, error_dict_or_none)
    """
    from phone_pilot.android.adb.utils import ensure_adb_env

    ensure_adb_env()
    platform = (platform or "auto").strip().lower()

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
