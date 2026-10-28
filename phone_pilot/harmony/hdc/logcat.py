"""
HarmonyOS log operations via hilog (analogous to Android logcat).

HarmonyOS uses ``hilog`` instead of ``logcat``.
"""

from __future__ import annotations

import logging
import pathlib
import re
from typing import Optional

from phone_pilot.harmony.hdc.runner import HdcCommandRunner
from phone_pilot.harmony.hdc.utils import hdc_prefix

logger = logging.getLogger(__name__)

_TS_RE = re.compile(r"^((?:\d{4}-)?\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)")


def _norm_ts(ts: str) -> str:
    """Normalize timestamp to MM-DD HH:MM:SS.mmm for uniform comparison."""
    ts = ts.strip()
    if re.match(r"^\d{4}-", ts):
        ts = ts[5:]
    m = re.match(r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})(?:\.(\d+))?", ts)
    if m:
        base = m.group(1)
        frac = (m.group(2) or "0")[:3].ljust(3, "0")
        return f"{base}.{frac}"
    return ts


def _compare_ts(line_ts: str, since_ts: str) -> bool:
    """Return True if line_ts >= since_ts."""
    has_year1 = bool(re.match(r"^\d{4}-", line_ts))
    has_year2 = bool(re.match(r"^\d{4}-", since_ts))
    if has_year1 and has_year2:
        return line_ts[:23] >= since_ts[:23]
    return _norm_ts(line_ts) >= _norm_ts(since_ts)


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


def device_hilog_time(device_serial: Optional[str] = None) -> Optional[str]:
    """Return the device's current time as a hilog timestamp ("MM-DD HH:MM:SS.000").

    Format matches hilog threadtime ("MM-DD HH:MM:SS.mmm").
    Runs `hdc shell date '+%m-%d %H:%M:%S.000'` via HdcCommandRunner.
    Falls back to host `datetime.now().strftime("%m-%d %H:%M:%S.000")` if device call fails.
    """
    try:
        cmd = hdc_prefix(device_serial) + ["shell", "date", "+%m-%d %H:%M:%S.000"]
        proc = HdcCommandRunner.run(
            cmd,
            check=False,
            timeout_s=5.0,
            log_output=False,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and out:
            out_clean = out.splitlines()[-1].strip("'\" \r\n")
            if out_clean:
                return out_clean
    except Exception as exc:
        logger.debug("device_hilog_time failed: %s", exc)

    from datetime import datetime

    return datetime.now().strftime("%m-%d %H:%M:%S.000")


def read_hilog(
    device_serial: str,
    *,
    lines: int = 200,
    since: Optional[str] = None,
    tag_filter: Optional[str] = None,
    level: Optional[str] = None,
) -> str:
    """
    Read hilog content from device.

    Args:
        device_serial: Device serial.
        lines: Number of recent lines to read (at least 2000 when ``since`` is given).
        since: Filter logs at or after this timestamp ("MM-DD HH:MM:SS.mmm" or
            "YYYY-MM-DD HH:MM:SS.mmm").
        tag_filter: Filter by tag (e.g., "MyApp").
        level: Minimum log level: D(ebug), I(nfo), W(arn), E(rror), F(atal).

    Returns:
        Log content as a string.
    """
    query_lines = max(lines, 2000) if since else lines
    cmd = hdc_prefix(device_serial) + ["shell", "hilog", "-x"]

    if query_lines:
        cmd += ["-t", str(query_lines)]

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
        raw = proc.stdout or ""
        if not since or not raw:
            return raw

        filtered_lines: list[str] = []
        keep_current = False
        for line in raw.splitlines():
            m = _TS_RE.match(line)
            if m:
                keep_current = _compare_ts(m.group(1), since)
            if keep_current:
                filtered_lines.append(line)
        return "\n".join(filtered_lines) + ("\n" if filtered_lines else "")
    except Exception as exc:
        logger.warning("read_hilog failed: %s", exc)
        return ""


def dump_hilog(
    device_serial: str,
    out_path: str,
    *,
    lines: int = 500,
    since: Optional[str] = None,
    tag_filter: Optional[str] = None,
    level: Optional[str] = None,
) -> dict:
    """
    Dump hilog content to a local file.

    Args:
        device_serial: Device serial.
        out_path: Local file path to write to.
        lines: Number of lines.
        since: Filter logs at or after this timestamp.
        tag_filter: Filter by tag.
        level: Min log level.

    Returns:
        {"ok": True, "path": str, "lines": int}
    """
    content = read_hilog(
        device_serial,
        lines=lines,
        since=since,
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
    "device_hilog_time",
]
