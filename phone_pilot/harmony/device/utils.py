#!/usr/bin/env python3
"""
HarmonyOS device helpers for basic input/screen/app operations.

This module provides a minimal HDC-only implementation to align with the
DeviceDriver protocol. Capabilities may be limited by HDC support.
"""

from __future__ import annotations

import re
from typing import Optional

from phone_pilot.harmony.hdc.runner import HdcCommandRunner
from phone_pilot.harmony.hdc.utils import hdc_prefix


_WM_SIZE_RE = re.compile(r"^(?:Physical|Override)\s+size:\s*(\d+)x(\d+)\s*$", re.MULTILINE)
_RES_XY_RE = re.compile(r"(\d{3,5})\s*[xX]\s*(\d{3,5})")
_WIDTH_RE = re.compile(r"\bwidth\b\s*[:=]\s*(\d{3,5})", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"\bheight\b\s*[:=]\s*(\d{3,5})", re.IGNORECASE)
_RENDER_RES_RE = re.compile(r"render resolution=(\d{3,5})x(\d{3,5})", re.IGNORECASE)
_PHYS_RES_RE = re.compile(r"physical resolution=(\d{3,5})x(\d{3,5})", re.IGNORECASE)
_FOCUS_RE = re.compile(r"\b(mCurrentFocus|mFocusedApp)=\S+\s+(\S+)/(\S+)\}")


def input_tap(device_serial: Optional[str], x: int, y: int, *, wait_s: float = 0.15) -> dict:
    """Tap at screen coordinates using HDC."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    try:
        xi, yi = int(x), int(y)
    except Exception:
        return {"ok": False, "error": "x/y must be integers", "x": x, "y": y}
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial) + ["shell", "input", "tap", str(xi), str(yi)],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "x": xi, "y": yi, "stderr": (proc.stderr or "").strip()}


def input_swipe(
    device_serial: Optional[str],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    duration_ms: int = 300,
    wait_s: float = 0.15,
) -> dict:
    """Swipe from (x1, y1) to (x2, y2) using HDC."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    try:
        xi1, yi1, xi2, yi2 = int(x1), int(y1), int(x2), int(y2)
    except Exception:
        return {"ok": False, "error": "x/y must be integers", "x1": x1, "y1": y1, "x2": x2, "y2": y2}
    try:
        dur = int(duration_ms)
    except Exception:
        dur = 300
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial) + ["shell", "input", "swipe", str(xi1), str(yi1), str(xi2), str(yi2), str(dur)],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "x1": xi1,
        "y1": yi1,
        "x2": xi2,
        "y2": yi2,
        "duration_ms": dur,
        "stderr": (proc.stderr or "").strip(),
    }


def input_long_press(
    device_serial: Optional[str],
    x: int,
    y: int,
    *,
    duration_ms: int = 800,
    wait_s: float = 0.25,
) -> dict:
    """Long-press at (x,y) using swipe with same start/end."""
    return input_swipe(
        device_serial,
        x,
        y,
        x,
        y,
        duration_ms=duration_ms,
        wait_s=wait_s,
    )


def input_text(device_serial: Optional[str], text: str, *, enter: bool = False) -> dict:
    """
    Input text into focused field.

    HDC-only fallback uses `input text` which typically supports ASCII only.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    t = "" if text is None else str(text)
    if any(ord(ch) > 127 for ch in t):
        return {
            "ok": False,
            "error": "unicode_input_not_supported",
            "note": "HDC-only mode may not support unicode input; consider device-side helper.",
        }
    safe = t.replace(" ", "%s")
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial) + ["shell", "input", "text", safe],
        check=False,
        delay_s=0.15,
        log_output=False,
    )
    if enter:
        HdcCommandRunner.run(
            hdc_prefix(device_serial) + ["shell", "input", "keyevent", "KEYCODE_ENTER"],
            check=False,
            delay_s=0.1,
            log_output=False,
        )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "text": t, "stderr": (proc.stderr or "").strip()}


def input_keyevent(device_serial: Optional[str], keycode: str, *, wait_s: float = 0.12) -> dict:
    """Send a key event via HDC input keyevent."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    k = str(keycode or "").strip()
    if not k:
        return {"ok": False, "error": "keycode is required"}
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial) + ["shell", "input", "keyevent", k],
        check=False,
        delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
        log_output=False,
    )
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "keycode": k, "stderr": (proc.stderr or "").strip()}


