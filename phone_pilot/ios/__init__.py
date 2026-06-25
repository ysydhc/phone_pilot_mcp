"""
iOS platform implementation.

Primary engine: WebDriverAgent (XCUITest) + pymobiledevice3.
Fallback: HDC-equivalent pymobiledevice3 commands for device operations.
"""

from phone_pilot.ios.driver import iOSDriver
from phone_pilot.ios.wda_bridge import (
    get_wda,
    reset_wda,
    is_wda_available,
)

__all__ = [
    "iOSDriver",
    "get_wda",
    "reset_wda",
    "is_wda_available",
]
