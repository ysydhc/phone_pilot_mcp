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

from phone_pilot.android.adb.runner import CommandRunner


_ADB_CACHED: Optional[str] = None
_ENV_ENSURED: bool = False


def ensure_adb_env() -> None:
    """
    在未设置 ADB_PATH/ANDROID_HOME 时，从常见路径发现 adb 并写入 os.environ，
    避免 MCP 被外部进程以空 env 启动时，首次成功、后续因 PATH/env 不一致失败。

    When ADB_PATH/ANDROID_HOME are not set, discover adb from common paths and set
    os.environ so that all tools see a consistent env (avoids "Not connected" after
    first successful call when MCP is started by external clients without env).
    """
    global _ENV_ENSURED, _ADB_CACHED
    if _ENV_ENSURED:
        return
    if (os.environ.get("ADB_PATH") or os.environ.get("ANDROID_ADB_PATH") or "").strip():
        _ENV_ENSURED = True
        return
    if (os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or "").strip():
        _ENV_ENSURED = True
        return

    candidates: list[str] = [
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
    ]
    if sys.platform == "darwin":
        try:
            import glob
            candidates += sorted(glob.glob("/Users/*/Library/Android/sdk/platform-tools/adb"))
            candidates += sorted(glob.glob("/Users/*/Android/Sdk/platform-tools/adb"))
        except Exception:
            pass
    for p in candidates:
        if p and os.path.exists(p) and os.access(p, os.X_OK):
            os.environ["ADB_PATH"] = p
            sdk_root = str(os.path.dirname(os.path.dirname(p)))
            if not os.environ.get("ANDROID_HOME") and not os.environ.get("ANDROID_SDK_ROOT"):
                os.environ["ANDROID_HOME"] = sdk_root
            path_dir = os.path.dirname(p)
            path_val = os.environ.get("PATH", "")
            if path_dir and path_dir not in path_val:
                os.environ["PATH"] = path_dir + os.pathsep + path_val
            _ADB_CACHED = None
            _ENV_ENSURED = True
            return

    which = shutil.which("adb")
    if which:
        os.environ["ADB_PATH"] = which
        _ADB_CACHED = None
        _ENV_ENSURED = True
        return

    _ENV_ENSURED = True
    return


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


def push_file(device: Optional[str], local_path: str, remote_path: str) -> None:
    """Push a local file to device."""
    CommandRunner.run(adb_prefix(device) + ["push", local_path, remote_path])


# Alias for backward compatibility
push_script = push_file


__all__ = [
    "adb_executable",
    "adb_prefix",
    "wait_for_device",
    "push_file",
    "push_script",
    "ensure_adb_env",
]