def get_screen_size(device_serial: Optional[str]) -> Optional[tuple[int, int]]:
    """Return screen size (width, height) from wm/hidumper outputs."""
    if not device_serial:
        return None
    # 1) Try Android-like wm size (may not exist on HarmonyOS)
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial) + ["shell", "wm", "size"],
        check=False,
        timeout_s=6.0,
        log_output=False,
    )
    text = proc.stdout or ""
    m = _WM_SIZE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 2) Try RenderService screen info (preferred on HarmonyOS)
    proc_rs = HdcCommandRunner.run(
        hdc_prefix(device_serial) + ["shell", "hidumper", "-s", "RenderService", "-a", "screen"],
        check=False,
        timeout_s=8.0,
        log_output=False,
    )
    rs_out = proc_rs.stdout or ""
    m_rs = _RENDER_RES_RE.search(rs_out) or _PHYS_RES_RE.search(rs_out)
    if m_rs:
        return int(m_rs.group(1)), int(m_rs.group(2))

    # 3) Try hidumper display services
    candidates = [
        ["shell", "hidumper", "-s", "DisplayManagerService", "-a", "-h"],
        ["shell", "hidumper", "-s", "WindowManagerService", "-a", "-h"],
        ["shell", "hidumper", "-s", "DisplayService", "-a", "-h"],
    ]
    for args in candidates:
        proc2 = HdcCommandRunner.run(
            hdc_prefix(device_serial) + args,
            check=False,
            timeout_s=8.0,
            log_output=False,
        )
        out = proc2.stdout or ""
        m2 = _RES_XY_RE.search(out)
        if m2:
            return int(m2.group(1)), int(m2.group(2))
        w = _WIDTH_RE.search(out)
        h = _HEIGHT_RE.search(out)
        if w and h:
            return int(w.group(1)), int(h.group(1))

    return None


def list_installed_packages(device_serial: Optional[str], *, include_system: bool = False) -> list[str]:
    """
    Best-effort list installed package/bundle names.

    Uses `bm dump -a` and extracts bundleName fields.
    """
    if not device_serial:
        return []
    cmd = hdc_prefix(device_serial) + ["shell", "bm", "dump", "-a"]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    out = proc.stdout or ""
    pkgs = []
    for line in out.splitlines():
        if "bundleName" in line:
            m = re.search(r"bundleName[:=]\\s*([\\w.\\-]+)", line)
            if m:
                pkgs.append(m.group(1))
    if not pkgs:
        # Fallback: try `bm dump -n <bundle>` not possible without names; return empty list.
        return []
    if include_system:
        return sorted(set(pkgs))
    # Heuristic: filter out system bundles by prefix if possible.
    return sorted({p for p in pkgs if not p.startswith("com.huawei.") and not p.startswith("ohos.")})


def launch_app(device_serial: Optional[str], package: str, activity: Optional[str] = None) -> dict:
    """Launch an app/ability using `aa start`."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "package is required"}
    cmd = hdc_prefix(device_serial) + ["shell", "aa", "start", "-b", pkg]
    if activity:
        cmd += ["-a", activity]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.2, log_output=False)
    return {"ok": proc.returncode == 0, "package": pkg, "activity": activity, "stderr": (proc.stderr or "").strip()}


def force_stop_package(device_serial: Optional[str], package: str) -> dict:
    """Force stop an application using `aa force-stop` (fallback to `aa stop`)."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "package is required"}
    cmd = hdc_prefix(device_serial) + ["shell", "aa", "force-stop", pkg]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.1, log_output=False)
    if proc.returncode != 0:
        cmd = hdc_prefix(device_serial) + ["shell", "aa", "stop", pkg]
        proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.1, log_output=False)
    return {"ok": proc.returncode == 0, "package": pkg, "stderr": (proc.stderr or "").strip()}


