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
import sys
import time
from typing import Optional

from android_tool.adb_utils import adb_prefix


def take_screenshot_png_bytes(device_serial: Optional[str]) -> bytes:
    """
    Capture a screenshot from the device and return PNG bytes.

    Uses:
      adb [-s SERIAL] exec-out screencap -p

    Note:
    - Some adb/device combinations may emit CRLF in the PNG stream; we normalize CRLF -> LF.
    """
    cmd = adb_prefix(device_serial) + ["exec-out", "screencap", "-p"]
    png_sig = b"\x89PNG\r\n\x1a\n"

    # Best-effort retries:
    # - Sometimes `adb exec-out screencap -p` returns empty/partial output under load.
    # - Sometimes output is not a valid PNG stream; downstream cv2.imdecode would fail.
    last_err = ""
    for attempt in range(1, 4):
        print(f"[screenshot] capture (try={attempt}): {' '.join(cmd)}", file=sys.stderr)
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, timeout=6.0)
        except subprocess.TimeoutExpired as e:
            last_err = f"timeout after 6.0s"
            if attempt < 3:
                time.sleep(0.15)
                continue
            raise RuntimeError(f"screencap failed: {last_err}") from e
        if proc.returncode != 0:
            last_err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            if attempt < 3:
                time.sleep(0.15)
                continue
            raise RuntimeError(f"screencap failed (rc={proc.returncode}): {last_err}")

        data = proc.stdout or b""
        # Some environments produce CRCRLF sequences in the PNG stream (commonly seen when line endings
        # are incorrectly transformed). Fix only that pattern without breaking the PNG signature.
        if b"\r\r\n" in data:
            data = data.replace(b"\r\r\n", b"\r\n")

        if data and data.startswith(png_sig):
            return data

        last_err = f"invalid_png_stream(bytes={len(data)})"
        if attempt < 3:
            time.sleep(0.15)
            continue
        raise RuntimeError(f"screencap failed: {last_err}")


def save_screenshot_png(device_serial: Optional[str], out_path: str) -> pathlib.Path:
    """
    Capture and save screenshot to out_path (PNG).
    Returns out_path.
    """
    data = take_screenshot_png_bytes(device_serial)
    data_path = pathlib.Path(out_path)
    print(f"save_screenshot_png: {data_path}")
    try:
        data_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"save_screenshot_png error: {e}")
    data_path.write_bytes(data)
    return data_path


def png_bytes_to_base64(data: bytes) -> str:
    """Encode PNG bytes to base64 string (no data: prefix)."""
    return base64.b64encode(data).decode("ascii")


