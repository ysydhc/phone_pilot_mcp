#!/usr/bin/env python3
"""
HarmonyOS device helpers for basic input/screen/app operations.

This module provides a minimal HDC-only implementation to align with the
DeviceDriver protocol. Capabilities may be limited by HDC support.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from phone_pilot.harmony.hdc.runner import HdcCommandRunner
from phone_pilot.harmony.hdc.utils import hdc_prefix

logger = logging.getLogger(__name__)


_WM_SIZE_RE = re.compile(
    r"^(?:Physical|Override)\s+size:\s*(\d+)x(\d+)\s*$", re.MULTILINE
)
_RES_XY_RE = re.compile(r"(\d{3,5})\s*[xX]\s*(\d{3,5})")
_WIDTH_RE = re.compile(r"\bwidth\b\s*[:=]\s*(\d{3,5})", re.IGNORECASE)
_HEIGHT_RE = re.compile(r"\bheight\b\s*[:=]\s*(\d{3,5})", re.IGNORECASE)
_RENDER_RES_RE = re.compile(r"render resolution=(\d{3,5})x(\d{3,5})", re.IGNORECASE)
_PHYS_RES_RE = re.compile(r"physical resolution=(\d{3,5})x(\d{3,5})", re.IGNORECASE)
_FOCUS_RE = re.compile(r"\b(mCurrentFocus|mFocusedApp)=\S+\s+(\S+)/(\S+)\}")


def input_tap(
    device_serial: Optional[str], x: int, y: int, *, wait_s: float = 0.15
) -> dict:
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
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "x": xi,
        "y": yi,
        "stderr": (proc.stderr or "").strip(),
    }


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
        return {
            "ok": False,
            "error": "x/y must be integers",
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        }
    try:
        dur = int(duration_ms)
    except Exception:
        dur = 300
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial)
        + ["shell", "input", "swipe", str(xi1), str(yi1), str(xi2), str(yi2), str(dur)],
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
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "text": t,
        "stderr": (proc.stderr or "").strip(),
    }


def input_keyevent(
    device_serial: Optional[str], keycode: str, *, wait_s: float = 0.12
) -> dict:
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
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "keycode": k,
        "stderr": (proc.stderr or "").strip(),
    }


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
        hdc_prefix(device_serial)
        + ["shell", "hidumper", "-s", "RenderService", "-a", "screen"],
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


def list_installed_packages(
    device_serial: Optional[str], *, include_system: bool = False
) -> list[str]:
    """
    Best-effort list installed package/bundle names.

    Uses `bm dump -a` and extracts bundleName fields or listed bundles.
    """
    if not device_serial:
        return []
    cmd = hdc_prefix(device_serial) + ["shell", "bm", "dump", "-a"]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    out = proc.stdout or ""
    pkgs = []
    for line in out.splitlines():
        line_s = line.strip()
        if not line_s or line_s.startswith("ID:"):
            continue
        if "bundleName" in line_s:
            m = re.search(r"bundleName[:=]\s*([\w.\-]+)", line_s)
            if m:
                pkgs.append(m.group(1))
        elif re.match(r"^[a-zA-Z0-9_\-]+(\.[a-zA-Z0-9_\-]+)+$", line_s):
            pkgs.append(line_s)
    if not pkgs:
        # Fallback: try `bm dump -n <bundle>` not possible without names; return empty list.
        return []
    if include_system:
        return sorted(set(pkgs))
    # Heuristic: filter out system bundles by prefix if possible.
    return sorted(
        {
            p
            for p in pkgs
            if not p.startswith("com.huawei.") and not p.startswith("ohos.") and not p.startswith("com.ohos.")
        }
    )


def launch_app(
    device_serial: Optional[str], package: str, activity: Optional[str] = None
) -> dict:
    """Launch an app/ability using `aa start`."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "package is required"}
    act = (activity or "").strip()
    if not act:
        # Try to resolve mainElementName via bm dump -n
        dump_proc = HdcCommandRunner.run(
            hdc_prefix(device_serial) + ["shell", "bm", "dump", "-n", pkg],
            check=False,
            timeout_s=5.0,
            log_output=False,
        )
        if dump_proc.stdout:
            m_main = re.search(r'"mainElementName"\s*:\s*"([^"]+)"', dump_proc.stdout)
            if m_main:
                act = m_main.group(1).strip()
    cmd = hdc_prefix(device_serial) + ["shell", "aa", "start", "-b", pkg]
    if act:
        cmd += ["-a", act]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.2, log_output=False)
    return {
        "ok": proc.returncode == 0,
        "package": pkg,
        "activity": act or None,
        "stderr": (proc.stderr or "").strip(),
    }


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
    return {
        "ok": proc.returncode == 0,
        "package": pkg,
        "stderr": (proc.stderr or "").strip(),
    }


def clear_app_data(device_serial: Optional[str], package: str) -> dict:
    """Clear app data via `bm clear -n <package>` (best-effort)."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "package is required"}
    cmd = hdc_prefix(device_serial) + ["shell", "bm", "clear", "-n", pkg]
    proc = HdcCommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    return {
        "ok": proc.returncode == 0,
        "package": pkg,
        "stderr": (proc.stderr or "").strip(),
    }


def open_deeplink(
    device_serial: Optional[str],
    uri: str,
    package: Optional[str] = None,
    wait_s: float = 0.8,
) -> dict:
    """Open a deeplink/scheme URI using HarmonyOS Ability Assistant:

      hdc shell aa start -U <uri> [-b <package>]

    Args:
        device_serial: Device serial number.
        uri: Deeplink URI (e.g. "myapp://page/detail").
        package: Optional target package / bundle name.
        wait_s: Delay in seconds after running the command.

    Returns:
        dict: Standard result dict with status and metadata.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    u = (uri or "").strip()
    if not u:
        return {"ok": False, "error": "uri is required"}

    pkg = package.strip() if package and isinstance(package, str) and package.strip() else None
    cmd = hdc_prefix(device_serial) + ["shell", "aa", "start", "-U", shlex.quote(u)]
    if pkg:
        cmd += ["-b", shlex.quote(pkg)]

    try:
        proc = HdcCommandRunner.run(
            cmd,
            check=False,
            delay_s=float(wait_s) if wait_s and wait_s > 0 else 0.0,
            log_output=False,
        )
        res = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "device_serial": device_serial,
            "uri": u,
            "package": pkg,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "platform": "harmony",
        }
        if proc.returncode != 0:
            res["error"] = (
                proc.stderr or proc.stdout or f"aa start exited with code {proc.returncode}"
            ).strip()
        return res
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "device_serial": device_serial,
            "uri": u,
            "package": pkg,
            "platform": "harmony",
        }


