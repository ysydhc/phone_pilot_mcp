#!/usr/bin/env python3
"""
Small ADB helpers shared across scripts.

Why this exists:
- Avoid duplicated `adb_prefix()`/`wait_for_device()` implementations.
- Keep call sites consistent and easy to evolve.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import List, Optional

from android_tool.runner import CommandRunner


_ADB_CACHED: Optional[str] = None


def adb_executable() -> str:
    """
    Return a usable adb executable path.

    Resolution order:
    1) ADB_PATH / ANDROID_ADB_PATH env var (explicit)
    2) ANDROID_HOME / ANDROID_SDK_ROOT + platform-tools/adb
    3) `shutil.which("adb")` from PATH
    4) Common macOS locations (homebrew + Android SDK)
    5) Fallback to literal "adb"

    Rationale:
    - MCP servers spawned from different environments may not inherit the same PATH.
    - Using an absolute path makes tool execution more reliable.
    """
    global _ADB_CACHED
    if _ADB_CACHED:
        return _ADB_CACHED

    env_path = (os.environ.get("ADB_PATH") or os.environ.get("ANDROID_ADB_PATH") or "").strip()
    if env_path:
        _ADB_CACHED = env_path
        return _ADB_CACHED

    sdk_root = (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "").strip()
    if sdk_root:
        cand = os.path.join(sdk_root, "platform-tools", "adb")
        if os.path.exists(cand) and os.access(cand, os.X_OK):
            _ADB_CACHED = cand
            return _ADB_CACHED

    candidates = [
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
    ]
    # If HOME is not set / user resolution is odd (some spawned MCP environments),
    # fall back to scanning common per-user SDK locations on macOS.
    if sys.platform == "darwin":
        try:
            import glob

            candidates += sorted(glob.glob("/Users/*/Library/Android/sdk/platform-tools/adb"))
            candidates += sorted(glob.glob("/Users/*/Android/Sdk/platform-tools/adb"))
        except Exception:
            pass
    for p in candidates:
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            _ADB_CACHED = p
            return _ADB_CACHED

    which = shutil.which("adb")
    if which:
        _ADB_CACHED = which
        return _ADB_CACHED

    _ADB_CACHED = "adb"
    return _ADB_CACHED


def adb_prefix(device: Optional[str]) -> List[str]:
    """Return base adb command, optionally pinned to a device serial."""
    return [adb_executable()] + (["-s", device] if device else [])


def wait_for_device(device: Optional[str]) -> None:
    """
    Block until device is present and boot has completed (best-effort).
    """
    CommandRunner.run(adb_prefix(device) + ["wait-for-device"])
    # Some devices return empty until fully booted; we don't hard-fail here.
    CommandRunner.run(adb_prefix(device) + ["shell", "getprop", "sys.boot_completed"], check=False)


def push_script(device: Optional[str], local_path: str, remote_path: str) -> None:
    """Push a local file to device."""
    CommandRunner.run(adb_prefix(device) + ["push", local_path, remote_path])


