#!/usr/bin/env python3
"""
Device lock/unlock helpers.
"""

from __future__ import annotations

import subprocess
import time
from typing import Optional

from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner
from phone_pilot.android.device.utils import input_keyevent, input_swipe, get_screen_size


def get_display_state(device_serial: Optional[str]) -> dict:
    """
    Best-effort detect whether the display is interactive/on.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    cmd = adb_prefix(device_serial) + ["shell", "dumpsys", "power"]
    timed_out = False
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=2.0, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc = None

    raw = (proc.stdout or "").strip() if proc is not None else ""
    lines = []
    for line in raw.splitlines():
        if any(tok in line for tok in ("mWakefulness=", "mInteractive=", "mHoldingDisplaySuspendBlocker", "Display Power: state=")):
            lines.append(line.strip())
        if len(lines) >= 20:
            break
    raw = "\n".join(lines)

    txt = raw.lower()
    screen_on: bool | None = None
    holding_display: bool | None = None

    if "mholdingdisplaysuspendblocker=true" in txt:
        holding_display = True
    elif "mholdingdisplaysuspendblocker=false" in txt:
        holding_display = False

    if "minteractive=true" in txt:
        screen_on = True
    elif "minteractive=false" in txt:
        screen_on = False
    elif "mwakefulness=awake" in txt:
        screen_on = True
    elif "mwakefulness=asleep" in txt:
        screen_on = False

    if holding_display is False and screen_on is True:
        screen_on = False

    return {
        "ok": True,
        "device_serial": device_serial,
        "method": "dumpsys_power",
        "raw": raw,
        "timed_out": timed_out,
        "timeout_s": 2.0,
        "screen_on": screen_on,
        "holding_display_suspend": holding_display,
    }


def get_device_state(device_serial: Optional[str]) -> dict:
    """
    Device state summary using the lock_status.sh logic.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    # 1) screen state (Awake + holding display suspend blocker)
    power_proc = CommandRunner.run(adb_prefix(device_serial) + ["shell", "dumpsys", "power"], check=False, log_output=False, silent=True)
    power_text = power_proc.stdout or ""
    power_lines = []
    for line in power_text.splitlines():
        if ("mWakefulness=" in line) or ("mHoldingDisplaySuspendBlocker" in line):
            power_lines.append(line.strip())
        if len(power_lines) >= 20:
            break
    power_text = "\n".join(power_lines)
    screen_on = ("Awake" in power_text) and ("mHoldingDisplaySuspendBlocker=true" in power_text)

    # 2) policy state (mShowingLockscreen / isStatusBarKeyguard)
    policy_proc = CommandRunner.run(adb_prefix(device_serial) + ["shell", "dumpsys", "window", "policy"], check=False, log_output=False, silent=True)
    policy_text = policy_proc.stdout or ""
    showing_lock = ""
    status_bar_keyguard = ""
    for line in policy_text.splitlines():
        if "mShowingLockscreen" in line and not showing_lock:
            showing_lock = line.strip()
        if "isStatusBarKeyguard" in line and not status_bar_keyguard:
            status_bar_keyguard = line.strip()
        if showing_lock and status_bar_keyguard:
            break

    # 3) focus window
    focus_proc = CommandRunner.run(adb_prefix(device_serial) + ["shell", "dumpsys", "window"], check=False, log_output=False, silent=True)
    focus_line = ""
    for line in (focus_proc.stdout or "").splitlines():
        if "mCurrentFocus" in line:
            focus_line = line.strip()
            break

    locked = None
    if screen_on:
        locked = False
        if "=true" in showing_lock or "=true" in status_bar_keyguard:
            locked = True
        if "NotificationShade" in focus_line or "keyguard" in focus_line.lower():
            locked = True

    focus_l = (focus_line or "").lower()
    unlocked_context: Optional[str] = None
    if not screen_on:
        screen_state = "screen_off"
    elif locked:
        screen_state = "locked"
    else:
        if "launcher" in focus_l:
            screen_state = "unlocked_launcher"
            unlocked_context = "launcher"
        elif focus_l:
            screen_state = "unlocked_app"
            unlocked_context = "app"
        else:
            screen_state = "unlocked"

    return {
        "ok": True,
        "device_serial": device_serial,
        "screen_on": bool(screen_on),
        "screen_state": screen_state,
        "locked": locked,
        "unlocked_context": unlocked_context,
        "showing_lock": showing_lock or None,
        "status_bar_keyguard": status_bar_keyguard or None,
        "focus_window": focus_line or None,
    }


def detect_lock_method(device_serial: Optional[str]) -> dict:
    """
    Best-effort detect lock status (simple, fast).
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    state = get_device_state(device_serial)
    if not (isinstance(state, dict) and state.get("ok")):
        return {"ok": False, "error": "device_state_failed", "device_state": state}
    if state.get("screen_on") is False:
        return {"ok": True, "device_serial": device_serial, "lock_method": "screen_off", "device_state": state}
    if state.get("locked") is True:
        return {"ok": True, "device_serial": device_serial, "lock_method": "locked_unknown", "device_state": state}
    return {"ok": True, "device_serial": device_serial, "lock_method": "none", "device_state": state}


def lock_screen(device_serial: Optional[str], *, settle_s: float = 0.6) -> dict:
    """
    Best-effort lock/sleep the device.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    actions: list[dict] = []
    r1 = input_keyevent(device_serial, "223", wait_s=0.0)  # KEYCODE_SLEEP
    actions.append({"step": "KEYCODE_SLEEP", **(r1 if isinstance(r1, dict) else {"ok": False})})
    if not (isinstance(r1, dict) and r1.get("ok")):
        r2 = input_keyevent(device_serial, "KEYCODE_POWER", wait_s=0.0)
        actions.append({"step": "KEYCODE_POWER", **(r2 if isinstance(r2, dict) else {"ok": False})})

    if settle_s and settle_s > 0:
        time.sleep(float(settle_s))

    state = get_device_state(device_serial)
    ok = bool(state.get("ok")) and (state.get("screen_on") is False or state.get("locked") is True)
    return {
        "ok": ok,
        "device_serial": device_serial,
        "actions": actions,
        "device_state": state,
        "note": "best-effort 锁屏：优先 KEYCODE_SLEEP，必要时 fallback KEYCODE_POWER。",
    }


