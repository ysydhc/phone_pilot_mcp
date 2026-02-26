"""
iOS Driver implementation (planned).

Will implement the DeviceDriver protocol for iOS devices.
"""

from __future__ import annotations


class iOSDriver:
    """
    iOS DeviceDriver implementation (planned).
    
    Will use libimobiledevice and WebDriverAgent for automation.
    """

    def __init__(self, device_id: str):
        raise NotImplementedError(
            "iOS support is planned for a future release. "
            "Contributions welcome!"
        )

    @property
    def platform(self) -> str:
        return "ios"
