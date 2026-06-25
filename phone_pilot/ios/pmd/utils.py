"""
iOS pmd utils — device discovery, form-factor detection, tunnel probe, UDID.

These helpers wrap pymobiledevice3 (real devices) and `xcrun simctl`
(simulators) via the pmd runner. They are the iOS analogue of
harmony/hdc/utils.py + the factory's device-probe helpers.

Form-factor split (design ADR-6):
- simulator: appears in `xcrun simctl list devices booted`
- real:      appears in `pymobiledevice3 usbmux list`

Tunnel (design ADR-3): real iOS 17+ devices need a RemoteXPC tunnel
(`pymobiledevice3 remote tunneld`, root). We only *probe* it and return a
diagnostic; we never start/own it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from typing import List, Optional

from phone_pilot.ios.pmd import runner

# ---------------------------------------------------------------------------
# UDID shapes (design §4 / Plan T4b)
# ---------------------------------------------------------------------------

_UDID_40HEX = re.compile(r"\b[0-9a-fA-F]{40}\b")
_UDID_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# iOS 16+ real-device style: 8 hex + '-' + 16 hex (no dashes inside the tail)
_UDID_IOS16 = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{16}\b")


def is_valid_udid(s: str) -> bool:
    """Return True if ``s`` is a valid iOS UDID (40-hex, UUID, or iOS16 style)."""
    if not s:
        return False
    return bool(
        re.fullmatch(r"[0-9a-fA-F]{40}", s)
        or re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            s,
        )
        or re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{16}", s)
    )


# ---------------------------------------------------------------------------
# Executable resolution (mirrors harmony hdc_executable)
# ---------------------------------------------------------------------------

_PMD_CACHED: Optional[str] = None
_XCRUN_CACHED: Optional[str] = None


def pmd_executable() -> str:
    """
    Locate the pymobiledevice3 executable.

    Resolution order:
      1) PYMOBILEDEVICE3_PATH env var (explicit)
      2) shutil.which("pymobiledevice3")
    Raises RuntimeError with an install hint if not found.
    """
    global _PMD_CACHED
    if _PMD_CACHED:
        return _PMD_CACHED

    env_path = (os.environ.get("PYMOBILEDEVICE3_PATH") or "").strip()
    if env_path:
        _PMD_CACHED = env_path
        return _PMD_CACHED

    which = shutil.which("pymobiledevice3")
    if which:
        _PMD_CACHED = which
        return _PMD_CACHED

    raise RuntimeError(
        "pymobiledevice3 not found. Install with: pip install phone-pilot[ios] "
        "(or set PYMOBILEDEVICE3_PATH)."
    )


def xcrun_executable() -> str:
    """
    Locate the `xcrun` executable (gateway to simctl). macOS only.

    Resolution order:
      1) shutil.which("xcrun")
      2) Fallback to literal "xcrun"
    """
    global _XCRUN_CACHED
    if _XCRUN_CACHED:
        return _XCRUN_CACHED
    which = shutil.which("xcrun")
    _XCRUN_CACHED = which or "xcrun"
    return _XCRUN_CACHED


_SIMCTL_CACHED: Optional[list[str]] = None


def simctl_prefix() -> list[str]:
    """
    Return the command prefix to invoke `simctl` (list form).

    `xcrun simctl` fails when xcode-select points at CommandLineTools (no
    simctl there). Resolution order:
      1) SIMCTL_PATH env var (explicit)
      2) DEVELOPER_DIR/usr/bin/simctl
      3) the simctl bundled in a full Xcode.app under /Applications
      4) fallback to ["xcrun", "simctl"]
    """
    global _SIMCTL_CACHED
    if _SIMCTL_CACHED:
        return list(_SIMCTL_CACHED)

    env_path = (os.environ.get("SIMCTL_PATH") or "").strip()
    if env_path and os.access(env_path, os.X_OK):
        _SIMCTL_CACHED = [env_path]
        return list(_SIMCTL_CACHED)

    dev_dir = (os.environ.get("DEVELOPER_DIR") or "").strip()
    candidates = []
    if dev_dir:
        candidates.append(os.path.join(dev_dir, "usr", "bin", "simctl"))
    candidates.append("/Applications/Xcode.app/Contents/Developer/usr/bin/simctl")
    for c in candidates:
        if c and os.access(c, os.X_OK):
            _SIMCTL_CACHED = [c]
            return list(_SIMCTL_CACHED)

    _SIMCTL_CACHED = [xcrun_executable(), "simctl"]
    return list(_SIMCTL_CACHED)


# ---------------------------------------------------------------------------
# Device listing
# ---------------------------------------------------------------------------


def _list_real_serials() -> List[str]:
    """List connected real-device UDIDs via `pymobiledevice3 usbmux list`."""
    try:
        exe = pmd_executable()
    except RuntimeError:
        return []
    try:
        proc = runner.run([exe, "usbmux", "list", "--no-color"], timeout_s=10.0)
    except Exception:
        return []
    out = proc.stdout or ""
    serials: List[str] = []

    # Preferred: JSON output → list of dicts with an Identifier/SerialNumber/udid.
    try:
        data = json.loads(out)
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                uid = (
                    item.get("Identifier")
                    or item.get("SerialNumber")
                    or item.get("udid")
                    or item.get("UniqueDeviceID")
                )
                if uid and is_valid_udid(str(uid)):
                    serials.append(str(uid))
        if serials:
            return _dedup(serials)
    except (ValueError, TypeError):
        pass

    # Fallback: regex-scan any UDID-shaped tokens from non-JSON output.
    for pat in (_UDID_UUID, _UDID_IOS16, _UDID_40HEX):
        for m in pat.findall(out):
            serials.append(m)
    return _dedup(serials)


def _list_simulator_serials() -> List[str]:
    """List *booted* simulator UDIDs via `simctl list devices booted -j`."""
    try:
        proc = runner.run(
            simctl_prefix() + ["list", "devices", "booted", "-j"], timeout_s=10.0
        )
    except Exception:
        return []
    out = proc.stdout or ""
    serials: List[str] = []
    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        return []
    devices = data.get("devices", {}) if isinstance(data, dict) else {}
    for _runtime, items in devices.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("state") == "Booted" and item.get("udid"):
                serials.append(str(item["udid"]))
    return _dedup(serials)


def list_ios_serials() -> List[str]:
    """Return all connected iOS UDIDs (real devices + booted simulators)."""
    return _dedup(_list_real_serials() + _list_simulator_serials())


def _dedup(items: List[str]) -> List[str]:
    """Order-preserving de-duplication."""
    seen: set[str] = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------------------
# Form-factor detection (design ADR-6)
# ---------------------------------------------------------------------------


def detect_form_factor(serial: str) -> str:
    """Return "simulator" | "real" | "unknown" for the given serial."""
    if serial in _list_simulator_serials():
        return "simulator"
    if serial in _list_real_serials():
        return "real"
    return "unknown"


# ---------------------------------------------------------------------------
# Tunnel probe (design ADR-3) — probe + diagnostic, never own
# ---------------------------------------------------------------------------


def probe_tunnel(serial: str) -> dict:
    """
    Probe the RemoteXPC tunnel for a real iOS 17+ device.

    Simulators never need a tunnel → {"ok": True, "form_factor": "simulator"}.
    For real devices, attempt `pymobiledevice3 remote tunnel-info` (read-only);
    on failure return a structured, actionable diagnostic (design ADR-3).
    """
    if detect_form_factor(serial) == "simulator":
        return {"ok": True, "form_factor": "simulator"}

    try:
        exe = pmd_executable()
    except RuntimeError:
        return _tunnel_unavailable()

    try:
        proc = runner.run([exe, "remote", "tunnel-info", "--no-color"], timeout_s=8.0)
    except Exception:
        return _tunnel_unavailable()

    if proc.returncode == 0 and (proc.stdout or "").strip():
        return {"ok": True, "form_factor": "real"}
    return _tunnel_unavailable()


def _tunnel_unavailable() -> dict:
    return {
        "ok": False,
        "error": "ios_tunnel_unavailable",
        "hint": "iOS 17+ requires a RemoteXPC tunnel. Run: "
        "sudo pymobiledevice3 remote tunneld",
    }


__all__ = [
    "is_valid_udid",
    "pmd_executable",
    "xcrun_executable",
    "list_ios_serials",
    "detect_form_factor",
    "probe_tunnel",
]
