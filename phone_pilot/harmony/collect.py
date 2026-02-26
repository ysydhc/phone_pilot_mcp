"""
HarmonyOS artifact collection (screenshot + focus + hilog).

Mirrors Android's collect.py functionality.
"""

from __future__ import annotations

import logging
import pathlib
import time

logger = logging.getLogger(__name__)


def collect_artifacts(
    device_serial: str,
    base_dir: str,
    name: str = "harmony",
    *,
    hilog_lines: int = 200,
) -> dict:
    """
    Collect screenshot, current focus info, and hilog into a timestamped folder.

    Args:
        device_serial: Device serial.
        base_dir: Base output directory.
        name: Subdirectory name prefix.
        hilog_lines: Number of hilog lines to capture.

    Returns:
        {"ok": True, "dir": str, "files": list}
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = pathlib.Path(base_dir) / f"{ts}_{name}"
    out_dir.mkdir(parents=True, exist_ok=True)

    files = []

    # 1. Screenshot
    try:
        from phone_pilot.harmony.driver import HarmonyScreenDriver
        screen = HarmonyScreenDriver(device_serial)
        png_data = screen.screenshot()
        screenshot_path = out_dir / "screenshot.png"
        screenshot_path.write_bytes(png_data)
        files.append(str(screenshot_path))
    except Exception as exc:
        logger.warning("Screenshot collection failed: %s", exc)

    # 2. Current focus
    try:
        from phone_pilot.harmony.driver import HarmonyUIDriver
        ui = HarmonyUIDriver(device_serial)
        focus = ui.get_current_activity()
        focus_path = out_dir / "focus.json"
        import json
        focus_path.write_text(json.dumps(focus, indent=2), encoding="utf-8")
        files.append(str(focus_path))
    except Exception as exc:
        logger.warning("Focus collection failed: %s", exc)

    # 3. HiLog
    try:
        from phone_pilot.harmony.hdc.logcat import dump_hilog
        hilog_path = out_dir / "hilog.txt"
        dump_hilog(device_serial, str(hilog_path), lines=hilog_lines)
        files.append(str(hilog_path))
    except Exception as exc:
        logger.warning("HiLog collection failed: %s", exc)

    return {"ok": True, "dir": str(out_dir), "files": files}


__all__ = ["collect_artifacts"]
