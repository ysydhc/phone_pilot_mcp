#!/usr/bin/env python3
"""
Recording Tools - Touch recording and analysis utilities.
录制工具 - 触摸录制和分析工具。

This module contains recording-related functions extracted from mcp_server.py.
本模块包含从 mcp_server.py 抽取的录制相关函数。

Key functions / 关键函数:
- record_taps_with_ui_impl: Record taps with UI analysis / 录制点击并分析 UI
- analyze_screen_buttons_impl: Analyze clickable buttons / 分析可点击按钮
- record_start_impl: Start touch recording / 开始触摸录制
- record_stop_impl: Stop touch recording / 停止触摸录制
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import select
import subprocess
import sys
import time
from typing import Any, Optional

from android_tool.adb_utils import adb_prefix
from android_tool.adb_parsers import parse_adb_devices
from android_tool.android_device_utils import (
    get_current_focus,
    get_screen_size,
)
from android_tool.device_store import capture_device_profile, get_device_profile_from_store
from android_tool.recordings_store import (
    append_jsonl,
    ensure_abs,
    index_path,
    infer_start_context,
    iso_now,
    now_dirname,
    now_ts,
    read_json,
    safe_name,
    write_json,
)
from android_tool.runner import CommandRunner
from android_tool.screenshot import save_screenshot_png, take_screenshot_png_bytes
from android_tool.touch_mks_convert import convert_raw_to_mks
from android_tool.touch_recorder import (
    start_getevent_recording_detached,
    stop_getevent_recording_pid,
)
from android_tool.uiautomator import (
    UINode,
    dump_ui_xml,
    find_nodes,
    parse_uiautomator_nodes,
)
from android_tool.covert_touch import parse_getevent, PointerAction, scale_actions


async def android_record_taps_with_ui(
    device_serial: Optional[str] = None,
    out_dir: str = "./recordings",
    duration_s: float = 12.0,
    countdown_s: int = 3,
    slot: int = 0,
    keep_device: Optional[str] = None,
    max_taps: int = 30,
    include_screenshot: bool = True,
    include_ui_dump: bool = True,
    include_focus: bool = True,
    max_hits: int = 12,
    post_tap_settle_s: float = 0.25,
) -> dict:
    """
    在你“手动操作手机”的同时，记录你每次点击（tap）命中的控件（best-effort）。

    实现方式（高层）：
    - 监听 `adb shell getevent -lt`（短时间窗口 duration_s）
    - 从触摸事件中重建 DOWN/UP，并把坐标缩放成屏幕像素
    - 每次识别到 tap 后，立即：
      - （可选）截图
      - （可选）uiautomator dump
      - 用 tap 坐标命中控件树里 bounds 覆盖的节点，并返回最“具体”的那个节点

    注意：
    - 这是 best-effort：页面动画/弹窗/无障碍信息缺失会导致命中不稳定。
    - 对于 FLAG_SECURE 页面，截图可能为黑；但 UI dump 仍可能可用。
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    out_root = ensure_abs(out_dir)
    ts = now_dirname()
    safe = safe_name(f"tap_targets_{ts}")
    serial_part = device_serial or "default"
    sess_dir = out_root / "tap_targets" / f"{ts}_{serial_part}_{safe}"
    sess_dir.mkdir(parents=True, exist_ok=True)

    # Preflight: ensure device is connected
    try:
        d = await asyncio.to_thread(parse_adb_devices, CommandRunner.run(["adb", "devices", "-l"], check=False).stdout or "")
        if device_serial and not any((it or {}).get("serial") == device_serial for it in (d or [])):
            return {
                "ok": False,
                "error": "device_not_found",
                "device_serial": device_serial,
                "note": "adb devices 未发现该 serial；请确认设备已连接且授权调试。",
            }
    except Exception:
        # best-effort; don't block recording if parsing fails
        pass

    # Resolve screen + abs ranges for coordinate scaling
    cache_used = False
    screen_w, screen_h = 1080, 1920
    abs_max_x, abs_max_y = 0, 0
    # 1) Prefer cached device profile (fast, avoids `getevent -lp`)
    try:
        cached = await asyncio.to_thread(get_device_profile_from_store, device_serial, out_dir=str(out_root))
        prof0 = (cached or {}).get("profile") if isinstance(cached, dict) else None
        if isinstance(prof0, dict):
            scr = prof0.get("screen") if isinstance(prof0.get("screen"), dict) else None
            if scr:
                screen_w = int(scr.get("w") or screen_w)
                screen_h = int(scr.get("h") or screen_h)
            touch0 = prof0.get("touch") if isinstance(prof0.get("touch"), dict) else None
            if touch0:
                abs_max_x = int(touch0.get("abs_max_x") or 0)
                abs_max_y = int(touch0.get("abs_max_y") or 0)
            cache_used = True
    except Exception:
        cache_used = False

    # 1.1) If device_serial changed (e.g. USB -> wifi adb), try to reuse an existing profile by model/brand.
    # This avoids the expensive `getevent -lp` call in capture_device_profile.
    if not cache_used and (abs_max_x <= 0 or abs_max_y <= 0):
        try:
            # quick props
            model = (CommandRunner.run(adb_prefix(device_serial) + ["shell", "getprop", "ro.product.model"], check=False).stdout or "").strip()
            brand = (CommandRunner.run(adb_prefix(device_serial) + ["shell", "getprop", "ro.product.brand"], check=False).stdout or "").strip()
            manufacturer = (CommandRunner.run(adb_prefix(device_serial) + ["shell", "getprop", "ro.product.manufacturer"], check=False).stdout or "").strip()
            best = None
            dev_dir = out_root / "devices"
            if dev_dir.exists():
                for p in dev_dir.glob("*.json"):
                    try:
                        obj = read_json(p)
                        if not isinstance(obj, dict):
                            continue
                        if model and str(obj.get("model") or "").strip() != model:
                            continue
                        if brand and str(obj.get("brand") or "").strip() != brand:
                            continue
                        if manufacturer and str(obj.get("manufacturer") or "").strip() != manufacturer:
                            continue
                        # choose latest
                        ts0 = float(obj.get("ts") or 0.0)
                        if best is None or ts0 > float(best.get("ts") or 0.0):
                            best = obj
                    except Exception:
                        continue
            if isinstance(best, dict):
                scr = best.get("screen") if isinstance(best.get("screen"), dict) else None
                if scr:
                    screen_w = int(scr.get("w") or screen_w)
                    screen_h = int(scr.get("h") or screen_h)
                touch0 = best.get("touch") if isinstance(best.get("touch"), dict) else None
                if touch0:
                    abs_max_x = int(touch0.get("abs_max_x") or 0)
                    abs_max_y = int(touch0.get("abs_max_y") or 0)
                if abs_max_x > 0 and abs_max_y > 0:
                    cache_used = True
        except Exception:
            pass

    # 2) Fallback: ask device directly for screen size (fast)
    if screen_w <= 0 or screen_h <= 0 or screen_w == 1080 and screen_h == 1920:
        try:
            wh = await asyncio.to_thread(get_screen_size, device_serial)
            screen_w, screen_h = int(wh[0]), int(wh[1])
        except Exception:
            screen_w, screen_h = 1080, 1920

    # 3) Fallback: only if abs range missing, run a profile capture (may call getevent -lp)
    if abs_max_x <= 0 or abs_max_y <= 0:
        try:
            prof = await asyncio.to_thread(capture_device_profile, device_serial, out_dir=str(out_root))
            touch = ((prof or {}).get("profile") or {}).get("touch") if isinstance(prof, dict) else None
            abs_max_x = int((touch or {}).get("abs_max_x") or 0)
            abs_max_y = int((touch or {}).get("abs_max_y") or 0)
        except Exception:
            abs_max_x, abs_max_y = 0, 0
    if abs_max_x <= 0 or abs_max_y <= 0:
        # Fallback: common ranges for 1080x2400 devices
        abs_max_x, abs_max_y = 10800, 24000

    scale_x = float((screen_w - 1) / float(abs_max_x)) if abs_max_x > 0 else 0.1
    scale_y = float((screen_h - 1) / float(abs_max_y)) if abs_max_y > 0 else 0.1

    # User-visible prompt + countdown (printed to stderr so it shows up in terminal)
    try:
        cd = int(countdown_s)
    except Exception:
        cd = 3
    cd = max(0, min(cd, 10))
    print(f"[tap_targets] READY. 将在 {cd}s 后开始监听触摸，请准备开始点击…", file=sys.stderr, flush=True)
    if cd > 0:
        for i in range(cd, 0, -1):
            print(f"[tap_targets] {i}…", file=sys.stderr, flush=True)
            time.sleep(1.0)
    print("[tap_targets] GO! 现在开始记录触摸。", file=sys.stderr, flush=True)

    # Start getevent stream
    cmd = adb_prefix(device_serial) + ["shell", "getevent", "-lt"]
    raw_log = sess_dir / "getevent.log"

    # Collect raw lines for a short window then parse (simple + reliable)
    t_end = time.time() + max(1.0, float(duration_s))
    lines: list[str] = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert proc.stdout is not None
        # Non-blocking-ish read: use select with a timeout so we can honor t_end even if no events arrive.
        while time.time() < t_end:
            timeout = max(0.0, min(0.08, t_end - time.time()))
            r, _, _ = select.select([proc.stdout], [], [], timeout)
            if not r:
                continue
            line = proc.stdout.readline()
            if not line:
                continue
            lines.append(line)
            if len(lines) > 200_000:
                break
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    raw_log.write_text("".join(lines), encoding="utf-8")

    # Parse pointer actions and compress into taps (DOWN..UP)
    actions = []
    try:
        with raw_log.open("r", encoding="utf-8", errors="ignore") as f:
            actions = [a for a in parse_getevent(f, keep_device, int(slot)) if isinstance(a, PointerAction)]
    except Exception as e:
        return {"ok": False, "error": "parse_getevent_failed", "detail": str(e), "raw_log_path": str(raw_log)}

    # Scale to screen pixels
    try:
        actions_px = scale_actions(actions, scale_x=scale_x, scale_y=scale_y, clamp=(screen_w - 1, screen_h - 1))
    except Exception:
        actions_px = actions

    def _nodes_at_xy(nodes, x: int, y: int, *, limit: int) -> list[dict]:
        """
        Return a list of nodes whose bounds contain (x,y), sorted by:
        - clickable first
        - then smaller area (more specific)
        """
        hits: list[tuple[int, int, Any]] = []
        for n in nodes or []:
            b = n.bounds_tuple() if hasattr(n, "bounds_tuple") else None
            if not b:
                continue
            x1, y1, x2, y2 = b
            if x < x1 or x > x2 or y < y1 or y > y2:
                continue
            area = max(1, (x2 - x1) * (y2 - y1))
            clickable = 1 if getattr(n, "clickable", None) is True else 0
            hits.append((-clickable, area, n))
        hits.sort(key=lambda t: (t[0], t[1]))
        out: list[dict] = []
        for _, area, n in hits[: max(1, int(limit))]:
            out.append(
                {
            "package": getattr(n, "package", None),
            "text": getattr(n, "text", None),
            "content_desc": getattr(n, "content_desc", None),
            "resource_id": getattr(n, "resource_id", None),
            "class_name": getattr(n, "class_name", None),
            "bounds": getattr(n, "bounds", None),
                    "area": int(area),
            "clickable": getattr(n, "clickable", None),
            "enabled": getattr(n, "enabled", None),
            "focusable": getattr(n, "focusable", None),
                    "focused": getattr(n, "focused", None),
            "scrollable": getattr(n, "scrollable", None),
            "selected": getattr(n, "selected", None),
                    "checkable": getattr(n, "checkable", None),
                    "checked": getattr(n, "checked", None),
                    "long_clickable": getattr(n, "long_clickable", None),
                    "password": getattr(n, "password", None),
                }
            )
        return out

    def _best_workflow_target(nodes_at_point: list[dict]) -> dict:
        """
        Choose the most useful workflow target from nodes at point.
        Preference:
        - clickable + resource_id
        - then any resource_id
        - else fall back to x/y tap
        """
        rid_clickable = None
        rid_any = None
        for n in nodes_at_point or []:
            rid = (n.get("resource_id") or "").strip()
            if not rid:
                continue
            if n.get("clickable") is True and rid_clickable is None:
                rid_clickable = rid
            if rid_any is None:
                rid_any = rid
        best = rid_clickable or rid_any
        return {
            "best_resource_id": best,
            "resource_id_candidates": [((n.get("resource_id") or "").strip()) for n in (nodes_at_point or []) if (n.get("resource_id") or "").strip()],
        }

    taps: list[dict] = []
    down = None
    last_down_action = None
    last_xy = None
    move_count = 0
    # simple tap heuristic
    TAP_MAX_DURATION_S = 0.9
    TAP_MAX_MOVE_PX = 22

    def _dist(a, b) -> float:
        dx = float(a[0] - b[0])
        dy = float(a[1] - b[1])
        return (dx * dx + dy * dy) ** 0.5

    for a in actions_px:
        if a.action == "DOWN":
            down = a
            last_down_action = a
            last_xy = (int(a.x), int(a.y))
            move_count = 0
        elif a.action == "MOVE" and last_xy is not None:
            xy = (int(a.x), int(a.y))
            if _dist(xy, last_xy) > 1.0:
                move_count += 1
                last_xy = xy
        elif a.action == "UP":
            up = a
            up_xy = (int(up.x), int(up.y))
            # Sometimes we miss the DOWN event (recording starts mid-touch / OEM logs),
            # but we still want to record "what is under this point" for workflow building.
            partial = down is None
            dt = float(up.timestamp - down.timestamp) if (down is not None) else None
            moved = _dist(up_xy, (int(down.x), int(down.y))) if (down is not None) else None
            is_tap = True
            if down is not None:
                is_tap = (dt is not None) and (dt <= TAP_MAX_DURATION_S) and (moved is not None) and (moved <= TAP_MAX_MOVE_PX) and (move_count <= 6)
            if is_tap:
                if post_tap_settle_s and post_tap_settle_s > 0:
                    time.sleep(float(post_tap_settle_s))

                item: dict[str, Any] = {
                    "ts": float(up.timestamp),
                    "x": int(up_xy[0]),
                    "y": int(up_xy[1]),
                    "duration_s": dt,
                    "moved_px": moved,
                    "partial": bool(partial),
                }

                if include_focus:
                    try:
                        item["focus"] = await asyncio.to_thread(get_current_focus, device_serial)
                    except Exception as e:
                        item["focus_error"] = str(e)

                if include_screenshot:
                    try:
                        p = await asyncio.to_thread(
                            save_screenshot_png,
                            device_serial,
                            sess_dir / "screenshots" / f"{len(taps):03d}.png",
                        )
                        item["screenshot_path"] = str(p)
                    except Exception as e:
                        item["screenshot_error"] = str(e)

                if include_ui_dump:
                    try:
                        dump = await asyncio.to_thread(dump_ui_xml, device_serial, compressed=True, wake_and_unlock=False, force_home=False)
                        # Persist XML to file for later correlation with workflows / screenshots.
                        xml = dump.get("xml") if isinstance(dump, dict) else None
                        ui_dump_path = None
                        if xml:
                            ui_dir = sess_dir / "ui_dumps"
                            ui_dir.mkdir(parents=True, exist_ok=True)
                            ui_dump_path = ui_dir / f"{len(taps):03d}.xml"
                            ui_dump_path.write_text(str(xml), encoding="utf-8")
                        item["ui_dump"] = {**dump, "path": str(ui_dump_path) if ui_dump_path else None} if isinstance(dump, dict) else {"ok": False, "error": "uiautomator_dump_failed"}
                        if xml:
                            nodes = parse_uiautomator_nodes(str(xml))
                            try:
                                from android_tool.uiautomator import ui_signature as _ui_sig
                                item["ui_signature"] = _ui_sig(nodes)
                            except Exception:
                                pass
                            item["nodes_count"] = int(len(nodes))
                            hits = _nodes_at_xy(nodes, int(up_xy[0]), int(up_xy[1]), limit=int(max_hits))
                            item["nodes_at_point"] = hits
                            item["matched_node"] = hits[0] if hits else None
                            item["workflow_target"] = _best_workflow_target(hits)
                    except Exception as e:
                        item["ui_dump_error"] = str(e)

                # Always add a workflow step suggestion (resource-id preferred, else x/y tap)
                try:
                    wf = item.get("workflow_target") if isinstance(item.get("workflow_target"), dict) else {}
                    best_rid = (wf.get("best_resource_id") or "").strip() if isinstance(wf, dict) else ""
                    if best_rid:
                        item["workflow_step_suggestion"] = {
                            "type": "find_and_tap",
                            "query": best_rid,
                            "field": "resource_id",
                            "exact": True,
                            "scroll_on_fail": False,
                        }
                    else:
                        item["workflow_step_suggestion"] = {"type": "tap", "x": int(item["x"]), "y": int(item["y"])}
                except Exception:
                    pass

                taps.append(item)
                if len(taps) >= max(1, int(max_taps)):
                    break
            down = None
            last_xy = None
            move_count = 0

    # Fallback: sometimes we capture only a DOWN (missing trailing SYN_REPORT / UP due to timing/termination).
    # For workflow-building, it's still useful to record "what is under this point".
    if not taps and last_down_action is not None:
        up_xy = (int(getattr(last_down_action, "x", 0)), int(getattr(last_down_action, "y", 0)))
        item: dict[str, Any] = {
            "ts": float(getattr(last_down_action, "timestamp", time.time())),
            "x": int(up_xy[0]),
            "y": int(up_xy[1]),
            "duration_s": None,
            "moved_px": None,
            "partial": True,
            "partial_reason": "down_only",
        }
        if include_focus:
            try:
                item["focus"] = await asyncio.to_thread(get_current_focus, device_serial)
            except Exception as e:
                item["focus_error"] = str(e)
        if include_screenshot:
            try:
                p = await asyncio.to_thread(
                    save_screenshot_png,
                    device_serial,
                    sess_dir / "screenshots" / f"{len(taps):03d}.png",
                )
                item["screenshot_path"] = str(p)
            except Exception as e:
                item["screenshot_error"] = str(e)
        if include_ui_dump:
            try:
                dump = await asyncio.to_thread(dump_ui_xml, device_serial, compressed=True, wake_and_unlock=False, force_home=False)
                xml = dump.get("xml") if isinstance(dump, dict) else None
                ui_dump_path = None
                if xml:
                    ui_dir = sess_dir / "ui_dumps"
                    ui_dir.mkdir(parents=True, exist_ok=True)
                    ui_dump_path = ui_dir / f"{len(taps):03d}.xml"
                    ui_dump_path.write_text(str(xml), encoding="utf-8")
                item["ui_dump"] = {**dump, "path": str(ui_dump_path) if ui_dump_path else None} if isinstance(dump, dict) else {"ok": False, "error": "uiautomator_dump_failed"}
                if xml:
                    nodes = parse_uiautomator_nodes(str(xml))
                    try:
                        from android_tool.uiautomator import ui_signature as _ui_sig
                        item["ui_signature"] = _ui_sig(nodes)
                    except Exception:
                        pass
                    item["nodes_count"] = int(len(nodes))
                    hits = _nodes_at_xy(nodes, int(up_xy[0]), int(up_xy[1]), limit=int(max_hits))
                    item["nodes_at_point"] = hits
                    item["matched_node"] = hits[0] if hits else None
                    item["workflow_target"] = _best_workflow_target(hits)
            except Exception as e:
                item["ui_dump_error"] = str(e)

        # Always add a workflow step suggestion (resource-id preferred, else x/y tap)
        try:
            wf = item.get("workflow_target") if isinstance(item.get("workflow_target"), dict) else {}
            best_rid = (wf.get("best_resource_id") or "").strip() if isinstance(wf, dict) else ""
            if best_rid:
                item["workflow_step_suggestion"] = {
                    "type": "find_and_tap",
                    "query": best_rid,
                    "field": "resource_id",
                    "exact": True,
                    "scroll_on_fail": False,
                }
            else:
                item["workflow_step_suggestion"] = {"type": "tap", "x": int(item["x"]), "y": int(item["y"])}
        except Exception:
            pass
        taps.append(item)

    # Persist summary for later referencing / linking to workflows
    try:
        meta = {
            "device_serial": device_serial,
            "started_at": iso_now(),
            "duration_s": float(duration_s),
            "count": int(len(taps)),
            "screen": {"w": int(screen_w), "h": int(screen_h)},
            "touch_abs": {"max_x": int(abs_max_x), "max_y": int(abs_max_y), "scale_x": float(scale_x), "scale_y": float(scale_y)},
            "raw_log_path": str(raw_log),
            "keep_device": keep_device,
            "slot": int(slot),
        }
        (sess_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        (sess_dir / "taps.json").write_text(json.dumps(taps, ensure_ascii=False, indent=2), encoding="utf-8")

        # Also write JSONL for easy streaming / grepping.
        jsonl_path = sess_dir / "taps.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            for it in taps:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")

        # And generate workflow-ready steps (resource-id preferred; fallback to x/y tap).
        steps = []
        for it in taps:
            st = it.get("workflow_step_suggestion")
            if isinstance(st, dict) and st.get("type"):
                steps.append(st)
            else:
                # ultra-safe fallback
                if it.get("x") is not None and it.get("y") is not None:
                    steps.append({"type": "tap", "x": int(it["x"]), "y": int(it["y"])})
        (sess_dir / "workflow_steps.json").write_text(json.dumps(steps, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return {
        "ok": True,
        "device_serial": device_serial,
        "duration_s": float(duration_s),
        "raw_log_path": str(raw_log),
        "out_dir": str(sess_dir),
        "cache_used": bool(cache_used),
        "screen": {"w": int(screen_w), "h": int(screen_h)},
        "touch_abs": {"max_x": int(abs_max_x), "max_y": int(abs_max_y), "scale_x": float(scale_x), "scale_y": float(scale_y)},
        "count": len(taps),
        "taps": taps,
        "taps_jsonl_path": str((sess_dir / "taps.jsonl")),
        "workflow_steps_path": str((sess_dir / "workflow_steps.json")),
        "note": "best-effort：按坐标命中 UIAutomator bounds；若页面变化快/控件无无障碍信息，可能命中不稳定。",
    }




async def android_analyze_screen_buttons(
    device_serial: Optional[str] = None,
    out_dir: str = "./recordings",
    name: str = "analyze_buttons",
    include_screenshot: bool = True,
    include_ui_dump: bool = True,
    wakeup: bool = True,
    only_clickable: bool = True,
    only_enabled: bool = True,
    include_text_targets: bool = True,
    max_results: int = 80,
    include_containers: bool = False,
    annotate: bool = False,
    annotate_limit: int = 80,
) -> dict:
    """
    通过“截图 + UI Tree”分析当前页面，并列出可用按钮（best-effort）。

    - 默认只返回 clickable=true 且 enabled=true 的节点
    - 输出包含：resource-id/text/content-desc/bounds/center/clickable/enabled 等
    - 同时给出 workflow 可直接使用的 selector（优先 resource-id；否则 text/desc；最后 fallback 坐标 tap）
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    out_root = ensure_abs(out_dir)
    ts = now_dirname()
    safe = safe_name(name or "analyze_buttons")
    sess_dir = out_root / "analyze_buttons" / f"{ts}_{device_serial}_{safe}"
    sess_dir.mkdir(parents=True, exist_ok=True)

    # Collect focus (for page association)
    focus = None
    try:
        focus = await asyncio.to_thread(get_current_focus, device_serial)
    except Exception:
        focus = None

    display = None
    device_state = None
    try:
        display = await asyncio.to_thread(get_display_state, device_serial)
    except Exception:
        display = None
    try:
        device_state = await asyncio.to_thread(get_device_state, device_serial)
    except Exception:
        device_state = None

    # Screenshot (optional)
    screenshot_path = None
    screenshot_res = None
    if include_screenshot:
        try:
            screenshot_res = await android_screenshot(device_serial=device_serial, out_dir=str(sess_dir), name="screen", include_base64=False)
            # Keep path even if screenshot is black (still useful for annotation/debugging).
            if isinstance(screenshot_res, dict) and screenshot_res.get("path"):
                screenshot_path = screenshot_res.get("path")
        except Exception as e:
            screenshot_res = {"ok": False, "error": str(e)}

    # UI dump (optional but recommended)
    ui_dump = None
    ui_path = None
    nodes: list[UINode] = []
    sig = None
    if include_ui_dump:
        try:
            ui_dump = await asyncio.to_thread(dump_ui_xml, device_serial, compressed=True, wake_and_unlock=bool(wakeup), force_home=False)
            if isinstance(ui_dump, dict) and ui_dump.get("ok"):
                xml = str(ui_dump.get("xml") or "")
                nodes = parse_uiautomator_nodes(xml)
                sig = ui_signature(nodes) if nodes else None
                # persist xml for debugging
                ui_path = sess_dir / "ui.xml"
                ui_path.write_text(xml, encoding="utf-8")
        except Exception as e:
            ui_dump = {"ok": False, "error": str(e)}

    def _area(b: tuple[int, int, int, int] | None) -> int:
        if not b:
            return -1
        x1, y1, x2, y2 = b
        return max(0, x2 - x1) * max(0, y2 - y1)

    # Estimate screen bounds from the largest node (usually the root FrameLayout).
    screen_area = None
    try:
        areas = [(_area(n.bounds_tuple()), n.bounds_tuple()) for n in nodes]
        areas = [a for a in areas if a[0] is not None and a[0] >= 0 and a[1] is not None]
        if areas:
            screen_area = max(a[0] for a in areas)
    except Exception:
        screen_area = None

    def _is_container(n: UINode, *, a: int) -> bool:
        cls = (n.class_name or "").lower()
        if "layout" in cls or "viewgroup" in cls or "recyclerview" in cls or "viewpager" in cls:
            return True
        # huge clickable container (often background)
        if screen_area and screen_area > 0 and a >= int(screen_area * 0.85):
            return True
        return False

    def _selector_for(n: UINode) -> dict | None:
        rid = (n.resource_id or "").strip()
        txt = (n.text or "").strip()
        desc = (n.content_desc or "").strip()
        if rid:
            return {"type": "find_and_tap", "query": rid, "field": "resource_id", "exact": True, "scroll_on_fail": False}
        if txt:
            return {"type": "find_and_tap", "query": txt, "field": "text", "exact": True, "scroll_on_fail": False}
        if desc:
            return {"type": "find_and_tap", "query": desc, "field": "desc", "exact": True, "scroll_on_fail": False}
        c = n.center()
        if c:
            return {"type": "tap", "x": int(c[0]), "y": int(c[1])}
        return None

    # Build actionable candidates (clickable nodes)
    items = []
    seen = set()
    for n in nodes:
        b = n.bounds_tuple()
        a = _area(b)
        if a <= 0:
            continue
        if only_clickable and n.clickable is not True:
            continue
        if only_enabled and n.enabled is not True:
            continue

        # Optionally drop "pure containers" to keep output high-signal.
        if (not include_containers) and _is_container(n, a=a):
            # keep if it has a strong selector (resource-id or text/desc)
            if not ((n.resource_id or "").strip() or (n.text or "").strip() or (n.content_desc or "").strip()):
                continue

        key = (n.resource_id or "", n.bounds or "", n.text or "", n.content_desc or "", n.class_name or "")
        if key in seen:
            continue
        seen.add(key)

        d = n.to_dict()
        d["area"] = int(a)
        d["workflow_step_suggestion"] = _selector_for(n)
        d["source"] = "clickable"
        # a compact label for display
        d["label"] = (n.text or n.content_desc or n.resource_id or n.class_name or "").strip() or None
        items.append(d)

    # Also include "text targets": nodes with visible text/desc but clickable=false.
    # This covers cases like:
    # - tabs where the parent container is clickable but has no resource-id/text
    # - RecyclerView items where text view is not clickable but tapping the text still triggers the parent
    if include_text_targets:
        for n in nodes:
            b = n.bounds_tuple()
            a = _area(b)
            if a <= 0:
                continue
            if only_enabled and n.enabled is not True:
                continue
            if n.clickable is True:
                continue
            txt = (n.text or "").strip()
            desc = (n.content_desc or "").strip()
            if not (txt or desc):
                continue
            # Avoid tiny/noise labels
            if a < 600:
                continue
            if len(txt) > 60 or len(desc) > 80:
                continue

            # Prefer text/desc selector for these (resource-id like tvTitle is often not unique).
            if txt:
                wf = {"type": "find_and_tap", "query": txt, "field": "text", "exact": True, "scroll_on_fail": False}
            else:
                wf = {"type": "find_and_tap", "query": desc, "field": "desc", "exact": True, "scroll_on_fail": False}

            d = n.to_dict()
            d["area"] = int(a)
            d["workflow_step_suggestion"] = wf
            d["source"] = "label"
            d["label"] = txt or desc
            key = ("label", d.get("label") or "", d.get("bounds") or "")
            if key in seen:
                continue
            seen.add(key)
            items.append(d)

    # Rank: prefer resource-id > text/desc > others; prefer smaller area (more specific)
    def _rank(it: dict) -> tuple:
        rid = (it.get("resource_id") or "").strip()
        txt = (it.get("text") or "").strip()
        desc = (it.get("content_desc") or "").strip()
        area = int(it.get("area") or 0)
        src = (it.get("source") or "").strip()
        # clickable nodes first, then label nodes
        src_rank = 0 if src == "clickable" else 1
        has_rid = 0 if rid else 1
        has_label = 0 if (txt or desc) else 1
        # smaller area first, but don't let ultra-tiny dominate: use area directly (best-effort)
        return (src_rank, has_rid, has_label, area)

    items.sort(key=_rank)
    if max_results and max_results > 0:
        items = items[: int(max_results)]

    # Assign stable indices based on final order (for screenshot annotation).
    for i, it in enumerate(items, start=1):
        it["index"] = int(i)

    # Heuristic: if the dump is only SystemUI root and no actionable nodes, it's likely lock/AOD.
    ui_packages = sorted({(n.package or "").strip() for n in nodes if (n.package or "").strip()})
    likely_locked_or_aod = False
    if ui_packages == ["com.android.systemui"] and len(nodes) <= 2:
        # Typical "null-ish" hierarchy: only root node with no children.
        likely_locked_or_aod = True

    # Optional: annotate screenshot with numbered boxes.
    annotated_path = None
    annotate_res = {"ok": False, "error": "not_run"} if annotate else None
    if annotate and (not screenshot_path):
        annotate_res = {"ok": False, "error": "no_screenshot_path"}
    elif annotate and (not items):
        annotate_res = {"ok": False, "error": "no_items_to_annotate"}
    elif annotate and screenshot_path and items:
        try:
            def _parse_bounds(bounds: str | None) -> Optional[tuple[int, int, int, int]]:
                if not bounds:
                    return None
                # bounds format: "[x1,y1][x2,y2]"
                m = re.match(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$", bounds.strip())
                if not m:
                    return None
                return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

            boxes = []
            draw_n = int(annotate_limit) if int(annotate_limit) > 0 else len(items)
            draw_n = min(draw_n, len(items))
            for it in items[:draw_n]:
                b = _parse_bounds(it.get("bounds"))
                if not b:
                    continue
                x1, y1, x2, y2 = b
                boxes.append(
                    {
                        "x": int(x1),
                        "y": int(y1),
                        "w": int(max(1, x2 - x1)),
                        "h": int(max(1, y2 - y1)),
                        "label": f"#{int(it.get('index') or 0)}",
                    }
                )

            if not boxes:
                annotate_res = {"ok": False, "error": "no_boxes_parsed_from_bounds"}
            else:
                try:
                    imgp = pathlib.Path(str(screenshot_path))
                    if not imgp.exists():
                        annotate_res = {"ok": False, "error": "screenshot_file_missing", "image_path": str(screenshot_path)}
                    elif imgp.stat().st_size <= 0:
                        annotate_res = {"ok": False, "error": "screenshot_file_empty", "image_path": str(screenshot_path)}
                    else:
                        ann = await android_annotate_image_boxes(
                            image_path=str(screenshot_path),
                            boxes=boxes,
                            out_dir=str(sess_dir),
                            name="buttons_annotated",
                        )
                        annotate_res = ann
                        if isinstance(ann, dict) and ann.get("ok") and ann.get("path"):
                            annotated_path = ann.get("path")
                except Exception as e:
                    annotate_res = {"ok": False, "error": "annotate_io_failed", "detail": str(e), "image_path": str(screenshot_path)}
        except Exception as e:
            annotate_res = {"ok": False, "error": str(e)}

    # Persist summary for later grepping.
    try:
        (sess_dir / "buttons.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        meta = {
            "device_serial": device_serial,
            "ts": ts,
            "name": safe,
            "display": display,
            "device_state": device_state,
            "focus": focus,
            "screenshot_path": screenshot_path,
            "ui_path": str(ui_path) if ui_path else None,
            "ui_signature": sig,
            "ui_packages": ui_packages,
            "likely_locked_or_aod": likely_locked_or_aod,
            "count": len(items),
            "annotated_path": annotated_path,
        }
        (sess_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return {
        "ok": True,
        "device_serial": device_serial,
        "out_dir": str(sess_dir),
        "display": display,
        "device_state": device_state,
        "focus": focus,
        "screenshot_path": screenshot_path,
        "annotated_path": annotated_path,
        "ui_path": str(ui_path) if ui_path else None,
        "ui_signature": sig,
        "ui_packages": ui_packages,
        "likely_locked_or_aod": likely_locked_or_aod,
        "count": len(items),
        "buttons": items,
        "note": (
            "buttons 为 best-effort：基于 UIAutomator 的 clickable/enabled 过滤与简单排序。"
            "若截图为黑可能是锁屏/熄屏/AOD 或安全页面(FLAG_SECURE)。"
            "若 ui_packages 只有 com.android.systemui 且 likely_locked_or_aod=true，通常需要先解锁/唤醒回到 App 页面。"
        ),
        "errors": {
            "screenshot": None if (not include_screenshot) else (None if (isinstance(screenshot_res, dict) and screenshot_res.get("ok")) else screenshot_res),
            "ui_dump": None if (not include_ui_dump) else (None if (isinstance(ui_dump, dict) and ui_dump.get("ok")) else ui_dump),
            "annotate": None if (not annotate) else (None if (isinstance(annotate_res, dict) and annotate_res.get("ok")) else annotate_res),
        },
    }




async def android_record_start(
    device_serial: Optional[str] = None,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
    tags: Optional[list[str]] = None,
    restart_app: bool = False,
    app_package: Optional[str] = None,
    app_component: Optional[str] = None,
    app_deeplink: Optional[str] = None,
    app_force_stop: bool = True,
    app_wait_s: float = 1.0,
    reset_home: bool = False,
    home_presses: int = 2,
    keep_device: Optional[str] = None,
    slot: int = 0,
    wait_device: bool = True,
) -> dict:
    """
    开始录制触摸事件（后台启动 `adb shell getevent -lt` 写入文件）。

    本工具会：
    - 可选：等待设备 ready（wait-for-device）
    - 可选：重启指定 App / 或回到桌面（作为录制起点）
    - 自动：采集并保存设备信息到 `recordings/devices/<device_id>.json`（独立设备库）
    - 启动 getevent 录制并写入：
      - `<out_dir>/<timestamp>_<serial>_<name>_<recording_id>/getevent.log`
      - 同目录 `meta.json` + 追加 `index.jsonl` 便于查询

    关键参数：
    - name: 录制名称（会做文件名安全化）
    - tags: 标签（用于后续搜索）
    - restart_app / app_package / app_component / app_deeplink: 录制前启动/重启 App（作为起点）
    - reset_home: 录制前回到桌面首页（作为起点）
    - keep_device / slot: getevent 过滤（只保留某个 input 设备/触摸槽）

    返回字段（示例）：
    - recording_id / name / device_serial / device_id
    - raw_log_path / out_dir
    """
    out_root = ensure_abs(out_dir)
    recording_id = uuid.uuid4().hex[:12]
    friendly = safe_name(name or f"rec_{now_dirname()}")
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]

    # Put id+name into folder so it's greppable in Finder/terminal.
    session_dir = out_root / f"{now_dirname()}_{device_serial or 'default'}_{friendly}_{recording_id}"
    raw_log = session_dir / "getevent.log"

    if wait_device:
        await asyncio.to_thread(CommandRunner.run, adb_prefix(device_serial) + ["wait-for-device"])

    if restart_app:
        await asyncio.to_thread(
            restart_app,
            device_serial,
            package=app_package,
            component=app_component,
            deeplink=app_deeplink,
            force_stop=app_force_stop,
            wait_s=app_wait_s,
        )

    if reset_home:
        await asyncio.to_thread(reset_to_home, device_serial, presses=home_presses)

    # Capture device info into an independent store (devices/<serial>.json) and link recordings by device_id.
    if device_serial:
        captured = await android_device_get(device_serial, out_dir=out_dir)
    else:
        captured = {"ok": False, "error": "device_serial is required"}
    device_id = captured.get("device_id") if isinstance(captured, dict) else None
    device_profile = (captured.get("profile") or {}) if isinstance(captured, dict) and captured.get("ok") else {}

    focus = await asyncio.to_thread(get_current_focus, device_serial)
    start_focused_package = focus.get("package")
    start_focused_activity = focus.get("activity")
    # Persist a replayable start context: home vs app.
    if reset_home or is_launcher_package(start_focused_package):
        start_context_type = "home"
    elif restart_app or start_focused_package:
        start_context_type = "app"
    else:
        start_context_type = "unknown"

    # Start a detached host-side adb process so recording can continue even if the MCP process exits.
    raw_err = session_dir / "getevent.err"
    pid = await asyncio.to_thread(start_getevent_recording_detached, device_serial, raw_log, err_log=raw_err)
    loop = asyncio.get_running_loop()
    _recordings[recording_id] = RecordingMeta(
        recording=None,
        device=device_serial,
        raw_log=raw_log,
        raw_err=raw_err,
        pid=pid,
        out_dir=session_dir,
        keep_device=keep_device,
        slot=slot,
        started_at=loop.time(),
        name=friendly,
        tags=tag_list,
    )

    # Persist per-session meta and append to index for later lookup.
    meta_path = session_dir / "meta.json"
    started_wall = iso_now()
    meta_obj: Dict[str, Any] = {
        "recording_id": recording_id,
        "name": friendly,
        "tags": tag_list,
        "device_serial": device_serial,
        "device_id": device_id or device_serial,
        "start_context_type": start_context_type,
        "start_focused_package": start_focused_package,
        "start_focused_activity": start_focused_activity,
        "restart_app": restart_app,
        "app_package": app_package,
        "app_component": app_component,
        "app_deeplink": app_deeplink,
        "app_force_stop": app_force_stop,
        "app_wait_s": app_wait_s,
        "reset_home": reset_home,
        "home_presses": home_presses,
        "keep_device": keep_device,
        "slot": slot,
        "out_dir": str(session_dir),
        "raw_log_path": str(raw_log),
        "raw_err_path": str(raw_err),
        "pid": pid,
        "status": "recording",
        "started_at": started_wall,
        "created_at": started_wall,
    }
    await asyncio.to_thread(write_json, meta_path, meta_obj)
    await asyncio.to_thread(
        append_jsonl,
        index_path(out_root),
        {
            "recording_id": recording_id,
            "name": friendly,
            "tags": tag_list,
            "device_serial": device_serial,
            "device_id": device_id or device_serial,
            "device_display_name": (device_profile or {}).get("display_name"),
            "device_market_name": (device_profile or {}).get("market_name"),
            "device_model": (device_profile or {}).get("model"),
            "device_brand": (device_profile or {}).get("brand"),
            "start_context_type": start_context_type,
            "start_focused_package": start_focused_package,
            "start_focused_activity": start_focused_activity,
            "restart_app": restart_app,
            "app_package": app_package,
            "app_component": app_component,
            "app_deeplink": app_deeplink,
            "reset_home": reset_home,
            "home_presses": home_presses,
            "out_dir": str(session_dir),
            "raw_log_path": str(raw_log),
            "raw_err_path": str(raw_err),
            "pid": pid,
            "status": "recording",
            "started_at": started_wall,
            "ts": now_ts(),
        },
    )
    return {
        "recording_id": recording_id,
        "name": friendly,
        "tags": tag_list,
        "device_serial": device_serial,
        "device_id": device_id or device_serial,
        "start_context_type": start_context_type,
        "start_focused_package": start_focused_package,
        "start_focused_activity": start_focused_activity,
        "restart_app": restart_app,
        "app_package": app_package,
        "app_component": app_component,
        "app_deeplink": app_deeplink,
        "app_force_stop": app_force_stop,
        "app_wait_s": app_wait_s,
        "reset_home": reset_home,
        "home_presses": home_presses,
        "raw_log_path": str(raw_log),
        "raw_err_path": str(raw_err),
        "pid": pid,
        "out_dir": str(session_dir),
        "keep_device": keep_device,
        "slot": slot,
    }




async def android_record_stop(
    recording_id: str,
    out_dir: str = "./recordings",
    convert: bool = True,
    mks_name: str = "touch.mks",
) -> dict:
    """
    停止一条正在录制的会话（按 recording_id）。

    - convert=true 时，会立即把 raw getevent 日志转换成 Monkey `.mks`
      并写入到该会话目录（默认文件名 touch.mks）。
    - 会更新会话 `meta.json`，并向 `recordings/index.jsonl` 追加一条 stop 事件。

    返回字段（示例）：
    - ok: bool
    - recording_id / device_serial / raw_log_path / duration_ms
    - mks_path（若 convert=true）以及 action_count/line_count/screen_w/h 等统计信息
    """
    meta = _recordings.get(recording_id)
    out_root = ensure_abs(out_dir)

    # If the server process restarted, _recordings may be empty. Recover from on-disk meta/index.
    if not meta:
        item = await asyncio.to_thread(load_recording_by_key, out_root, recording_id)
        if not item:
            return {"ok": False, "error": f"unknown recording_id: {recording_id}"}
        session_dir = ensure_abs(str(item.get("out_dir") or "")) if item.get("out_dir") else None
        raw_log = ensure_abs(str(item.get("raw_log_path") or "")) if item.get("raw_log_path") else None
        if not session_dir or not raw_log:
            return {"ok": False, "error": "recording found in index but missing out_dir/raw_log_path"}
        meta_path = session_dir / "meta.json"
        existing: Dict[str, Any] = {}
        if meta_path.exists():
            try:
                existing = read_json(meta_path)
            except Exception:
                existing = {}
        pid = existing.get("pid") if isinstance(existing, dict) else None
        try:
            pid_i = int(pid) if pid is not None else None
        except Exception:
            pid_i = None
        if pid_i:
            await asyncio.to_thread(stop_getevent_recording_pid, pid_i)
        # Derive duration from started_at wall time.
        duration_ms = None
        try:
            started_at = (existing.get("started_at") if isinstance(existing, dict) else None) or item.get("started_at")
            if started_at:
                sdt = _dt.datetime.fromisoformat(str(started_at))
                duration_ms = int(round((_dt.datetime.now() - sdt).total_seconds() * 1000))
        except Exception:
            duration_ms = None

        # Build a temporary meta object compatible with below flow.
        meta = RecordingMeta(
            recording=None,
            device=item.get("device_serial"),
            raw_log=raw_log,
            raw_err=(session_dir / "getevent.err"),
            pid=pid_i,
            out_dir=session_dir,
            keep_device=existing.get("keep_device") if isinstance(existing, dict) else None,
            slot=int((existing.get("slot") if isinstance(existing, dict) else 0) or 0),
            started_at=asyncio.get_running_loop().time(),
            name=str(item.get("name") or existing.get("name") or recording_id),
            tags=list(existing.get("tags") or item.get("tags") or []),
        )
        duration_ms_i = int(duration_ms) if duration_ms is not None else 0
    else:
        # Stop the active recording.
        if meta.pid:
            await asyncio.to_thread(stop_getevent_recording_pid, int(meta.pid))
        elif meta.recording:
            await asyncio.to_thread(stop_getevent_recording, meta.recording)
        duration_ms_i = int(round((asyncio.get_running_loop().time() - meta.started_at) * 1000))

    result: Dict[str, Any] = {
        "ok": True,
        "recording_id": recording_id,
        "device_serial": meta.device,
        "raw_log_path": str(meta.raw_log),
        "duration_ms": duration_ms_i,
    }

    if convert:
        mks_out = meta.out_dir / mks_name
        stats = await asyncio.to_thread(
            convert_raw_to_mks, meta.raw_log, mks_out, meta.keep_device, meta.slot, meta.device
        )
        result.update(
            {
                "mks_path": str(mks_out),
                "keep_device": meta.keep_device,
                "slot": meta.slot,
                **stats,
            }
        )

    # Update per-session meta and append an index update event.
    meta_path = meta.out_dir / "meta.json"
    finished_wall = iso_now()

    # IMPORTANT: merge with existing meta so we don't lose start_context/restart_app/etc.
    existing: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            existing = read_json(meta_path)
        except Exception:
            existing = {}
    device_id = (existing.get("device_id") if isinstance(existing, dict) else None) or meta.device

    meta_update: Dict[str, Any] = {
        "recording_id": recording_id,
        "name": meta.name,
        "tags": meta.tags,
        "device_serial": meta.device,
        "device_id": device_id,
        "keep_device": meta.keep_device,
        "slot": meta.slot,
        "out_dir": str(meta.out_dir),
        "raw_log_path": str(meta.raw_log),
        "status": "stopped",
        "duration_ms": duration_ms_i,
        "stopped_at": finished_wall,
        "updated_at": finished_wall,
    }
    if "mks_path" in result:
        meta_update.update(
            {
                "mks_path": result.get("mks_path"),
                "action_count": result.get("action_count"),
                "line_count": result.get("line_count"),
                "screen_w": result.get("screen_w"),
                "screen_h": result.get("screen_h"),
                "abs_max_x": result.get("abs_max_x"),
                "abs_max_y": result.get("abs_max_y"),
                "scale_x": result.get("scale_x"),
                "scale_y": result.get("scale_y"),
            }
        )
    merged = {**existing, **meta_update}
    await asyncio.to_thread(write_json, meta_path, merged)

    # Find out_root by parent of out_dir default layout; safest is meta.out_dir.parent.
    out_root = meta.out_dir.parent
    await asyncio.to_thread(
        append_jsonl,
        index_path(out_root),
        {
            "recording_id": recording_id,
            "status": "stopped",
            "duration_ms": duration_ms,
            "mks_path": result.get("mks_path"),
            "action_count": result.get("action_count"),
            "line_count": result.get("line_count"),
            "updated_at": finished_wall,
            "ts": now_ts(),
        },
    )

    _recordings.pop(recording_id, None)
    return result

