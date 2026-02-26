#!/usr/bin/env python3
"""
Tap helpers for Android automation.
点击器：统一处理坐标/匹配框点击。
"""

from __future__ import annotations

from typing import Optional

from phone_pilot.android.device.utils import input_tap


class Clicker:
    """
    点击器：封装 tap 行为，支持 match/box 的中心点点击。
    Clicker: wrap tap actions and tap match/box centers.

    用法/Usage:
    - clicker = Clicker(device_serial="SERIAL")
    - clicker.tap(100, 200)
    - clicker.tap_match({"x":10,"y":20,"w":30,"h":40})
    """

    def __init__(self, device_serial: Optional[str], *, default_wait_s: float = 0.15) -> None:
        """Create a clicker with default wait between taps."""
        self.device_serial = device_serial
        self.default_wait_s = float(default_wait_s)

    def tap(self, x: int, y: int, *, wait_s: Optional[float] = None) -> dict:
        """Tap at given coordinates."""
        return input_tap(self.device_serial, int(x), int(y), wait_s=float(wait_s or self.default_wait_s))

    def tap_match(
        self,
        match: dict,
        *,
        offset_x: int = 0,
        offset_y: int = 0,
        wait_s: Optional[float] = None,
    ) -> dict:
        """Tap the center of a match box, with optional offsets."""
        cx = match.get("center_x")
        cy = match.get("center_y")
        if cx is None or cy is None:
            x = int(match.get("x", 0))
            y = int(match.get("y", 0))
            w = int(match.get("w", 0))
            h = int(match.get("h", 0))
            cx = x + w / 2.0
            cy = y + h / 2.0
        return self.tap(int(cx) + int(offset_x), int(cy) + int(offset_y), wait_s=wait_s)