def clear_app_data(device_serial: Optional[str], package: str) -> dict:
    """Clear app data via `bm clear -n <package>` (best-effort)."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "package is required"}
    cmd = hdc_prefix(device_serial) + ["shell", "bm", "clear", "-n", pkg]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    return {"ok": proc.returncode == 0, "package": pkg, "stderr": (proc.stderr or "").strip()}


def get_current_focus(device_serial: Optional[str]) -> dict:
    """
    Best-effort current foreground activity info.

    Uses `dumpsys window` pattern similar to Android when available.
    """
    if not device_serial:
        return {"package": None, "activity": None}
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial)
        + [
            "shell",
            "dumpsys window | grep -E 'mCurrentFocus=|mFocusedApp=' | head -n 5",
        ],
        check=False,
        log_output=False,
    )
    m = _FOCUS_RE.search(proc.stdout or "")
    if not m:
        return {"package": None, "activity": None}
    return {"package": m.group(2), "activity": m.group(3)}


__all__ = [
    "input_tap",
    "input_swipe",
    "input_long_press",
    "input_text",
    "input_keyevent",
    "get_screen_size",
    "list_installed_packages",
    "launch_app",
    "force_stop_package",
    "clear_app_data",
    "get_current_focus",
    "clear_background_processes",
]


def clear_background_processes(device_serial: Optional[str]) -> dict:
    """
    Clear background apps on HarmonyOS by opening the recent apps view
    and tapping the 'clear all' button.

    Works by performing a slow swipe-up gesture from the bottom of the screen
    to open the recent apps view, then finding and clicking the clear-all
    button (id: RecentClearAllView_Stack_deleteFull).

    Falls back to force-stopping third-party running apps if the UI approach
    fails.
    """
    import logging
    import time

    logger = logging.getLogger(__name__)

    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    try:
        from phone_pilot.harmony.hmdriver_bridge import get_hmdriver
        hm = get_hmdriver(device_serial)
    except Exception as exc:
        return {"ok": False, "error": f"hmdriver2 not available: {exc}"}

    # --- Strategy 1: Open recent apps via swipe-up gesture and clear all ---
    try:
        # Press HOME first to ensure we're in a known state
        hm.go_home()
        time.sleep(0.8)

        # Slow swipe up from bottom to open recent apps
        w, h = hm.display_size
        start_x = w // 2
        start_y = h - 50
        end_y = h // 2
        hm.swipe(start_x, start_y, start_x, end_y, speed=300)
        time.sleep(1.5)

        # Look for the 'clear all' button by id
        clear_btn = hm(id="RecentClearAllView_Stack_deleteFull")
        if clear_btn.exists():
            clear_btn.click()
            time.sleep(0.8)
            logger.info("Cleared all recent apps via UI")
            # Go back to home
            hm.go_home()
            time.sleep(0.5)
            return {"ok": True, "method": "recent_clear_all", "platform": "harmony"}

        # If clear button not found, maybe there are no recent apps
        # Check if we're in the recent apps view at all
        hierarchy = hm.dump_hierarchy()
        has_recent = _hierarchy_contains_id(hierarchy, "SCBScenePanelCanvas")
        if has_recent:
            # We're in recent view but no clear button - means no apps to clear
            hm.go_home()
            time.sleep(0.5)
            return {"ok": True, "method": "recent_no_apps", "platform": "harmony"}

        logger.debug("Could not find recent apps view via swipe, trying fallback")
    except Exception as exc:
        logger.debug("Recent apps clear failed: %s", exc)

    # --- Strategy 2: Force-stop third-party apps ---
    try:
        apps = hm.list_apps()
        stopped = 0
        # Skip system apps (com.ohos.*, com.huawei.*)
        for bundle in apps:
            bundle_s = str(bundle)
            if bundle_s.startswith(("com.ohos.", "com.huawei.hmos.")):
                continue
            try:
                hm.stop_app(bundle_s)
                stopped += 1
            except Exception:
                continue
        hm.go_home()
        time.sleep(0.5)
        return {"ok": True, "method": "force_stop_3p", "stopped": stopped, "platform": "harmony"}
    except Exception as exc:
        logger.warning("Force-stop fallback failed: %s", exc)

    # Go home regardless
    try:
        hm.go_home()
    except Exception:
        pass

    return {"ok": False, "error": "all clear_background strategies failed"}


def _hierarchy_contains_id(hierarchy: dict, target_id: str) -> bool:
    """Check if any node in the hierarchy has the given id."""
    attrs = hierarchy.get("attributes", {})
    if attrs.get("id") == target_id:
        return True
    for child in hierarchy.get("children", []):
        if _hierarchy_contains_id(child, target_id):
            return True
    return False
