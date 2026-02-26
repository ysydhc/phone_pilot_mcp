"""
HarmonyOS log operations via hilog (analogous to Android logcat).

HarmonyOS uses ``hilog`` instead of ``logcat``.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Optional

from phone_pilot.harmony.hdc.runner import HdcCommandRunner
from phone_pilot.harmony.hdc.utils import hdc_prefix

logger = logging.getLogger(__name__)


def clear_hilog(device_serial: str) -> dict:
    """
    Clear hilog buffer on device.

    Equivalent to Android's ``adb logcat -c``.
    """
    try:
        proc = HdcCommandRunner.run(
            hdc_prefix(device_serial) + ["shell", "hilog", "-r"],
            check=False,
            timeout_s=5.0,
            log_output=False,
        )
        return {"ok": proc.returncode == 0}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def read_hilog(
    device_serial: str,
    *,
    lines: int = 200,
    tag_filter: Optional[str] = None,
    level: Optional[str] = None,
) -> str:
    """
    Read hilog content from device.

    Args:
        device_serial: Device serial.
        lines: Number of recent lines to read.
        tag_filter: Filter by tag (e.g., "MyApp").
        level: Minimum log level: D(ebug), I(nfo), W(arn), E(rror), F(atal).

    Returns:
        Log content as a string.
    """
    cmd = hdc_prefix(device_serial) + ["shell", "hilog", "-x"]

    if lines:
        cmd += ["-t", str(lines)]

    if tag_filter:
        cmd += ["-T", tag_filter]

    if level:
        # hilog -L maps: D/I/W/E/F
        cmd += ["-L", level.upper()[0] if level else "D"]

    try:
        proc = HdcCommandRunner.run(
            cmd,
            check=False,
            timeout_s=10.0,
            log_output=False,
        )
        return proc.stdout or ""
    except Exception as exc:
        logger.warning("read_hilog failed: %s", exc)
        return ""


def dump_hilog(
    device_serial: str,
    out_path: str,
    *,
    lines: int = 500,
    tag_filter: Optional[str] = None,
    level: Optional[str] = None,
) -> dict:
    """
    Dump hilog content to a local file.

    Args:
        device_serial: Device serial.
        out_path: Local file path to write to.
        lines: Number of lines.
        tag_filter: Filter by tag.
        level: Min log level.

    Returns:
        {"ok": True, "path": str, "lines": int}
    """
    content = read_hilog(
        device_serial,
        lines=lines,
        tag_filter=tag_filter,
        level=level,
    )
    p = pathlib.Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    actual_lines = content.count("\n")
    return {"ok": True, "path": str(p), "lines": actual_lines}


__all__ = [
    "clear_hilog",
    "read_hilog",
    "dump_hilog",
]