def _input_pin_via_keyevents(device_serial: Optional[str], pin: str, *, wait_s: float = 0.08) -> dict:
    """Enter a numeric PIN using keyevents and press Enter."""
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    p = (pin or "").strip()
    if not p:
        return {"ok": False, "error": "pin is required"}
    if not p.isdigit():
        return {"ok": False, "error": "pin must be digits only", "pin": p}

    actions: list[dict] = []
    for ch in p:
        key = f"KEYCODE_{ch}"
        actions.append(input_keyevent(device_serial, key, wait_s=wait_s))
    actions.append(input_keyevent(device_serial, "KEYCODE_ENTER", wait_s=wait_s))
    ok = all(bool(a.get("ok")) for a in actions if isinstance(a, dict))
    return {"ok": ok, "pin_len": len(p), "actions": actions}


def wake_and_unlock(
    device_serial: Optional[str],
    *,
    wakeup: bool = True,
    swipe: bool = True,
    swipe_times: int = 1,
    pin: Optional[str] = None,
    swipe_duration_ms: int = 420,
    swipe_wait_s: float = 0.45,
    settle_s: float = 0.8,
) -> dict:
    """
    Best-effort wake + unlock flow.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    actions: list[dict] = []

    if wakeup:
        try:
            actions.append(input_keyevent(device_serial, "KEYCODE_WAKEUP", wait_s=0.0, silent=True))
        except Exception as e:
            actions.append({"ok": False, "error": "wakeup_failed", "exception": str(e)})

    state0 = get_device_state(device_serial)
    if isinstance(state0, dict) and state0.get("screen_state") in ("unlocked_launcher", "unlocked_app", "unlocked"):
        return {
            "ok": True,
            "device_serial": device_serial,
            "wakeup": bool(wakeup),
            "swipe": bool(swipe),
            "swipe_times": int(swipe_times) if swipe else 0,
            "pin_provided": bool((pin or "").strip()),
            "actions": actions,
            "device_state": state0,
            "note": "already_unlocked",
        }

    if swipe:
        wh = None
        try:
            wh = get_screen_size(device_serial)
        except Exception:
            wh = None
        if wh and isinstance(wh, tuple) and len(wh) == 2 and int(wh[0]) > 0 and int(wh[1]) > 0:
            w, h = int(wh[0]), int(wh[1])
        else:
            w, h = 1080, 1920
        x = int(round(w * 0.5))
        y1 = int(round(h * 0.78))
        y2 = int(round(h * 0.22))
        try:
            n_swipe = int(swipe_times)
        except Exception:
            n_swipe = 1
        n_swipe = max(1, min(n_swipe, 3))
        for _ in range(n_swipe):
            try:
                actions.append(
                    input_swipe(
                        device_serial,
                        x,
                        y1,
                        x,
                        y2,
                        duration_ms=int(swipe_duration_ms),
                        wait_s=float(swipe_wait_s),
                    )
                )
            except Exception as e:
                actions.append({"ok": False, "error": "swipe_failed", "exception": str(e)})

    state1 = get_device_state(device_serial)
    if isinstance(state1, dict) and state1.get("screen_state") in ("unlocked_launcher", "unlocked_app", "unlocked"):
        return {
            "ok": True,
            "device_serial": device_serial,
            "wakeup": bool(wakeup),
            "swipe": bool(swipe),
            "swipe_times": int(swipe_times) if swipe else 0,
            "pin_provided": bool((pin or "").strip()),
            "actions": actions,
            "device_state": state1,
            "note": "unlocked_after_swipe",
        }

    pin2 = (pin or "").strip() if pin is not None else ""
    if pin2:
        actions.append(_input_pin_via_keyevents(device_serial, pin2, wait_s=0.08))

    if settle_s and settle_s > 0:
        time.sleep(float(settle_s))

    state2 = get_device_state(device_serial)
    unlocked = isinstance(state2, dict) and state2.get("screen_state") in ("unlocked_launcher", "unlocked_app", "unlocked")
    ok = unlocked or all(bool(a.get("ok")) for a in actions if isinstance(a, dict))
    return {
        "ok": ok,
        "device_serial": device_serial,
        "wakeup": bool(wakeup),
        "swipe": bool(swipe),
        "swipe_times": int(swipe_times) if swipe else 0,
        "pin_provided": bool(pin2),
        "actions": actions,
        "device_state": state2,
        "note": "best-effort 解锁：wakeup + swipe + (optional PIN)。不支持指纹/人脸。",
    }


__all__ = [
    "get_display_state",
    "get_device_state",
    "detect_lock_method",
    "lock_screen",
    "wake_and_unlock",
]
