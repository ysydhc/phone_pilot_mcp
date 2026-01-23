#!/usr/bin/env python3
"""
Android logcat helpers.

In MCP stdio servers, stdout is reserved for protocol messages, so we always
capture command output and write logs to files instead of printing.
"""

from __future__ import annotations

import pathlib
from typing import Optional, Tuple

from android_tool.adb_utils import adb_prefix
from android_tool.runner import CommandRunner


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


def _pidof(device_serial: Optional[str], package_name: Optional[str]) -> Optional[int]:
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


def _build_filter_spec(tag_filter: Optional[str], filter_spec: Optional[str]) -> Optional[str]:
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
) -> Tuple[str, dict]:
    try:
        n = int(lines)
    except Exception:
        n = 5000
    n = max(1, min(n, 20000))
    fmt2 = (fmt or "threadtime").strip() or "threadtime"
    fs = _build_filter_spec(tag_filter, filter_spec)

    pid = _pidof(device_serial, package_name)
    cmd = adb_prefix(device_serial) + ["logcat", "-d", "-v", fmt2, "-t", str(n)]
    used_pid = False
    if pid is not None:
        cmd += ["--pid", str(pid)]
        used_pid = True
    if fs:
        cmd.append(fs)

    proc = CommandRunner.run(cmd, check=False, log_output=False)
    # If --pid is unsupported, retry without pid.
    if proc.returncode != 0 and used_pid:
        cmd = adb_prefix(device_serial) + ["logcat", "-d", "-v", fmt2, "-t", str(n)]
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


