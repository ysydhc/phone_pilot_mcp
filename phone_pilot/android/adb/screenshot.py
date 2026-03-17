#!/usr/bin/env python3
"""
Android screenshot helpers (via adb).

We intentionally DO NOT use CommandRunner.run() here because screenshots are binary output.
CommandRunner.run() runs subprocess with text=True which would corrupt PNG bytes.
"""

from __future__ import annotations

import base64
import pathlib
import subprocess
import tempfile
import time
from typing import Optional

from phone_pilot.android.adb.utils import adb_prefix


def _normalize_png_stream(data: bytes) -> bytes:
    """Normalize CRLF anomalies in PNG byte streams from adb."""
    if not data:
        return data
    if b"\r\r\n" in data:
        data = data.replace(b"\r\r\n", b"\r\n")
    return data


def _try_screencap_cmd(cmd: list[str], *, timeout_s: float = 6.0) -> bytes:
    """Run a screencap command and validate the PNG signature."""
    png_sig = b"\x89PNG\r\n\x1a\n"
    proc = subprocess.run(cmd, check=False, capture_output=True, timeout=timeout_s, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"screencap failed (rc={proc.returncode}): {err}")
    data = _normalize_png_stream(proc.stdout or b"")
    if data and data.startswith(png_sig):
        return data
    raise RuntimeError(f"screencap failed: invalid_png_stream(bytes={len(data)})")


def _fallback_screencap_file(device_serial: Optional[str]) -> bytes:
    """Fallback path: save screencap to file, pull, then read bytes."""
    png_sig = b"\x89PNG\r\n\x1a\n"
    remote_path = "/sdcard/phone_pilot_screen.png"
    local_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    local_tmp.close()
    try:
        cmd_capture = adb_prefix(device_serial) + ["shell", "screencap", "-p", remote_path]
        subprocess.run(cmd_capture, check=False, capture_output=True, timeout=8.0, stdin=subprocess.DEVNULL)
        cmd_pull = adb_prefix(device_serial) + ["pull", remote_path, local_tmp.name]
        subprocess.run(cmd_pull, check=False, capture_output=True, timeout=8.0, stdin=subprocess.DEVNULL)
        data = pathlib.Path(local_tmp.name).read_bytes()
        data = _normalize_png_stream(data)
        if data and data.startswith(png_sig):
            return data
        raise RuntimeError(f"screencap failed: invalid_png_stream(bytes={len(data)})")
    finally:
        try:
            subprocess.run(adb_prefix(device_serial) + ["shell", "rm", "-f", remote_path], check=False, capture_output=True, timeout=6.0, stdin=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            pathlib.Path(local_tmp.name).unlink(missing_ok=True)
        except Exception:
            pass


def take_screenshot_png_bytes(device_serial: Optional[str]) -> bytes:
    """
    Capture a screenshot from the device and return PNG bytes.

    Uses:
      adb [-s SERIAL] exec-out screencap -p

    Note:
    - Some adb/device combinations may emit CRLF in the PNG stream; we normalize CRLF -> LF.
    """
    from phone_pilot.android.adb.runner import _log, _current_log_level

    cmd = adb_prefix(device_serial) + ["exec-out", "screencap", "-p"]
    last_err = ""
    for attempt in range(1, 4):
        # Only log retries (attempt >= 2) or verbose mode
        if attempt > 1 or _current_log_level() >= 2:
            _log(f"[screenshot] capture (try={attempt})")
        try:
            return _try_screencap_cmd(cmd, timeout_s=6.0)
        except subprocess.TimeoutExpired as e:
            last_err = "timeout after 6.0s"
            if attempt < 3:
                time.sleep(0.15)
                continue
            raise RuntimeError(f"screencap failed: {last_err}") from e
        except Exception as e:
            last_err = str(e)
            if attempt < 3:
                time.sleep(0.15)
                continue
            break

    # Fallback 1: adb shell screencap -p
    cmd_shell = adb_prefix(device_serial) + ["shell", "screencap", "-p"]
    for attempt in range(1, 3):
        try:
            _log(f"[screenshot] fallback shell (try={attempt})")
            return _try_screencap_cmd(cmd_shell, timeout_s=8.0)
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(0.2)
                continue

    # Fallback 2: screencap to file + pull
    for attempt in range(1, 3):
        try:
            _log(f"[screenshot] fallback file (try={attempt})")
            return _fallback_screencap_file(device_serial)
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(0.2)
                continue

    raise RuntimeError(f"screencap failed: {last_err}")


def save_screenshot_png(device_serial: Optional[str], out_path: str) -> pathlib.Path:
    """
    Capture and save screenshot to out_path (PNG).
    Returns out_path.
    """
    data = take_screenshot_png_bytes(device_serial)
    data_path = pathlib.Path(out_path)
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    data_path.write_bytes(data)
    return data_path


def png_bytes_to_base64(data: bytes) -> str:
    """Encode PNG bytes to base64 string (no data: prefix)."""
    return base64.b64encode(data).decode("ascii")


__all__ = [
    "take_screenshot_png_bytes",
    "save_screenshot_png",
    "png_bytes_to_base64",
]