def get_current_focus(device_serial: Optional[str]) -> dict:
    """
    Best-effort current foreground activity info.

    Prefers `hidumper -s WindowManagerService -a '-a'`, falling back to
    dumpsys window patterns.
    """
    if not device_serial:
        return {"package": None, "activity": None}

    # 1. Try HarmonyOS WindowManagerService dump
    proc = HdcCommandRunner.run(
        hdc_prefix(device_serial)
        + ["shell", "hidumper", "-s", "WindowManagerService", "-a", "-a"],
        check=False,
        timeout_s=6.0,
        log_output=False,
    )
    out = proc.stdout or ""
    focus_m = re.search(r"Focus window:\s*(\d+)", out)
    if focus_m:
        win_id = focus_m.group(1)
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2].isdigit() and parts[3] == win_id:
                wname, pid = parts[0], parts[2]
                bundle = None
                if wname.startswith(("SCB", "BackgroundBlur", "TransparentView")):
                    bundle = "com.ohos.sceneboard"
                else:
                    try:
                        p_cmd = HdcCommandRunner.run(
                            hdc_prefix(device_serial)
                            + ["shell", f"cat /proc/{pid}/cmdline"],
                            check=False,
                            timeout_s=2.0,
                            log_output=False,
                        )
                        c_out = (p_cmd.stdout or "").strip().replace("\x00", "")
                        if c_out:
                            bundle = c_out
                    except Exception:
                        pass
                return {"package": bundle or wname, "activity": wname}

    # 2. Fallback to Android/dumpsys pattern
    proc_dump = HdcCommandRunner.run(
        hdc_prefix(device_serial)
        + [
            "shell",
            "dumpsys window | grep -E 'mCurrentFocus=|mFocusedApp=' | head -n 5",
        ],
        check=False,
        timeout_s=4.0,
        log_output=False,
    )
    m = _FOCUS_RE.search(proc_dump.stdout or "")
    if m:
        return {"package": m.group(2), "activity": m.group(3)}
    return {"package": None, "activity": None}


