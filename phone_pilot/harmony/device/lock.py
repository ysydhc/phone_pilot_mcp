"""
HarmonyOS device lock/unlock operations (powered by hmdriver2).
"""

from __future__ import annotations

import logging
from typing import Optional

from phone_pilot.harmony.hmdriver_bridge import get_hmdriver

logger = logging.getLogger(__name__)


def wake_and_unlock(device_serial: str, *, pin: Optional[str] = None) -> dict:
    """
    Wake and unlock a HarmonyOS device.

    Uses hmdriver2's screen_on() + unlock() which does a swipe gesture.
    PIN input is not yet supported via hmdriver2; a manual swipe + tap
    sequence would be needed for PIN-locked devices.

    Args:
        device_serial: Device serial.
        pin: Unlock PIN (not yet implemented for Harmony).

    Returns:
        {"ok": True} on success.
    """
    try:
        hm = get_hmdriver(device_serial)
        hm.screen_on()
        hm.unlock()
        if pin:
            logger.warning(
                "PIN unlock not yet implemented for HarmonyOS; "
                "device was unlocked with swipe only."
            )
        return {"ok": True, "engine": "hmdriver2"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def lock_screen(device_serial: str) -> dict:
    """Lock/sleep the device."""
    try:
        hm = get_hmdriver(device_serial)
        hm.screen_off()
        return {"ok": True, "engine": "hmdriver2"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def screen_on(device_serial: str) -> dict:
    """Wake up the screen."""
    try:
        hm = get_hmdriver(device_serial)
        hm.screen_on()
        return {"ok": True, "engine": "hmdriver2"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_screen_state(device_serial: str) -> dict:
    """
    Get screen state (AWAKE/INACTIVE/SLEEP).

    Uses hmdriver2's hdc wrapper for power state.
    """
    try:
        hm = get_hmdriver(device_serial)
        state = hm.hdc.screen_state()
        return {
            "ok": True,
            "state": state,
            "screen_on": state == "AWAKE" if state else None,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


__all__ = [
    "wake_and_unlock",
    "lock_screen",
    "screen_on",
    "get_screen_state",
]
