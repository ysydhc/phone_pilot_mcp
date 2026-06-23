#!/usr/bin/env python3
"""
Android logcat helpers.

In MCP stdio servers, stdout is reserved for protocol messages, so we always
capture command output and write logs to files instead of printing.
"""

from __future__ import annotations

import pathlib
from typing import Optional, Tuple

from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner


def clear_logcat(device_serial: Optional[str] = None) -> dict:
    """
    Clear the in-memory logcat buffer: `adb logcat -c`.
    """
    cmd = adb_prefix(device_serial) + ["logcat", "-c"]
    proc = CommandRunner.run(cmd, check=False, log_output=False)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stderr": (proc.stderr or "").strip(),
    }


def device_logcat_time(device_serial: Optional[str] = None) -> Optional[str]:
    """Return the device's current time as a logcat threadtime timestamp.

    Format matches ``logcat -v threadtime`` ("MM-DD HH:MM:SS.mmm"), so the
    result can be passed straight to ``read_logcat(since=...)`` / ``logcat -T``.
    Uses the device clock (same timezone as logcat), so no timezone conversion
    is needed. Returns None on failure.
    """
    # The device-side shell re-splits the command on spaces, so the format
    # string must be quoted to survive as a single argument to `date`.
    cmd = adb_prefix(device_serial) + ["shell", "date", "'+%m-%d %H:%M:%S.000'"]
    proc = CommandRunner.run(cmd, check=False, log_output=False)
    out = (proc.stdout or "").strip()
    return out or None


def _pidof(device_serial: Optional[str], package_name: Optional[str]) -> Optional[int]:
    """Return the first pid for a package, or None if not found."""
    if not package_name:
        return None
    cmd = adb_prefix(device_serial) + ["shell", "pidof", package_name]
    proc = CommandRunner.run(cmd, check=False, log_output=False)
    out = (proc.stdout or "").strip()
    if not out:
        return None
    # pidof may return multiple pids separated by spaces
    try:
        return int(out.split()[0])
    except Exception:
        return None


def _build_filter_spec(
    tag_filter: Optional[str], filter_spec: Optional[str]
) -> Optional[str]:
    """Build a logcat filter spec from tag or raw spec input."""
    if filter_spec and filter_spec.strip():
        return filter_spec.strip()
    if not tag_filter or not tag_filter.strip():
        return None
    raw = tag_filter.strip()
    if ":" in raw or " " in raw:
        return raw
    tags = [t.strip() for t in raw.replace(",", " ").split() if t.strip()]
    if not tags:
        return None
    return " ".join([f"{t}:D" for t in tags] + ["*:S"])


def read_logcat(
    device_serial: Optional[str],
    *,
    lines: int = 5000,
    fmt: str = "threadtime",
    filter_spec: Optional[str] = None,
    tag_filter: Optional[str] = None,
    package_name: Optional[str] = None,
    since: Optional[str] = None,
) -> Tuple[str, dict]:
    """
    Read logcat content from the device.

    Args:
        device_serial: Device serial number
        lines: Number of lines to retrieve (ignored when ``since`` is given)
        fmt: Logcat format (threadtime, brief, etc.)
        filter_spec: Full filter specification
        tag_filter: Tag-based filter
        package_name: Filter by package PID
        since: Only return logs at/after this device timestamp (maps to
            ``logcat -T``, e.g. "01-15 14:30:00.000"). Takes precedence over
            ``lines``.

    Returns:
        Tuple of (content, metadata dict)
    """
    try:
        n = int(lines)
    except Exception:
        n = 5000
    n = max(1, min(n, 20000))
    fmt2 = (fmt or "threadtime").strip() or "threadtime"
    fs = _build_filter_spec(tag_filter, filter_spec)

    # -T <time> (since) and -t <count> are mutually exclusive selectors; when a
    # start time is given it takes precedence so we get the full window after it.
    def _window_args() -> list[str]:
        if since:
            return ["-T", since]
        return ["-t", str(n)]

    pid = _pidof(device_serial, package_name)
    cmd = adb_prefix(device_serial) + ["logcat", "-d", "-v", fmt2] + _window_args()
    used_pid = False
    if pid is not None:
        cmd += ["--pid", str(pid)]
        used_pid = True
    if fs:
        cmd.append(fs)

    proc = CommandRunner.run(cmd, check=False, log_output=False)
    # If --pid is unsupported, retry without pid.
    if proc.returncode != 0 and used_pid:
        cmd = adb_prefix(device_serial) + ["logcat", "-d", "-v", fmt2] + _window_args()
        if fs:
            cmd.append(fs)
        proc = CommandRunner.run(cmd, check=False, log_output=False)
        used_pid = False

    content = proc.stdout or ""
    if proc.stderr:
        content += "\n\n--- stderr ---\n" + proc.stderr
    meta = {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "lines": n,
        "since": since,
        "format": fmt2,
        "filter_spec": fs,
        "package_name": package_name,
        "pid_used": pid if used_pid else None,
    }
    return content, meta


def dump_logcat(
    device_serial: Optional[str],
    out_path: pathlib.Path,
    *,
    lines: int = 5000,
    fmt: str = "threadtime",
    filter_spec: Optional[str] = None,
    tag_filter: Optional[str] = None,
    package_name: Optional[str] = None,
) -> dict:
    """
    Dump (non-streaming) logcat into a local file.

    Uses:
      adb logcat -d -v <fmt> -t <lines> [<filter_spec>]
    """
    content, meta = read_logcat(
        device_serial,
        lines=lines,
        fmt=fmt,
        filter_spec=filter_spec,
        tag_filter=tag_filter,
        package_name=package_name,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8", errors="replace")
    return {
        **meta,
        "path": str(out_path),
    }


__all__ = [
    "clear_logcat",
    "read_logcat",
    "dump_logcat",
]
