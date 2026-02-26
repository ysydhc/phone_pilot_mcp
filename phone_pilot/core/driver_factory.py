"""
Driver factory — creates the correct DeviceDriver for a given platform.

Supports auto-detection: scans ADB and HDC to find connected devices
and returns the appropriate driver instance.
"""

from __future__ import annotations

import logging
from typing import Optional

from phone_pilot.core.protocols import DeviceDriver

logger = logging.getLogger(__name__)


def create_driver(
    device_serial: str,
    platform: str = "auto",
) -> DeviceDriver:
    """
    Create a DeviceDriver for the given serial and platform.

    Args:
        device_serial: Device serial string (required).
        platform: "android" | "harmony" | "ios" | "auto".
            If "auto", the factory probes ADB then HDC to detect platform.

    Returns:
        A DeviceDriver instance (AndroidDriver or HarmonyDriver).

    Raises:
        ValueError: If the platform is unknown.
        RuntimeError: If auto-detection fails to find the device.
    """
    plat = (platform or "auto").strip().lower()

    if plat == "android":
        from phone_pilot.android.driver import AndroidDriver
        return AndroidDriver(device_serial)

    if plat == "harmony":
        from phone_pilot.harmony.driver import HarmonyDriver
        return HarmonyDriver(device_serial)

    if plat == "ios":
        raise NotImplementedError("iOS support is planned but not yet implemented")

    if plat == "auto":
        return _auto_detect_driver(device_serial)

    raise ValueError(f"Unknown platform: {platform!r}")


def detect_device() -> tuple[str, str]:
    """
    Detect the first available device across all platforms.

    Returns:
        (device_serial, platform) tuple.

    Raises:
        RuntimeError: If no device is found.
    """
    # --- try ADB first ---
    serial = _first_adb_device()
    if serial:
        return serial, "android"

    # --- try HDC ---
    serial = _first_hdc_device()
    if serial:
        return serial, "harmony"

    raise RuntimeError(
        "No device found. Connect a device via USB or ensure ADB/HDC is running."
    )


def create_driver_auto() -> DeviceDriver:
    """Detect the first device and create a driver for it."""
    serial, plat = detect_device()
    return create_driver(serial, plat)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auto_detect_driver(serial: str) -> DeviceDriver:
    """Given a serial, figure out if it's ADB or HDC and create the driver."""
    if _is_adb_device(serial):
        from phone_pilot.android.driver import AndroidDriver
        return AndroidDriver(serial)

    if _is_hdc_device(serial):
        from phone_pilot.harmony.driver import HarmonyDriver
        return HarmonyDriver(serial)

    # Last resort: try to connect as HarmonyOS (HDC serial format varies)
    # then fall back to Android
    try:
        from phone_pilot.harmony.driver import HarmonyDriver
        drv = HarmonyDriver(serial)
        # Quick sanity check — try to get screen size
        drv.screen.get_screen_size()
        return drv
    except Exception:
        pass

    try:
        from phone_pilot.android.driver import AndroidDriver
        return AndroidDriver(serial)
    except Exception:
        pass

    raise RuntimeError(
        f"Cannot determine platform for device {serial!r}. "
        "Pass platform='android' or platform='harmony' explicitly."
    )


def _first_adb_device() -> Optional[str]:
    """Return the first online ADB device serial, or None."""
    try:
        from phone_pilot.android.adb.utils import adb_executable
        from phone_pilot.android.adb.runner import CommandRunner
        from phone_pilot.android.adb.parsers import parse_adb_devices

        proc = CommandRunner.run(
            [adb_executable(), "devices"],
            check=False, delay_s=0, log_output=False,
        )
        devices = parse_adb_devices(proc.stdout or "")
        for serial, state in devices:
            if state == "device":
                return serial
    except Exception:
        pass
    return None


def _first_hdc_device() -> Optional[str]:
    """Return the first online HDC device serial, or None."""
    try:
        from phone_pilot.harmony.hdc.utils import hdc_executable
        from phone_pilot.harmony.hdc.runner import HdcCommandRunner

        proc = HdcCommandRunner.run(
            [hdc_executable(), "list", "targets"],
            check=False, delay_s=0, log_output=False, silent=True,
        )
        for line in (proc.stdout or "").strip().splitlines():
            s = line.strip()
            if s and s not in ("[Empty]", ""):
                return s
    except Exception:
        pass
    return None


def _is_adb_device(serial: str) -> bool:
    """Check if the serial appears in `adb devices` as 'device'."""
    try:
        from phone_pilot.android.adb.utils import adb_executable
        from phone_pilot.android.adb.runner import CommandRunner
        from phone_pilot.android.adb.parsers import parse_adb_devices

        proc = CommandRunner.run(
            [adb_executable(), "devices"],
            check=False, delay_s=0, log_output=False,
        )
        for s, state in parse_adb_devices(proc.stdout or ""):
            if s == serial and state == "device":
                return True
    except Exception:
        pass
    return False


def _is_hdc_device(serial: str) -> bool:
    """Check if the serial appears in `hdc list targets`."""
    try:
        from phone_pilot.harmony.hdc.utils import hdc_executable
        from phone_pilot.harmony.hdc.runner import HdcCommandRunner

        proc = HdcCommandRunner.run(
            [hdc_executable(), "list", "targets"],
            check=False, delay_s=0, log_output=False, silent=True,
        )
        for line in (proc.stdout or "").strip().splitlines():
            if line.strip() == serial:
                return True
    except Exception:
        pass
    return False
