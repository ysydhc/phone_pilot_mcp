#!/usr/bin/env python3
"""
HarmonyOS screenshot helpers (via hdc).

We intentionally DO NOT use HdcCommandRunner.run() for binary output because it uses text=True.
"""

from __future__ import annotations

import pathlib
import subprocess
import tempfile
import time
from typing import Optional

from phone_pilot.harmony.hdc.utils import hdc_prefix, pull_file


def _normalize_png_stream(data: bytes) -> bytes:
    """Normalize CRLF anomalies in PNG byte streams."""
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
    """Fallback: capture to remote file then pull with hdc."""
    png_sig = b"\x89PNG\r\n\x1a\n"
    remote_path = "/data/local/tmp/phone_pilot_screen.png"
    local_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    local_tmp.close()
    try:
        cmd_capture = hdc_prefix(device_serial) + ["shell", "screencap", "-p", remote_path]
        subprocess.run(cmd_capture, check=False, capture_output=True, timeout=8.0, stdin=subprocess.DEVNULL)
        pull_res = pull_file(device_serial, remote_path, local_tmp.name)
        if not pull_res.get("ok"):
            raise RuntimeError(f"screencap pull failed: {pull_res.get('stderr')}")
        data = pathlib.Path(local_tmp.name).read_bytes()
        data = _normalize_png_stream(data)
        if data and data.startswith(png_sig):
            return data
        raise RuntimeError(f"screencap failed: invalid_png_stream(bytes={len(data)})")
    finally:
        try:
            subprocess.run(hdc_prefix(device_serial) + ["shell", "rm", "-f", remote_path], check=False, capture_output=True, timeout=6.0, stdin=subprocess.DEVNULL)
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
      hdc [-t SERIAL] shell screencap -p
    """
    cmd = hdc_prefix(device_serial) + ["shell", "screencap", "-p"]
    last_err = ""
    for attempt in range(1, 4):
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
    return _fallback_screencap_file(device_serial)


__all__ = ["take_screenshot_png_bytes"]