__all__ = [
    "input_tap",
    "input_swipe",
    "input_long_press",
    "input_text",
    "input_keyevent",
    "get_screen_size",
    "list_installed_packages",
    "launch_app",
    "open_deeplink",
    "force_stop_package",
    "clear_app_data",
    "get_current_focus",
    "clear_background_processes",
    "execute_shell",
    "get_clipboard_text",
    "get_default_launcher_component",
    "list_all_launcher_components",
    "build_launch_profiles_snapshot",
    "launch_from_home",
    "lock_screen",
    "unlock_screen",
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
        return {
            "ok": True,
            "method": "force_stop_3p",
            "stopped": stopped,
            "platform": "harmony",
        }
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


# 允许的 shell 命令前缀（鸿蒙专属）/ Allowed command prefixes for HarmonyOS execute_shell
_SHELL_WHITELIST = frozenset(
    [
        "bm",
        "aa",
        "hidumper",
        "param",
        "input",
        "wm",
        "hilog",
        "ps",
        "top",
        "cat",
        "ls",
        "df",
        "id",
        "uitest",
        "snapshot_display",
    ]
)

# 禁止的命令前缀 / Forbidden command prefixes
_SHELL_FORBIDDEN = frozenset(["reboot", "shutdown", "mkfs", "dd"])


def execute_shell(
    device_serial: Optional[str] = None,
    command: str = "",
    timeout_s: float = 30.0,
) -> dict:
    """执行受限 shell 命令（白名单限制）。
    Execute restricted shell command (whitelist limited).

    Args:
        device_serial: 设备序列号 / Device serial
        command: shell 命令 / Shell command
        timeout_s: 超时秒数 / Timeout in seconds

    Returns:
        dict: {"ok": True, "stdout": str, "stderr": str, "returncode": int}
        或 {"ok": False, "error": str}
    """
    try:
        cmd_stripped = (command or "").strip()
        if not cmd_stripped:
            return {"ok": False, "error": "command is required"}
        parts = cmd_stripped.split()
        first = parts[0] if parts else ""
        # 检查禁止前缀 / Check forbidden prefixes
        for forbidden in _SHELL_FORBIDDEN:
            if cmd_stripped.startswith(forbidden):
                return {
                    "ok": False,
                    "error": f"command prefix '{forbidden}' is forbidden",
                }
        if cmd_stripped.startswith("rm -rf") or cmd_stripped.startswith("rm -fr"):
            return {"ok": False, "error": "rm -rf is forbidden"}
        # 检查白名单 / Check whitelist
        if first not in _SHELL_WHITELIST:
            return {"ok": False, "error": f"command prefix '{first}' not in whitelist"}
        full_cmd = hdc_prefix(device_serial) + ["shell"] + parts
        proc = HdcCommandRunner.run(
            full_cmd, check=False, log_output=False, timeout_s=timeout_s
        )
        return {
            "ok": True,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "returncode": proc.returncode,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _extract_clipboard_text(raw_dump: str) -> str:
    """Extract clipboard text from hidumper PasteboardService dump output."""
    if not raw_dump or "No copy data" in raw_dump:
        return ""
    patterns = [
        r"(?:primaryText|plainText|text|content)\s*[:=]\s*[\"']?([^\r\n\"']+)[\"']?",
        r"Record\[\d+\].*?(?:text|content)[:=]\s*[\"']?([^\r\n\"']+)[\"']?",
    ]
    for pat in patterns:
        m = re.search(pat, raw_dump, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    if "PasteboardService" not in raw_dump and "Record[" not in raw_dump and ":" not in raw_dump:
        return raw_dump.strip()
    return ""


def get_clipboard_text(device_serial: Optional[str] = None) -> dict:
    """获取剪贴板文本。
    优先尝试 hmdriver2 剪贴板读取，失败时兜底执行 `hidumper -s PasteboardService`。

    Args:
        device_serial: 设备序列号 / Device serial

    Returns:
        dict: {"ok": True, "text": str} 或 {"ok": False, "error": str}
    """
    # 1) Try hmdriver2 if available and has clipboard capability
    if device_serial:
        try:
            from phone_pilot.harmony.hmdriver_bridge import get_hmdriver

            hm = get_hmdriver(device_serial)
            for attr in ("get_clipboard_text", "get_clipboard", "clipboard", "pasteboard"):
                if hasattr(hm, attr):
                    val = getattr(hm, attr)
                    res = val() if callable(val) else val
                    if isinstance(res, dict):
                        return res
                    if isinstance(res, str):
                        return {"ok": True, "text": res}
        except Exception as exc:
            logger.debug("hmdriver2 clipboard read failed, fallback to hidumper: %s", exc)

    # 2) Fallback: execute hidumper -s PasteboardService via HDC
    try:
        cmd = hdc_prefix(device_serial) + ["shell", "hidumper", "-s", "PasteboardService"]
        proc = HdcCommandRunner.run(cmd, check=False, log_output=False)
        if proc.returncode != 0:
            return {
                "ok": False,
                "error": f"hidumper PasteboardService failed: {(proc.stderr or proc.stdout or '').strip()}",
            }
        stdout = (proc.stdout or "").strip()
        text = _extract_clipboard_text(stdout)
        return {"ok": True, "text": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


_KNOWN_LAUNCHER_BUNDLES = (
    "com.ohos.sceneboard",
    "com.ohos.launcher",
    "com.huawei.hmos.launcher",
)
_HOME_ACTIONS = frozenset(
    [
        "action.system.home",
        "action.ohos.home",
        "ohos.want.action.home",
    ]
)
_HOME_ENTITIES = frozenset(["entity.system.home"])
_ENTRY_ACTIONS = frozenset(
    [
        "action.system.home",
        "action.ohos.home",
        "ohos.want.action.home",
        "action.system.entry",
        "action.ohos.entry",
    ]
)
_ENTRY_ENTITIES = frozenset(["entity.system.home", "entity.system.browsable"])


def _parse_bm_dump_abilities(raw_output: str) -> list[dict]:
    """
    Parse abilities and skills from OpenHarmony `bm dump` outputs.
    Supports both JSON and line-formatted dumps.

    Returns:
        List of dicts: [
            {
                "bundle_name": str,
                "ability_name": str,
                "actions": list[str],
                "entities": list[str],
                "is_main": bool,
            }, ...
        ]
    """
    text = (raw_output or "").strip()
    if not text:
        return []

    # 1. Try parsing JSON
    try:
        # If output has leading/trailing banner text, locate json bounds
        json_str = text
        start_bracket = text.find("[")
        start_brace = text.find("{")
        first_idx = -1
        if start_bracket != -1 and start_brace != -1:
            first_idx = min(start_bracket, start_brace)
        elif start_bracket != -1:
            first_idx = start_bracket
        elif start_brace != -1:
            first_idx = start_brace

        if first_idx != -1:
            last_bracket = text.rfind("]")
            last_brace = text.rfind("}")
            last_idx = max(last_bracket, last_brace)
            if last_idx > first_idx:
                json_str = text[first_idx : last_idx + 1]

        data = json.loads(json_str)
        bundle_list = []
        if isinstance(data, list):
            bundle_list = data
        elif isinstance(data, dict):
            if "bundles" in data and isinstance(data["bundles"], list):
                bundle_list = data["bundles"]
            elif "bundleList" in data and isinstance(data["bundleList"], list):
                bundle_list = data["bundleList"]
            elif "bundleName" in data or "name" in data:
                bundle_list = [data]
            else:
                for k, v in data.items():
                    if isinstance(v, dict):
                        b_dict = dict(v)
                        if "bundleName" not in b_dict:
                            b_dict["bundleName"] = k
                        bundle_list.append(b_dict)

        results: list[dict] = []
        for b in bundle_list:
            if not isinstance(b, dict):
                continue
            bundle_name = b.get("bundleName") or b.get("name") or ""
            if not bundle_name:
                continue
            main_elem = b.get("mainElement") or b.get("mainAbility") or ""
            abilities = b.get("abilities") or []
            if not isinstance(abilities, list):
                abilities = []

            if not abilities and main_elem:
                results.append(
                    {
                        "bundle_name": bundle_name,
                        "ability_name": main_elem,
                        "actions": ["action.system.home"],
                        "entities": ["entity.system.home"],
                        "is_main": True,
                    }
                )
                continue

            for ab in abilities:
                if not isinstance(ab, dict):
                    continue
                ab_name = ab.get("name") or ab.get("abilityName") or ""
                if not ab_name:
                    continue
                actions: list[str] = []
                entities: list[str] = []
                skills = ab.get("skills") or []
                if isinstance(skills, list):
                    for sk in skills:
                        if isinstance(sk, dict):
                            acts = sk.get("actions") or []
                            if isinstance(acts, list):
                                actions.extend(str(a) for a in acts)
                            elif isinstance(acts, str):
                                actions.append(acts)
                            ents = sk.get("entities") or []
                            if isinstance(ents, list):
                                entities.extend(str(e) for e in ents)
                            elif isinstance(ents, str):
                                entities.append(ents)
                is_main = (ab_name == main_elem) or bool(ab.get("isLauncherAbility"))
                results.append(
                    {
                        "bundle_name": bundle_name,
                        "ability_name": ab_name,
                        "actions": actions,
                        "entities": entities,
                        "is_main": is_main,
                    }
                )

        if results:
            return results
    except Exception:
        pass

    # 2. Stateful text parsing
    results = []
    curr_bundle: Optional[str] = None
    curr_ability: Optional[str] = None
    curr_actions: list[str] = []
    curr_entities: list[str] = []
    curr_main: bool = False

    def flush():
        nonlocal curr_bundle, curr_ability, curr_actions, curr_entities, curr_main
        if curr_bundle and curr_ability:
            results.append(
                {
                    "bundle_name": curr_bundle,
                    "ability_name": curr_ability,
                    "actions": list(curr_actions),
                    "entities": list(curr_entities),
                    "is_main": curr_main,
                }
            )
        curr_ability = None
        curr_actions = []
        curr_entities = []
        curr_main = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Check bundleName pattern
        m_b = re.search(r"bundleName\s*[:=]\s*([a-zA-Z0-9._-]+)", line, re.I)
        if m_b:
            flush()
            curr_bundle = m_b.group(1).strip()
            continue

        # Check bracketed bundle name like `app name [com.ohos.sceneboard]`
        m_b_bracket = re.search(r"(?:app name|bundle)\s*\[([a-zA-Z0-9._-]+)\]", line, re.I)
        if m_b_bracket:
            flush()
            curr_bundle = m_b_bracket.group(1).strip()
            continue

        # Check top-level bundle line like `com.ohos.sceneboard:`
        if (line.startswith("com.") or line.startswith("ohos.")) and line.endswith(":"):
            pkg = line[:-1].strip()
            if "/" not in pkg and " " not in pkg:
                flush()
                curr_bundle = pkg
                continue

        # Check mainAbility / mainElement
        m_m = re.search(
            r"(?:mainAbility|mainElement)\s*[:=]\s*([a-zA-Z0-9._-]+)", line, re.I
        )
        if m_m:
            main_name = m_m.group(1).strip()
            if not curr_ability:
                curr_ability = main_name
                curr_main = True
            elif curr_ability == main_name:
                curr_main = True

        m_m_bracket = re.search(r"main ability\s*\[([a-zA-Z0-9._-]+)\]", line, re.I)
        if m_m_bracket:
            main_name = m_m_bracket.group(1).strip()
            if not curr_ability:
                curr_ability = main_name
                curr_main = True
            elif curr_ability == main_name:
                curr_main = True

        # Check ability name
        m_a = re.search(
            r"(?:abilityName|ability|name)\s*[:=]\s*([a-zA-Z0-9._-]+)", line, re.I
        )
        if m_a and not line.lower().startswith("bundlename"):
            cand_name = m_a.group(1).strip()
            lower_cand = cand_name.lower()
            if (
                lower_cand not in ("skills", "actions", "entities", "action", "entity")
                and not lower_cand.startswith("action.")
                and not lower_cand.startswith("entity.")
            ):
                if curr_ability and curr_ability != cand_name:
                    flush()
                curr_ability = cand_name

        # Check actions
        for act in (
            "action.system.home",
            "action.ohos.home",
            "ohos.want.action.home",
            "action.system.entry",
            "action.ohos.entry",
        ):
            if act in line and act not in curr_actions:
                curr_actions.append(act)

        # Check entities
        for ent in ("entity.system.home", "entity.system.browsable"):
            if ent in line and ent not in curr_entities:
                curr_entities.append(ent)

    flush()
    return results


def get_default_launcher_component(
    device_serial: Optional[str],
) -> Optional[str]:
    """
    Resolve the default desktop launcher component (e.g. 'com.ohos.sceneboard/MainAbility').

    Queries `hdc shell bm dump -a` or `bm dump -s` to find an ability registered with
    action 'action.system.home' / entity 'entity.system.home'. Falls back to known
    system launcher bundles (com.ohos.sceneboard, com.ohos.launcher, com.huawei.hmos.launcher).

    Args:
        device_serial: Device serial identifier.

    Returns:
        String 'bundle_name/ability_name' or None if unresolved.
    """
    # 1. Query bm dump -a
    cmd = hdc_prefix(device_serial) + ["shell", "bm", "dump", "-a"]
    proc = HdcCommandRunner.run(
        cmd, check=False, delay_s=0.0, log_output=False, timeout_s=10.0
    )
    abilities = _parse_bm_dump_abilities(proc.stdout or "")

    # 2. Fallback to bm dump -s if needed
    if not abilities:
        cmd_s = hdc_prefix(device_serial) + ["shell", "bm", "dump", "-s"]
        proc_s = HdcCommandRunner.run(
            cmd_s, check=False, delay_s=0.0, log_output=False, timeout_s=8.0
        )
        abilities = _parse_bm_dump_abilities(proc_s.stdout or "")

    # 3. Match home launcher action / entity
    for ab in abilities:
        acts = set(ab.get("actions", []))
        ents = set(ab.get("entities", []))
        if (acts & _HOME_ACTIONS) or (ents & _HOME_ENTITIES):
            return f"{ab['bundle_name']}/{ab['ability_name']}"

    # 4. Fallback to known default launchers
    installed = list_installed_packages(device_serial, include_system=True)
    for known in _KNOWN_LAUNCHER_BUNDLES:
        if known in installed:
            matching = [ab for ab in abilities if ab.get("bundle_name") == known]
            if matching:
                return f"{known}/{matching[0]['ability_name']}"

            # Query single bundle
            cmd_n = hdc_prefix(device_serial) + ["shell", "bm", "dump", "-n", known]
            proc_n = HdcCommandRunner.run(
                cmd_n, check=False, delay_s=0.0, log_output=False, timeout_s=5.0
            )
            n_abs = _parse_bm_dump_abilities(proc_n.stdout or "")
            if n_abs:
                return f"{known}/{n_abs[0]['ability_name']}"
            return f"{known}/MainAbility"

    return None


def list_all_launcher_components(
    device_serial: Optional[str],
) -> dict[str, list[str]]:
    """
    Query all abilities that serve as entry points or desktop launcher entry points.

    Returns:
        Dict mapping bundle_name to list of 'bundle_name/ability_name' components.
    """
    cmd = hdc_prefix(device_serial) + ["shell", "bm", "dump", "-a"]
    proc = HdcCommandRunner.run(
        cmd, check=False, delay_s=0.0, log_output=False, timeout_s=10.0
    )
    abilities = _parse_bm_dump_abilities(proc.stdout or "")

    comps: dict[str, list[str]] = {}
    for ab in abilities:
        b_name = ab.get("bundle_name") or ""
        a_name = ab.get("ability_name") or ""
        if not b_name or not a_name:
            continue
        acts = set(ab.get("actions", []))
        ents = set(ab.get("entities", []))
        is_main = ab.get("is_main", False)

        if (acts & _ENTRY_ACTIONS) or (ents & _ENTRY_ENTITIES) or is_main:
            comps.setdefault(b_name, []).append(f"{b_name}/{a_name}")

    # Fallback: if a bundle was dumped but has no matched entry points, add its first ability
    all_bundles = {ab["bundle_name"] for ab in abilities if ab.get("bundle_name")}
    for b_name in all_bundles:
        if b_name not in comps:
            first = next(
                (ab for ab in abilities if ab.get("bundle_name") == b_name and ab.get("ability_name")),
                None,
            )
            if first:
                comps.setdefault(b_name, []).append(f"{b_name}/{first['ability_name']}")

    if not comps:
        default_comp = get_default_launcher_component(device_serial)
        if default_comp and "/" in default_comp:
            pkg = default_comp.split("/", 1)[0]
            comps[pkg] = [default_comp]
        try:
            installed = list_installed_packages(device_serial, include_system=False)
            for pkg in installed:
                if pkg and pkg not in comps:
                    comps[pkg] = [f"{pkg}/EntryAbility"]
        except Exception as exc:
            logger.debug("Failed to list installed packages for fallback: %s", exc)

    return {k: sorted(set(v)) for k, v in comps.items()}


def build_launch_profiles_snapshot(
    device_serial: Optional[str],
) -> list[dict[str, str]]:
    """
    Build launch profiles list from launcher components.

    Returns:
        List of dicts: [
            {
                "package_name": bundle,
                "package": bundle,
                "activity": ability,
                "component": f"{bundle}/{ability}",
                "adb_cmd": f"hdc -t {device_serial} shell aa start -b {bundle} -a {ability}",
                "source": "bm_dump",
            }, ...
        ]
    """
    profiles: list[dict[str, str]] = []
    comp_map = list_all_launcher_components(device_serial)
    for pkg, comps in comp_map.items():
        if not comps:
            continue
        component = comps[0]
        activity = component.split("/", 1)[1] if "/" in component else None
        target_arg = f"-t {device_serial} " if device_serial else ""
        if activity:
            adb_cmd = f"hdc {target_arg}shell aa start -b {pkg} -a {activity}"
        else:
            adb_cmd = f"hdc {target_arg}shell aa start -b {pkg}"

        profiles.append(
            {
                "package_name": pkg,
                "package": pkg,
                "activity": activity,
                "component": component,
                "adb_cmd": adb_cmd,
                "source": "bm_dump",
            }
        )
    return profiles


def launch_from_home(
    device_serial: Optional[str],
    bundle_name: str,
    ability_name: Optional[str] = None,
) -> dict:
    """
    Cold launch an application from home screen via HDC.

    Sends HOME keyevent (1), then launches the app via `launch_app`.

    Args:
        device_serial: Device serial identifier.
        bundle_name: Application bundle name.
        ability_name: Optional ability name to start.

    Returns:
        Dict: {"ok": True, "bundle": bundle_name, "ability": ability_name} or
              {"ok": False, "error": str, ...}
    """
    # 1. Send HOME keyevent
    input_keyevent(device_serial, "1")

    # 2. Launch the app
    launch_res = launch_app(device_serial, bundle_name, activity=ability_name)
    if not launch_res.get("ok"):
        return {
            "ok": False,
            "bundle": bundle_name,
            "ability": ability_name,
            "error": launch_res.get("stderr") or "failed_to_launch",
        }

    # 3. Return success
    return {
        "ok": True,
        "bundle": bundle_name,
        "ability": ability_name,
    }


def set_wifi_enabled(
    device_serial: Optional[str] = None, enabled: bool = True
) -> dict:
    """开关 WiFi (HarmonyOS)。
    Toggle WiFi on/off on HarmonyOS.

    Args:
        device_serial: 设备序列号 / Device serial
        enabled: True 开启 / False 关闭 / True to enable, False to disable

    Returns:
        dict: {"ok": True, "wifi_on": bool} 或 {"ok": False, "error": str, "wifi_on": bool}
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required", "wifi_on": enabled}

    try:
        flag = "-e" if enabled else "-d"
        cmd = hdc_prefix(device_serial) + ["shell", "wifi_tool", flag]
        proc = HdcCommandRunner.run(cmd, check=False, log_output=False)
        if proc.returncode == 0:
            return {"ok": True, "wifi_on": enabled}

        # Fallback 1: param set persist.wifi.state on/off
        state_str = "on" if enabled else "off"
        cmd_param = hdc_prefix(device_serial) + [
            "shell",
            "param",
            "set",
            "persist.wifi.state",
            state_str,
        ]
        proc_param = HdcCommandRunner.run(cmd_param, check=False, log_output=False)

        # Fallback 2: settings put global wifi_on 1/0
        val_str = "1" if enabled else "0"
        cmd_settings = hdc_prefix(device_serial) + [
            "shell",
            "settings",
            "put",
            "global",
            "wifi_on",
            val_str,
        ]
        proc_settings = HdcCommandRunner.run(cmd_settings, check=False, log_output=False)

        if proc_param.returncode == 0 or proc_settings.returncode == 0:
            return {"ok": True, "wifi_on": enabled}

        err = (
            proc.stderr
            or proc_param.stderr
            or proc_settings.stderr
            or "failed to toggle wifi"
        )
        return {
            "ok": False,
            "error": f"wifi_tool and fallback failed: {err.strip()}",
            "wifi_on": enabled,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "wifi_on": enabled}


def set_airplane_mode(
    device_serial: Optional[str] = None, enabled: bool = True
) -> dict:
    """开关飞行模式 (HarmonyOS)。
    Toggle airplane mode on HarmonyOS.

    Args:
        device_serial: 设备序列号 / Device serial
        enabled: True 开启 / False 关闭 / True to enable, False to disable

    Returns:
        dict: {"ok": True, "airplane_on": bool} 或 {"ok": False, "error": str, "airplane_on": bool}
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required", "airplane_on": enabled}

    try:
        val = "1" if enabled else "0"
        cmd_param = hdc_prefix(device_serial) + [
            "shell",
            "param",
            "set",
            "persist.radio.airplane_mode_on",
            val,
        ]
        proc_param = HdcCommandRunner.run(cmd_param, check=False, log_output=False)

        cmd_settings = hdc_prefix(device_serial) + [
            "shell",
            "settings",
            "put",
            "global",
            "airplane_mode_on",
            val,
        ]
        proc_settings = HdcCommandRunner.run(cmd_settings, check=False, log_output=False)

        if proc_param.returncode != 0 and proc_settings.returncode != 0:
            err = (
                proc_settings.stderr
                or proc_param.stderr
                or "failed to set airplane mode"
            )
            return {
                "ok": False,
                "error": f"failed to set airplane mode: {err.strip()}",
                "airplane_on": enabled,
            }

        # Broadcast CES event for airplane mode change
        cmd_ces = hdc_prefix(device_serial) + [
            "shell",
            "ces",
            "pub",
            "-e",
            "usual.event.AIRPLANE_MODE_CHANGED",
        ]
        HdcCommandRunner.run(cmd_ces, check=False, log_output=False)

        return {"ok": True, "airplane_on": enabled}
    except Exception as e:
        return {"ok": False, "error": str(e), "airplane_on": enabled}


def _parse_harmony_notifications(raw_text: str) -> list[dict[str, str]]:
    """Parse notification records from HarmonyOS notification dump text."""
    if not raw_text or not raw_text.strip():
        return []

    # 1. Try JSON parsing
    stripped = raw_text.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        try:
            data = json.loads(stripped)
            found: list[dict[str, str]] = []

            def _extract_from_obj(obj: Any):
                if isinstance(obj, dict):
                    pkg = (
                        obj.get("bundleName")
                        or obj.get("bundle_name")
                        or obj.get("package")
                        or obj.get("pkg")
                    )
                    if pkg and isinstance(pkg, str):
                        title = obj.get("title") or obj.get("contentTitle") or ""
                        text = (
                            obj.get("text")
                            or obj.get("contentText")
                            or obj.get("content")
                            or ""
                        )
                        if not title and isinstance(obj.get("content"), dict):
                            c = obj["content"]
                            title = c.get("title") or ""
                            text = c.get("text") or ""
                        if not title and isinstance(obj.get("notification"), dict):
                            n = obj["notification"]
                            if isinstance(n.get("content"), dict):
                                title = n["content"].get("title") or ""
                                text = n["content"].get("text") or ""
                        found.append(
                            {
                                "package": str(pkg).strip(),
                                "title": str(title).strip(),
                                "text": str(text).strip(),
                            }
                        )
                    for v in obj.values():
                        _extract_from_obj(v)
                elif isinstance(obj, list):
                    for item in obj:
                        _extract_from_obj(item)

            _extract_from_obj(data)
            if found:
                return found
        except Exception:
            pass

    # 2. Text parsing
    notifications: list[dict[str, str]] = []

    if re.search(r"NotificationRecord|NotificationRequest", raw_text, re.IGNORECASE):
        blocks = re.split(
            r"\n\s*(?:NotificationRecord|NotificationRequest)\b",
            raw_text,
            flags=re.IGNORECASE,
        )
    elif re.search(r"\n\s*-{5,}\s*\n", raw_text):
        blocks = re.split(r"\n\s*-{5,}\s*\n", raw_text)
    elif "bundleName" in raw_text or "bundle_name" in raw_text:
        blocks = re.split(
            r"(?=(?:\n|^)\s*(?:bundleName|bundle_name)\s*[:=])",
            raw_text,
            flags=re.IGNORECASE,
        )
    else:
        blocks = [raw_text]

    for block in blocks:
        pkg = ""
        title = ""
        text = ""
        for line in block.splitlines():
            line_str = line.strip()
            if not line_str:
                continue

            # package / bundleName
            if not pkg:
                m_pkg = re.search(
                    r'(?:bundleName|bundle_name|package|pkg)\s*[:=]\s*["\']?([^"\'\s,\]}]+)',
                    line_str,
                    re.IGNORECASE,
                )
                if m_pkg:
                    pkg = m_pkg.group(1).strip()
                elif '"bundleName"' in line_str or '"bundle_name"' in line_str:
                    m_json_pkg = re.search(
                        r'"(?:bundleName|bundle_name)"\s*:\s*"([^"]+)"', line_str
                    )
                    if m_json_pkg:
                        pkg = m_json_pkg.group(1).strip()

            # title
            if not title:
                m_title = re.search(
                    r'(?:contentTitle|title)\s*[:=]\s*["\']?([^"\'\r\n]+?)(?:["\']?\s*$|,\s*\w+[:=])',
                    line_str,
                    re.IGNORECASE,
                )
                if m_title:
                    title = m_title.group(1).strip()
                elif '"title"' in line_str or '"contentTitle"' in line_str:
                    m_json_t = re.search(
                        r'"(?:title|contentTitle)"\s*:\s*"([^"]+)"', line_str
                    )
                    if m_json_t:
                        title = m_json_t.group(1).strip()

            # text / content
            if not text:
                m_text = re.search(
                    r'(?:contentText|text|content)\s*[:=]\s*["\']?([^"\'\r\n]+?)(?:["\']?\s*$|,\s*\w+[:=])',
                    line_str,
                    re.IGNORECASE,
                )
                if m_text:
                    val = m_text.group(1).strip()
                    if not val.startswith("{") and not val.startswith("["):
                        text = val
                elif '"text"' in line_str or '"contentText"' in line_str:
                    m_json_txt = re.search(
                        r'"(?:text|contentText)"\s*:\s*"([^"]+)"', line_str
                    )
                    if m_json_txt:
                        text = m_json_txt.group(1).strip()

        if pkg:
            notifications.append(
                {
                    "package": pkg,
                    "title": title,
                    "text": text,
                }
            )

    return notifications


def get_notifications(device_serial: Optional[str] = None) -> dict:
    """获取通知栏通知列表 (HarmonyOS)。
    Get notification list from notification bar on HarmonyOS.

    Args:
        device_serial: 设备序列号 / Device serial

    Returns:
        dict: {"ok": True, "notifications": [{"package": str, "title": str, "text": str}]}
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required", "notifications": []}

    services = [
        ["hidumper", "-s", "NotificationManagerService", "-a", "-a"],
        ["hidumper", "-s", "AdvancedNotificationService", "-a", "-a"],
        ["hidumper", "-s", "NotificationService", "-a", "-a"],
        ["hidumper", "-s", "NotificationManagerService"],
    ]

    last_err = ""
    stdout = ""
    success = False

    for svc_cmd in services:
        try:
            cmd = hdc_prefix(device_serial) + ["shell"] + svc_cmd
            proc = HdcCommandRunner.run(cmd, check=False, log_output=False)
            if proc.returncode == 0 and proc.stdout and proc.stdout.strip():
                stdout = proc.stdout
                success = True
                break
            else:
                last_err = proc.stderr or proc.stdout or last_err
        except Exception as exc:
            last_err = str(exc)

    if not success and not stdout:
        return {
            "ok": False,
            "error": f"hidumper notification dump failed: {last_err.strip() or 'service not responding'}",
            "notifications": [],
        }

    notifications = _parse_harmony_notifications(stdout)
    return {"ok": True, "notifications": notifications}


def parse_harmony_meminfo(
    raw_output: str, package_name: Optional[str] = None
) -> dict:
    """解析 hidumper --mem 输出为标准结构。
    Parse hidumper --mem output into structured dictionary.

    Args:
        raw_output: hidumper --mem 命令的文本输出 / Command output
        package_name: 可选包名过滤 / Optional package name to filter

    Returns:
        dict: {"summary": {...}, "details": {...}, "app_summary": {...}}
    """
    result: Dict[str, Any] = {
        "summary": {},
        "details": {},
        "app_summary": {},
    }

    if not raw_output or not raw_output.strip():
        result["summary"] = {
            "total_pss_kb": 0,
            "total_pss_mb": 0.0,
            "total_rss_kb": 0,
            "total_vss_kb": 0,
            "total_uss_kb": 0,
            "native_heap_kb": 0,
            "native_heap_mb": 0.0,
            "ark_ts_heap_kb": 0,
            "ark_ts_heap_mb": 0.0,
            "dalvik_heap_kb": 0,
            "dalvik_heap_mb": 0.0,
            "java_heap_kb": 0,
            "java_heap_mb": 0.0,
            "graphics_kb": 0,
            "graphics_mb": 0.0,
            "gl_kb": 0,
            "graph_kb": 0,
        }
        return result

    lines = raw_output.strip().splitlines()

    total_pss_kb = 0
    total_rss_kb = 0
    total_vss_kb = 0
    total_uss_kb = 0
    gl_kb = 0
    graph_kb = 0
    ark_ts_heap_kb = 0
    native_heap_kb = 0

    in_details_table = False
    in_process_table = False
    process_cols: list[str] = []

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        # Detect Details table header
        if ("Pss" in line and "Shared" in line and ("Private" in line or "Dirty" in line)) or (
            line_str.lower().startswith("details:") or line_str.lower() == "details"
        ):
            in_details_table = True
            in_process_table = False
            continue

        # Detect Process / Summary table header
        if re.search(r"\b(?:PID|Pid)\b", line) and re.search(
            r"\b(?:Pss|Total Pss)\b", line, re.IGNORECASE
        ):
            in_process_table = True
            in_details_table = False
            hdr_norm = re.sub(r"\(.*?\)", "", line)
            for term in [
                "Total Pss",
                "Total Vss",
                "Total Rss",
                "Total Uss",
                "Pss Total",
                "Total SwapPss",
                "Swap Pss",
            ]:
                hdr_norm = re.sub(
                    re.escape(term), term.replace(" ", "_"), hdr_norm, flags=re.IGNORECASE
                )
            process_cols = [c.lower() for c in hdr_norm.split()]
            continue

        if in_details_table:
            if line_str.startswith("---") or line_str.startswith("("):
                continue
            # End of details table
            m_tot = re.search(r"^(?:Total|TOTAL)[:\s]+(\d+)", line_str)
            if m_tot:
                if total_pss_kb == 0:
                    total_pss_kb = int(m_tot.group(1))
                in_details_table = False
                continue

            # Skip header continuation lines like 'Total   Clean   Dirty...'
            if re.search(r"^\s*Total\s+Clean\s+Dirty", line_str, re.IGNORECASE):
                continue

            # Parse category row
            m = re.match(r"^\s*([A-Za-z0-9_\.\s\-]+?)\s{2,}(\d+(?:\s+\d+)*)\s*$", line)
            if not m:
                m = re.match(
                    r"^\s*([A-Za-z][A-Za-z0-9_\.\s\-]+?)\s+(\d+(?:\s+\d+)+)\s*$", line
                )
            if not m:
                m = re.match(
                    r"^\s*([A-Za-z0-9_\.\s\-]+?)\s*[:=]\s*(\d+)(?:\s*kB)?\s*$",
                    line,
                    re.IGNORECASE,
                )
            if m:
                cat = m.group(1).strip()
                nums = [int(x) for x in m.group(2).split()]
                if nums:
                    pss_val = nums[0]
                    det_entry: Dict[str, Any] = {
                        "pss": pss_val,
                        "pss_total_kb": pss_val,
                    }
                    if len(nums) > 1:
                        det_entry["shared_clean_kb"] = nums[1]
                    if len(nums) > 2:
                        det_entry["shared_dirty_kb"] = nums[2]
                    if len(nums) > 3:
                        det_entry["private_clean_kb"] = nums[3]
                    if len(nums) > 4:
                        det_entry["private_dirty_kb"] = nums[4]
                    if len(nums) > 5:
                        det_entry["swap_total_kb"] = nums[5]
                    if len(nums) > 6:
                        det_entry["swap_pss_kb"] = nums[6]
                    if len(nums) > 7:
                        det_entry["heap_size_kb"] = nums[7]
                    if len(nums) > 8:
                        det_entry["heap_alloc_kb"] = nums[8]
                    if len(nums) > 9:
                        det_entry["heap_free_kb"] = nums[9]

                    result["details"][cat] = det_entry
                    if cat.lower() in ("ark ts heap", "arkts heap", "ark ts", "arkts"):
                        ark_ts_heap_kb = pss_val
                    elif cat.lower() in ("native heap", "native"):
                        native_heap_kb = pss_val
            continue

        if in_process_table:
            if line_str.startswith("---"):
                continue
            # Data row starts with PID digits
            if re.match(r"^\d+", line_str):
                row_text = re.sub(r"\([^\)]*\)", "", line_str)
                row_text = re.sub(r"\bkB\b", "", row_text, flags=re.IGNORECASE)
                tokens = row_text.split()
                if len(tokens) >= 2:
                    if package_name and len(tokens) > len(process_cols) - 1:
                        if package_name not in tokens[-1]:
                            continue
                    for i, tok in enumerate(tokens):
                        if not tok.isdigit():
                            continue
                        val = int(tok)
                        hdr = process_cols[i] if i < len(process_cols) else ""
                        if "pid" in hdr:
                            result["pid"] = val
                        elif "pss" in hdr:
                            total_pss_kb = val
                        elif "vss" in hdr:
                            total_vss_kb = val
                        elif "rss" in hdr:
                            total_rss_kb = val
                        elif "uss" in hdr:
                            total_uss_kb = val
                        elif "gl" in hdr:
                            gl_kb = val
                        elif "graph" in hdr:
                            graph_kb = val
                    if len(tokens) > 0 and not tokens[-1].isdigit():
                        result["name"] = tokens[-1]
            else:
                in_process_table = False

        # Key-Value lines
        m_kv = re.match(
            r"^\s*([A-Za-z0-9_\.\s\-\(\)]+?)\s*[:=]\s*(\d+)(?:\s*kB)?\s*$",
            line,
            re.IGNORECASE,
        )
        if m_kv:
            k = m_kv.group(1).strip().lower()
            v = int(m_kv.group(2))
            if "total pss" in k or k in ("pss", "pss(kb)", "total pss(kb)"):
                if total_pss_kb == 0 or "total" in k:
                    total_pss_kb = v
            elif "total vss" in k or k in ("vss", "vss(kb)", "total vss(kb)"):
                if total_vss_kb == 0 or "total" in k:
                    total_vss_kb = v
            elif "total rss" in k or k in ("rss", "rss(kb)", "total rss(kb)"):
                if total_rss_kb == 0 or "total" in k:
                    total_rss_kb = v
            elif "total uss" in k or k in ("uss", "uss(kb)", "total uss(kb)"):
                if total_uss_kb == 0 or "total" in k:
                    total_uss_kb = v
            elif "gl" in k:
                gl_kb = v
            elif "graph" in k:
                graph_kb = v
            elif "ark" in k and "heap" in k:
                ark_ts_heap_kb = v
            elif "native" in k and "heap" in k:
                native_heap_kb = v

    if total_pss_kb == 0 and result["details"]:
        total_pss_kb = sum(d.get("pss", 0) for d in result["details"].values())

    graphics_kb = gl_kb + graph_kb

    summary = {
        "total_pss_kb": total_pss_kb,
        "total_pss_mb": round(total_pss_kb / 1024, 2),
        "total_rss_kb": total_rss_kb,
        "total_vss_kb": total_vss_kb,
        "total_uss_kb": total_uss_kb,
        "native_heap_kb": native_heap_kb,
        "native_heap_mb": round(native_heap_kb / 1024, 2),
        "ark_ts_heap_kb": ark_ts_heap_kb,
        "ark_ts_heap_mb": round(ark_ts_heap_kb / 1024, 2),
        "dalvik_heap_kb": ark_ts_heap_kb,
        "dalvik_heap_mb": round(ark_ts_heap_kb / 1024, 2),
        "java_heap_kb": ark_ts_heap_kb,
        "java_heap_mb": round(ark_ts_heap_kb / 1024, 2),
        "graphics_kb": graphics_kb,
        "graphics_mb": round(graphics_kb / 1024, 2),
        "gl_kb": gl_kb,
        "graph_kb": graph_kb,
    }
    result["summary"] = summary

    # app_summary
    app_summary: Dict[str, int] = {
        "ArkTS Heap": ark_ts_heap_kb,
        "Java Heap": ark_ts_heap_kb,
        "Native Heap": native_heap_kb,
        "Graphics": graphics_kb,
        "GL": gl_kb,
        "Graph": graph_kb,
        "Total Pss": total_pss_kb,
        "TOTAL PSS": total_pss_kb,
    }
    for cat, d in result["details"].items():
        app_summary[cat] = d.get("pss", 0)
    result["app_summary"] = app_summary

    return result


def capture_meminfo(
    device_serial: Optional[str],
    package_name: str,
    *,
    timeout_s: float = 30.0,
) -> dict:
    """使用 hidumper --mem 捕获 HarmonyOS 应用内存快照。
    Capture memory info using HDC shell hidumper --mem <package_name>.

    Args:
        device_serial: 设备序列号 / Device serial
        package_name: 应用包名 (bundleName) / Application package/bundle name
        timeout_s: 超时时间（秒）/ Timeout in seconds

    Returns:
        dict: 解析后的内存信息 / Parsed memory information dict
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required", "platform": "harmony"}

    if not package_name or not package_name.strip():
        return {"ok": False, "error": "package_name is required", "platform": "harmony"}

    pkg = package_name.strip()
    try:
        # First attempt: direct hidumper --mem <pkg>
        cmd = hdc_prefix(device_serial) + ["shell", "hidumper", "--mem", pkg]
        proc = HdcCommandRunner.run(
            cmd, check=False, timeout_s=timeout_s, log_output=False
        )

        stdout = proc.stdout or ""
        # If output looks like a valid hidumper memory dump (contains Total or Pss)
        if proc.returncode == 0 and ("Total" in stdout or "Pss" in stdout):
            parsed = parse_harmony_meminfo(stdout, package_name=pkg)
            parsed["ok"] = True
            parsed["package"] = pkg
            parsed["platform"] = "harmony"
            parsed["raw_output"] = stdout
            parsed["captured_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            return parsed

        # If direct package lookup failed or didn't contain memory table, attempt PID resolution
        target_pid = None
        if not pkg.isdigit():
            pid_proc = HdcCommandRunner.run(
                hdc_prefix(device_serial) + ["shell", "pidof", pkg],
                check=False,
                timeout_s=5.0,
                log_output=False,
            )
            pid_out = (pid_proc.stdout or "").strip()
            if pid_out:
                target_pid = pid_out.split()[0]
            else:
                ps_proc = HdcCommandRunner.run(
                    hdc_prefix(device_serial) + ["shell", "ps -ef"],
                    check=False,
                    timeout_s=5.0,
                    log_output=False,
                )
                for line in (ps_proc.stdout or "").splitlines():
                    parts = line.split()
                    if len(parts) >= 8 and (parts[-1] == pkg or parts[-1].startswith(pkg + ":")):
                        target_pid = parts[1]
                        break

        if target_pid and target_pid.isdigit():
            cmd_pid = hdc_prefix(device_serial) + ["shell", "hidumper", "--mem", target_pid]
            proc_pid = HdcCommandRunner.run(
                cmd_pid, check=False, timeout_s=timeout_s, log_output=False
            )
            if proc_pid.returncode == 0:
                stdout_pid = proc_pid.stdout or ""
                parsed = parse_harmony_meminfo(stdout_pid, package_name=pkg)
                parsed["ok"] = True
                parsed["package"] = pkg
                parsed["pid"] = int(target_pid)
                parsed["platform"] = "harmony"
                parsed["raw_output"] = stdout_pid
                parsed["captured_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                return parsed

        # If both attempts failed or initial returned non-zero
        err_msg = (proc.stderr or proc.stdout or "").strip()
        return {
            "ok": False,
            "error": f"hidumper --mem failed: {err_msg}" if err_msg else "hidumper --mem failed",
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "").strip(),
            "package": pkg,
            "platform": "harmony",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "package": pkg,
            "platform": "harmony",
        }


def get_param(device_serial: Optional[str], param_name: str) -> str:
    """获取系统参数 (param get)。"""
    cmd = hdc_prefix(device_serial) + ["shell", "param", "get", param_name]
    proc = HdcCommandRunner.run(cmd, check=False, log_output=False)
    out = (proc.stdout or "").strip()
    if out.startswith("Get parameter") or "fail" in out or "not found" in out:
        return ""
    return out


def get_device_model_and_version(device_serial: Optional[str] = None) -> tuple[str, str]:
    """获取鸿蒙设备型号与系统版本。"""
    model = (
        get_param(device_serial, "const.product.model")
        or get_param(device_serial, "const.product.name")
        or ""
    )
    version = (
        get_param(device_serial, "const.product.software.version")
        or get_param(device_serial, "const.ohos.version.release")
        or ""
    )
    return (model, version)


def get_device_info(device_serial: Optional[str] = None) -> dict:
    """通过系统参数与 HDC 获取鸿蒙设备画像 (HDC 降级)。"""
    try:
        model = get_param(device_serial, "const.product.model") or ""
        product_name = get_param(device_serial, "const.product.name") or ""
        software_version = get_param(device_serial, "const.product.software.version") or ""
        sdk_version = get_param(device_serial, "const.ohos.apiversion") or ""
        cpu_abi = get_param(device_serial, "const.product.cpu.abilist") or "arm64-v8a"

        # Try getting display size via screen driver
        from phone_pilot.harmony.screen.driver import HarmonyScreenDriver
        try:
            w, h = HarmonyScreenDriver(device_serial or "").get_screen_size()
            display_size = [w, h]
        except Exception:
            display_size = [0, 0]

        return {
            "ok": True,
            "productName": product_name or model,
            "model": model,
            "sdkVersion": sdk_version,
            "sysVersion": software_version,
            "cpuAbi": cpu_abi,
            "wlanIp": "",
            "displaySize": display_size,
            "displayRotation": "DisplayRotation.ROTATION_0",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def lock_screen(device_serial: Optional[str]) -> dict:
    """Lock/suspend the device screen."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    cmd = hdc_prefix(device_serial) + ["shell", "power-shell", "suspend"]
    proc = HdcCommandRunner.run(cmd, check=False, timeout_s=5.0, log_output=False)
    return {"ok": proc.returncode == 0, "engine": "hdc_cli", "error": (proc.stderr or "").strip()}


def unlock_screen(device_serial: Optional[str], pin: Optional[str] = None) -> dict:
    """Wakeup system and unlock screen using power-shell and swipe."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    # 1. Wakeup
    cmd_wake = hdc_prefix(device_serial) + ["shell", "power-shell", "wakeup"]
    HdcCommandRunner.run(cmd_wake, check=False, timeout_s=5.0, log_output=False)

    # 2. Get screen size
    w, h = get_screen_size(device_serial)
    if not w or not h:
        w, h = 1260, 2844

    # 3. Swipe up from 75% to 25% height
    x = w // 2
    y_start = int(h * 0.75)
    y_end = int(h * 0.25)
    cmd_swipe = hdc_prefix(device_serial) + [
        "shell",
        "uinput",
        "-T",
        "-m",
        str(x),
        str(y_start),
        str(x),
        str(y_end),
    ]
    proc = HdcCommandRunner.run(cmd_swipe, check=False, timeout_s=5.0, log_output=False)

    # 4. Optional PIN input
    if pin:
        input_text(device_serial, pin)
        input_keyevent(device_serial, "KEY_ENTER")

    return {"ok": proc.returncode == 0, "engine": "hdc_cli"}



