#!/usr/bin/env python3
"""
Small HDC helpers shared across HarmonyOS modules.

Why this exists:
- Avoid duplicated `hdc_prefix()` / command resolution.
- Keep call sites consistent and easy to evolve.
"""

from __future__ import annotations

import os
import shutil
from typing import List, Optional

from phone_pilot.harmony.hdc.runner import HdcCommandRunner


_HDC_CACHED: Optional[str] = None


def hdc_executable() -> str:
    """
    Return a usable hdc executable path.

    Resolution order:
    1) HDC_PATH / HARMONY_HDC_PATH env var (explicit)
    2) `shutil.which("hdc")` from PATH
    3) Fallback to literal "hdc"
    """
    global _HDC_CACHED
    if _HDC_CACHED:
        return _HDC_CACHED

    env_path = (os.environ.get("HDC_PATH") or os.environ.get("HARMONY_HDC_PATH") or "").strip()
    if env_path:
        _HDC_CACHED = env_path
        return _HDC_CACHED

    which = shutil.which("hdc")
    if which:
        _HDC_CACHED = which
        return _HDC_CACHED

    candidates = [
        "/Applications/DevEco-Studio.app/Contents/sdk/default/openharmony/toolchains/hdc",
        "/Applications/DevEco-Studio.app/Contents/sdk/openharmony/toolchains/hdc",
    ]
    for p in candidates:
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            _HDC_CACHED = p
            return _HDC_CACHED

    _HDC_CACHED = "hdc"
    return _HDC_CACHED


def hdc_prefix(device: Optional[str]) -> List[str]:
    """Return base hdc command, optionally pinned to a device serial."""
    # HDC uses -t <device> to target a specific device.
    return [hdc_executable()] + (["-t", device] if device else [])


def wait_for_device(device: Optional[str]) -> None:
    """
    Best-effort wait for device to be present.

    Note: HDC lacks a consistent wait-for-device; we probe shell once.
    """
    cmd = hdc_prefix(device) + ["shell", "echo", "ok"]
    HdcCommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False, timeout_s=8.0)


def push_file(device: Optional[str], local_path: str, remote_path: str) -> dict:
    """Push a local file to device using `hdc file send`."""
    cmd = hdc_prefix(device) + ["file", "send", local_path, remote_path]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stderr": (proc.stderr or "").strip()}


def pull_file(device: Optional[str], remote_path: str, local_path: str) -> dict:
    """Pull a remote file to local using `hdc file recv`."""
    cmd = hdc_prefix(device) + ["file", "recv", remote_path, local_path]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "stderr": (proc.stderr or "").strip()}


__all__ = [
    "hdc_executable",
    "hdc_prefix",
    "wait_for_device",
    "push_file",
    "pull_file",
]
