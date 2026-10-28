"""
iOS system operations backed by `simctl` (simulator) — clipboard, app
install/uninstall, restricted shell, and device listing.

These mirror the Android system helpers (phone_pilot/android/device/utils.py)
so the corresponding MCP tools can route to iOS. Real-device equivalents
(pymobiledevice3) are out of scope here; on a real device these return a clear
not_supported until wired.
"""

from __future__ import annotations

import json
import shlex
from typing import List

from phone_pilot.ios.pmd import runner as pmd_runner
from phone_pilot.ios.pmd import utils as pmd_utils

# Same spirit as the Android execute_shell whitelist: only safe, read-only-ish
# commands are permitted. `simctl spawn` requires an ABSOLUTE binary path, so we
# map each whitelisted name to its location inside the simulator runtime.
_SHELL_WHITELIST = {
    "uname": "/usr/bin/uname",
    "sw_vers": "/usr/bin/sw_vers",
    "ls": "/bin/ls",
    "cat": "/bin/cat",
    "echo": "/bin/echo",
    "ps": "/bin/ps",
    "id": "/usr/bin/id",
    "pwd": "/bin/pwd",
    "env": "/usr/bin/env",
    "log": "/usr/bin/log",
    "defaults": "/usr/bin/defaults",
    "plutil": "/usr/bin/plutil",
    "sysctl": "/usr/sbin/sysctl",
}


def _simctl(serial: str, args: List[str], *, timeout_s: float = 20.0, input_text=None):
    return pmd_runner.run(
        pmd_utils.simctl_prefix() + args,
        timeout_s=timeout_s,
        input_text=input_text,
    )


def get_clipboard(serial: str) -> dict:
    """Read the simulator clipboard via `simctl pbpaste`."""
    try:
        proc = _simctl(serial, ["pbpaste", serial])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "platform": "ios"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "pbpaste_failed").strip()[:200]}
    return {"ok": True, "text": proc.stdout or ""}


def set_clipboard(serial: str, text: str) -> dict:
    """Write the simulator clipboard via `simctl pbcopy` (text on stdin)."""
    try:
        proc = _simctl(serial, ["pbcopy", serial], input_text=text)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "platform": "ios"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or "pbcopy_failed").strip()[:200]}
    return {"ok": True, "text": text}


def install_app(serial: str, app_path: str) -> dict:
    """Install a .app/.ipa bundle via `simctl install` (simulator) or `pmd apps install` (real device)."""
    if not app_path or not str(app_path).strip():
        return {"ok": False, "error": "app_path is required", "platform": "ios"}
    form = pmd_utils.detect_form_factor(serial)
    try:
        if form == "real":
            exe = pmd_utils.pmd_executable()
            proc = pmd_runner.run(
                [exe, "apps", "install", app_path, "--udid", serial],
                timeout_s=120.0,
            )
            engine = "pymobiledevice3"
        else:
            proc = _simctl(serial, ["install", serial, app_path], timeout_s=120.0)
            engine = "simctl"
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "platform": "ios"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": (proc.stderr or "install_failed").strip()[:200],
            "platform": "ios",
        }
    return {"ok": True, "app_path": app_path, "engine": engine}


def uninstall_app(serial: str, bundle_id: str) -> dict:
    """Uninstall an app via `simctl uninstall` (simulator) or `pmd apps uninstall` (real device)."""
    form = pmd_utils.detect_form_factor(serial)
    try:
        if form == "real":
            exe = pmd_utils.pmd_executable()
            proc = pmd_runner.run(
                [exe, "apps", "uninstall", bundle_id, "--udid", serial],
                timeout_s=60.0,
            )
            engine = "pymobiledevice3"
        else:
            proc = _simctl(serial, ["uninstall", serial, bundle_id], timeout_s=60.0)
            engine = "simctl"
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "platform": "ios"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": (proc.stderr or "uninstall_failed").strip()[:200],
            "platform": "ios",
        }
    return {"ok": True, "package": bundle_id, "engine": engine}


def execute_shell(serial: str, command: str, timeout_s: float = 30.0) -> dict:
    """Run a whitelisted command inside the simulator via `simctl spawn`."""
    parts = shlex.split(command or "")
    if not parts:
        return {"ok": False, "error": "empty command", "platform": "ios"}
    first = parts[0].rsplit("/", 1)[-1]
    abs_bin = _SHELL_WHITELIST.get(first)
    if abs_bin is None:
        return {
            "ok": False,
            "error": f"command prefix '{first}' not in whitelist",
            "platform": "ios",
        }
    # simctl spawn needs an absolute binary path; substitute the resolved one.
    spawn_args = [abs_bin, *parts[1:]]
    try:
        proc = _simctl(serial, ["spawn", serial, *spawn_args], timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "platform": "ios"}
    return {
        "ok": proc.returncode == 0,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "returncode": proc.returncode,
    }


def list_ios_devices() -> List[dict]:
    """List booted iOS simulators as device entries (serial/status/platform/description)."""
    try:
        proc = pmd_runner.run(
            pmd_utils.simctl_prefix() + ["list", "devices", "-j"], timeout_s=10.0
        )
        data = json.loads(proc.stdout or "{}")
    except Exception:
        return []
    out: List[dict] = []
    for _runtime, items in (data.get("devices", {}) or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or it.get("state") != "Booted":
                continue
            udid = it.get("udid")
            if not udid:
                continue
            out.append(
                {
                    "serial": udid,
                    "status": "device",
                    "platform": "ios",
                    "description": it.get("name"),
                }
            )
    return out


__all__ = [
    "get_clipboard",
    "set_clipboard",
    "install_app",
    "uninstall_app",
    "execute_shell",
    "list_ios_devices",
]
