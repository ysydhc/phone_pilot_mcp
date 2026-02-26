#!/usr/bin/env python3
"""
UI Finder - UI element finding and interaction utilities.
UI 查找器 - UI 元素查找和交互工具。

This module contains UI finding logic extracted from mcp_server.py.
本模块包含从 mcp_server.py 抽取的 UI 查找逻辑。

Key functions / 关键函数:
- launch_from_home_impl: Launch app from home screen / 从桌面启动应用
- analyze_screen_buttons_impl: Analyze clickable buttons on screen / 分析屏幕上的可点击按钮
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from typing import Optional

import uuid

from phone_pilot.android.device.utils import get_screen_size, get_current_focus, input_swipe, is_launcher_package, reset_to_home, restart_app
from phone_pilot.android.ui.clicker import Clicker
from phone_pilot.core.storage import (
    append_index_record,
    iso_now,
    migrate_jsonl_to_json,
    now_ts,
    recordings_root,
    safe_name,
)
from phone_pilot.android.ui.automator import dump_ui_xml, find_nodes, parse_uiautomator_nodes, pick_scrollable_bounds, ui_signature, infer_scroll_directions


def _build_component(pkg: Optional[str], act: Optional[str]) -> Optional[str]:
    """Build component string from package and activity."""
    if not pkg or not act:
        return None
    return f"{pkg}/{act}"


def _build_adb_start_cmd(device_serial: Optional[str], component: Optional[str]) -> Optional[str]:
    """Build adb start command."""
    if not component:
        return None
    serial_part = f"-s {device_serial} " if device_serial else ""
    return f"adb {serial_part}shell am start -n {component}"


def _looks_like_leakcanary_component(component: Optional[str]) -> bool:
    """Check if component looks like LeakCanary launcher."""
    if not component:
        return False
    s = component.lower()
    return bool(s) and (("leakcanary" in s) or ("leaklauncheractivity" in s))


def _launch_profiles_index_path(out_root: pathlib.Path) -> pathlib.Path:
    """Get path to launch profiles index."""
    return out_root / "launch_profiles" / "index.json"


async def _find_text_and_tap_with_scroll(
    device_serial: str,
    query: str,
    max_swipes: int = 10,
    directions: Optional[list[str]] = None,
    stuck_threshold: int = 2,
    tap_offset_x: int = 0,
    tap_offset_y: int = 0,
    swipe_duration_ms: int = 280,
    swipe_wait_s: float = 0.35,
) -> dict:
    """Find a UI element by text and tap it, scrolling through pages if needed.

    This is a simplified find-and-tap that only uses UIAutomator (no OCR / image
    fallback) and is intended for home-screen icon launching where text labels
    are always available in the UI hierarchy.
    """
    # --- helpers ---
    def _dump_and_find() -> tuple[list[dict], Optional[tuple[int, int, int, int]], Optional[str]]:
        res = dump_ui_xml(device_serial, compressed=True)
        if not (isinstance(res, dict) and res.get("ok") and res.get("xml")):
            return [], None, None
        xml = str(res.get("xml") or "")
        nodes = parse_uiautomator_nodes(xml)
        sig = ui_signature(nodes)
        sb = pick_scrollable_bounds(nodes)
        hits = find_nodes(nodes, query=query, field="auto", exact=False, case_sensitive=False, limit=5)
        matches = [h.to_dict() for h in hits]
        return matches, sb, sig

    matches, sb, sig_prev = await asyncio.to_thread(_dump_and_find)

    # --- scroll if element not found ---
    if not matches and sb and sig_prev:
        dirs2 = [d for d in (directions or ["left", "right"]) if d in ("up", "down", "left", "right")]
        if not dirs2:
            dirs2 = ["left", "right"]

        x1, y1, x2, y2 = sb
        pad = 0.25
        w, h = max(1, x2 - x1), max(1, y2 - y1)
        px, py = int(round(w * pad)), int(round(h * pad))
        sx1, sx2 = x1 + px, x2 - px
        sy1, sy2 = y1 + py, y2 - py
        cx_s = int(round((sx1 + sx2) / 2))
        cy_s = int(round((sy1 + sy2) / 2))

        def _swipe_pts(d: str) -> tuple[int, int, int, int]:
            if d == "up":
                return cx_s, sy2, cx_s, sy1
            if d == "down":
                return cx_s, sy1, cx_s, sy2
            if d == "left":
                return sx2, cy_s, sx1, cy_s
            return sx1, cy_s, sx2, cy_s  # right

        dir_idx, stuck_count = 0, 0
        for _ in range(max(1, min(max_swipes, 20))):
            direction = dirs2[dir_idx]
            a1, b1, a2, b2 = _swipe_pts(direction)
            await asyncio.to_thread(
                input_swipe, device_serial, a1, b1, a2, b2,
                duration_ms=swipe_duration_ms, wait_s=swipe_wait_s,
            )
            matches, sb_new, sig_new = await asyncio.to_thread(_dump_and_find)
            if sb_new:
                sb = sb_new
            same = bool(sig_new and sig_prev and sig_new == sig_prev)
            if same:
                stuck_count += 1
            else:
                stuck_count = 0
            sig_prev = sig_new or sig_prev
            if stuck_count >= stuck_threshold and (dir_idx + 1) < len(dirs2):
                dir_idx += 1
                stuck_count = 0
            if matches:
                break

    if not matches:
        return {"ok": False, "error": "no_match", "query": query}

    m = matches[0]
    cx = m.get("center_x")
    cy = m.get("center_y")
    if cx is None or cy is None:
        return {"ok": False, "error": "match_has_no_center", "match": m}

    tx = int(cx) + int(tap_offset_x)
    ty = int(cy) + int(tap_offset_y)
    # Clamp to screen
    try:
        wh = get_screen_size(device_serial)
        if wh and len(wh) == 2:
            tx = max(0, min(int(wh[0]) - 1, tx))
            ty = max(0, min(int(wh[1]) - 1, ty))
    except Exception:
        pass

    clicker = Clicker(device_serial=device_serial)
    tap_result = await asyncio.to_thread(clicker.tap, tx, ty)
    return {
        "ok": bool(isinstance(tap_result, dict) and tap_result.get("ok")),
        "match": m,
        "tap_point": {"x": tx, "y": ty},
        "tap": tap_result,
    }


async def launch_from_home_impl(
    device_serial: Optional[str],
    query: str = "",
    out_dir: str = "./.recordings",
    reset_home: bool = True,
    home_presses: int = 2,
    max_swipes: int = 10,
    directions: Optional[list[str]] = None,
    stuck_threshold: int = 2,
    tap_offset_x: int = 0,
    tap_offset_y: int = -160,
    wait_s: float = 1.2,
    save_profile: bool = True,
) -> dict:
    """
    Find app icon on home screen and tap to launch, then record the launched Activity.
    在桌面找图标并点击启动 App，然后记录启动的 Activity。

    Features / 功能:
    - Find icon by text on home screen / 在桌面通过文字查找图标
    - Tap with offset to hit icon area / 使用偏移点击图标区域
    - Record launched package/activity for future fast launch / 记录启动的包/Activity 供后续快速启动

    Args / 参数:
    - query: App name to search / 要搜索的应用名
    - reset_home: Go to home screen first / 先回到桌面
    - tap_offset_y: Offset to tap icon instead of label (default -160) / 点击偏移以点击图标而非标签
    - save_profile: Save launch profile for future use / 保存启动配置供后续使用
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    out_root = recordings_root(out_dir)

    if reset_home:
        await asyncio.to_thread(reset_to_home, device_serial, presses=int(home_presses))

    # Default: home is paged horizontally, but infer from UI if possible.
    dirs = directions
    if not dirs:
        try:
            res = await asyncio.to_thread(dump_ui_xml, device_serial, compressed=True)
            if isinstance(res, dict) and res.get("ok") and res.get("xml"):
                nodes = parse_uiautomator_nodes(str(res.get("xml") or ""))
                dirs = infer_scroll_directions(nodes, default_dirs=["left", "right"])
        except Exception:
            dirs = None
    if not dirs:
        dirs = ["left", "right"]

    # Launcher pages should prefer horizontal paging.
    try:
        focus = await asyncio.to_thread(get_current_focus, device_serial)
        pkg = (focus or {}).get("package") if isinstance(focus, dict) else None
        if pkg and is_launcher_package(str(pkg)):
            dirs = ["left", "right"]
    except Exception:
        pass

    # --- find text on home screen with page scrolling, then tap ---
    try:
        tap_res = await _find_text_and_tap_with_scroll(
            device_serial=device_serial,
            query=query,
            max_swipes=int(max_swipes),
            directions=dirs,
            stuck_threshold=int(stuck_threshold),
            tap_offset_x=int(tap_offset_x),
            tap_offset_y=int(tap_offset_y),
        )
    except Exception as e:
        import traceback
        return {
            "ok": False,
            "error": "launch_find_failed",
            "detail": str(e),
            "trace": traceback.format_exc(),
        }
    if not (isinstance(tap_res, dict) and tap_res.get("ok")):
        return {"ok": False, "error": "tap_icon_failed", "detail": tap_res}

    # After tapping, wait and poll focus to avoid transient/null focus.
    deadline_s = max(0.0, float(wait_s or 0.0)) + 2.5
    t0 = time.time()
    focus: dict[str, Optional[str]] = {"package": None, "activity": None}
    while True:
        focus = await asyncio.to_thread(get_current_focus, device_serial)
        pkg = (focus or {}).get("package") if isinstance(focus, dict) else None
        act = (focus or {}).get("activity") if isinstance(focus, dict) else None
        if pkg and act and not is_launcher_package(pkg):
            break
        if time.time() - t0 >= deadline_s:
            break
        await asyncio.to_thread(time.sleep, 0.25)

    pkg = (focus or {}).get("package") if isinstance(focus, dict) else None
    act = (focus or {}).get("activity") if isinstance(focus, dict) else None
    component = _build_component(pkg, act)
    adb_cmd = _build_adb_start_cmd(device_serial, component) if component else None

    # Avoid LeakCanary launcher page
    if pkg and component and _looks_like_leakcanary_component(component):
        await asyncio.to_thread(restart_app, device_serial, package=pkg, force_stop=True, wait_s=float(wait_s))
        focus = await asyncio.to_thread(get_current_focus, device_serial)
        pkg = (focus or {}).get("package") if isinstance(focus, dict) else None
        act = (focus or {}).get("activity") if isinstance(focus, dict) else None
        component = _build_component(pkg, act)
        adb_cmd = _build_adb_start_cmd(device_serial, component) if component else None

    # If still on launcher (or no focus), try launching by package resolved from device profile.
    if (not pkg) or (pkg and is_launcher_package(pkg)):
        try:
            from phone_pilot.android.device.store import get_device_profile_from_store
            prof = get_device_profile_from_store(device_serial, out_dir=str(out_root))
            apps = (prof.get("profile") or {}).get("apps", {}).get("apps", []) if isinstance(prof, dict) else []
            q = (query or "").strip().lower()
            pkg2 = None
            for app in apps:
                name = str(app.get("AppName") or app.get("app_name") or "").strip().lower()
                if q and name and q in name:
                    pkg2 = str(app.get("PackageName") or app.get("package_name") or "").strip()
                    if pkg2:
                        break
            if pkg2:
                await asyncio.to_thread(restart_app, device_serial, package=pkg2, force_stop=True, wait_s=float(wait_s))
                focus = await asyncio.to_thread(get_current_focus, device_serial)
                pkg = (focus or {}).get("package") if isinstance(focus, dict) else None
                act = (focus or {}).get("activity") if isinstance(focus, dict) else None
                component = _build_component(pkg, act)
                adb_cmd = _build_adb_start_cmd(device_serial, component) if component else None
        except Exception:
            pass

    if (not pkg) or (pkg and is_launcher_package(pkg)):
        return {"ok": False, "error": "launch_failed", "detail": "focus_invalid_or_launcher", "tap": tap_res}

    rec = {
        "profile_id": uuid.uuid4().hex[:12],
        "name": safe_name(query or (pkg or "app")),
        "query": query,
        "device_serial": device_serial,
        "package": pkg,
        "activity": act,
        "component": component,
        "adb_cmd": adb_cmd,
        "tap": tap_res,
        "created_at": iso_now(),
        "ts": now_ts(),
    }
    saved = False
    if save_profile and component:
        if _looks_like_leakcanary_component(component):
            return {"ok": True, "focus": focus, "component": component, "adb_cmd": adb_cmd, "saved": False, "record": rec, "note": "检测到 LeakCanary Launcher，已跳过保存 profile"}
        idx = _launch_profiles_index_path(out_root)
        legacy = out_root / "launch_profiles" / "index.jsonl"
        migrate_jsonl_to_json(idx, legacy)
        await asyncio.to_thread(append_index_record, idx, rec)
        saved = True

    return {"ok": True, "focus": focus, "component": component, "adb_cmd": adb_cmd, "saved": saved, "record": rec}
