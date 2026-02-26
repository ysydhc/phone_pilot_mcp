"""
HarmonyOS platform implementation.

Primary engine: hmdriver2 (uitest daemon + socket).
Fallback: raw HDC commands for basic operations.
"""

from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.harmony.hmdriver_bridge import (
    get_hmdriver,
    has_hmdriver,
    reset_hmdriver,
    is_hmdriver_available,
)

__all__ = [
    "HarmonyDriver",
    "get_hmdriver",
    "has_hmdriver",
    "reset_hmdriver",
    "is_hmdriver_available",
]
