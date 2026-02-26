"""
Android device management utilities.

Provides device state detection, lock/unlock, and profile storage.
"""

from phone_pilot.android.device.lock import (
    get_display_state,
    get_device_state,
    detect_lock_method,
    lock_screen,
    wake_and_unlock,
)

__all__ = [
    "get_display_state",
    "get_device_state",
    "detect_lock_method",
    "lock_screen",
    "wake_and_unlock",
]
