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
        raise NotImplementedError("iOS support is planned for a future release")
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


def _resolve_device_serial(
    device_serial: Optional[str], platform: str = "auto"
) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """Resolve device_serial and platform from adb/hdc.

    Returns: (resolved_serial, resolved_platform, error_dict_or_none)
    """
    from phone_pilot.android.adb.utils import ensure_adb_env

    ensure_adb_env()
    platform = (platform or "auto").strip().lower()
    android_serials: list[str] = []
    harmony_serials: list[str] = []

    # Scan Android devices (adb)
    if platform in ("android", "auto"):
        try:
            from phone_pilot.android.adb.utils import adb_executable
            from phone_pilot.android.adb.runner import CommandRunner
            from phone_pilot.android.adb.parsers import parse_adb_devices

            proc = CommandRunner.run([adb_executable(), "devices", "-l"], check=False)
            devices = parse_adb_devices(proc.stdout or "")
            for d in devices:
                if not d:
                    continue
                state = d.get("status") or d.get("state")
                if state == "device":
                    serial = d.get("serial")
                    if serial:
                        android_serials.append(serial)
        except Exception:
            pass

    # Scan Harmony devices (hdc)
    if platform in ("harmony", "auto"):
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
                    harmony_serials.append(serial)
        except Exception:
            pass

    if device_serial:
        if device_serial in android_serials:
            return device_serial, "android", None
        if device_serial in harmony_serials:
            return device_serial, "harmony", None
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
            },
        )

    # Auto-select
    if platform == "android" and android_serials:
        return android_serials[0], "android", None
    if platform == "harmony" and harmony_serials:
        return harmony_serials[0], "harmony", None
    if android_serials:
        return android_serials[0], "android", None
    if harmony_serials:
        return harmony_serials[0], "harmony", None

    return (
        None,
        None,
        {
            "ok": False,
            "error": "no_device_connected",
            "note": "未发现可用设备。请连接 Android 或 HarmonyOS 设备。",
        },
    )
