"""
iOS launch-from-home flow: go home → locate the app icon by name → tap it.

Uses the same WDA bridge as the driver. Kept dependency-light so the driver
can delegate to it; device interaction is fully mockable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def launch_from_home(serial: str, app_name: str, **kwargs) -> dict:
    """
    Go to the home screen, find an icon whose label matches ``app_name``, tap it.

    Returns {"ok": True, "app_name": ...} on success, else an error envelope.
    """
    # Imported lazily to avoid import cycle with driver.py.
    from phone_pilot.ios import wda_bridge
    from phone_pilot.ios.ui import automator

    def _do(client):
        client.home()
        # WDA source(format="json") returns a dict; parse_wda_source accepts
        # both dict and JSON string.
        source = client.source(format="json")
        elements = automator.find_in_source(source, {"text": app_name})
        if not elements:
            # Fall back to a contains match.
            elements = automator.find_in_source(source, {"text_contains": app_name})
        if not elements:
            return {
                "ok": False,
                "error": "app_icon_not_found",
                "app_name": app_name,
                "platform": "ios",
            }
        target = elements[0]
        client.tap(int(target["center_x"]), int(target["center_y"]))
        return {"ok": True, "app_name": app_name, "engine": "wda"}

    result = wda_bridge.with_reconnect(serial, _do)
    if isinstance(result, dict):
        return result
    return {
        "ok": False,
        "error": "launch_from_home_failed",
        "app_name": app_name,
        "platform": "ios",
    }


__all__ = ["launch_from_home"]
