#!/usr/bin/env python3
"""
UI Finder - UI element finding and interaction utilities.
UI 查找器 - UI 元素查找和交互工具。

This module contains UI finding logic extracted from mcp_server.py.
本模块包含从 mcp_server.py 抽取的 UI 查找逻辑。

Key functions / 关键函数:
- find_and_tap_impl: Find UI element and tap / 查找 UI 元素并点击
- dismiss_popups_impl: Dismiss common popups / 关闭常见弹窗
- analyze_screen_buttons_impl: Analyze clickable buttons on screen / 分析屏幕上的可点击按钮
"""

from __future__ import annotations

import asyncio
import pathlib
import time
from typing import Any, Optional

import uuid

from android_tool.android_device_utils import get_screen_size, get_current_focus, input_swipe, is_launcher_package, reset_to_home, restart_app
from android_tool.clicker import Clicker
from android_tool.recordings_store import append_jsonl, ensure_abs, iso_now, now_dirname, now_ts, safe_name
from android_tool.screenshot import save_screenshot_png, take_screenshot_png_bytes
from android_tool.uiautomator import UINode, dump_ui_xml, find_nodes, parse_uiautomator_nodes, pick_scrollable_bounds, ui_signature
from android_tool.vision import annotate_boxes_on_image_file, find_template_on_screen_with_fallback
from android_tool.ocr import ocr_screenshot_and_find


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
    return out_root / "launch_profiles" / "index.jsonl"


async def find_and_tap_impl(
    device_serial: Optional[str],
    query: str = "",
    field: str = "text_or_desc",  # Deprecated, always uses text_or_desc
    exact: bool = False,
    match_index: int = 0,
    scroll_on_fail: bool = True,
    max_swipes: int = 4,
    directions: Optional[list[str]] = None,
    swipe_padding_ratio: float = 0.25,
    swipe_duration_ms: int = 280,
    swipe_wait_s: float = 0.35,
    stuck_threshold: int = 2,
    tap_offset_x: int = 0,
    tap_offset_y: int = 0,
    pre_screenshot: bool = False,
    post_screenshot: bool = False,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
    debug_annotate: bool = False,
    # OCR fallback options / OCR 回退选项
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    ocr_psm: int = 6,
    # Image fallback options / 图片回退选项
    template_path: Optional[str] = None,
    threshold: float = 0.80,
    grayscale: bool = True,
    roi: Optional[tuple[int, int, int, int]] = None,
    scales: Optional[list[float]] = None,
    method: str = "TM_CCOEFF_NORMED",
    feature_enabled: bool = False,
    feature_ratio_thresh: float = 0.75,
    feature_min_inliers: int = 8,
) -> dict:
    """
    Enhanced find and tap with three-level fallback: UIAutomator → OCR → Image.
    增强版查找并点击，支持三级回退：UIAutomator → OCR → 图片匹配。

    This is the core implementation for android_find_and_tap tool.
    这是 android_find_and_tap 工具的核心实现。

    Features / 功能:
    - UIAutomator-based element finding (primary) / 基于 UIAutomator 的元素查找（主要）
    - OCR fallback for keyboard/IME text / OCR 回退，适用于键盘/输入法文字
    - Image template matching fallback / 图片模板匹配回退
    - Auto-scroll when element not found / 元素未找到时自动滚动
    - Stuck detection to switch scroll direction / 卡住检测以切换滚动方向
    - Tap offset support for icon labels / 支持图标标签的点击偏移

    Args:
        device_serial: Device serial number / 设备序列号
        query: Text/description to search (for UIAutomator and OCR) / 要搜索的文字/描述
        field: Deprecated, always uses text_or_desc / 已废弃，始终使用 text_or_desc
        exact: Exact match or substring / 精确匹配或子串
        match_index: Which match to tap (0-based) / 点击第几个匹配项（从0开始）
        scroll_on_fail: Auto-scroll if not found / 未找到时自动滚动
        max_swipes: Maximum scroll attempts / 最大滚动次数
        directions: Scroll directions to try / 尝试的滚动方向
        swipe_padding_ratio: Padding ratio for swipe bounds / 滑动边界的填充比例
        swipe_duration_ms: Swipe duration in ms / 滑动持续时间（毫秒）
        swipe_wait_s: Wait time after swipe / 滑动后等待时间
        stuck_threshold: Swipes before switching direction / 切换方向前的滑动次数
        tap_offset_x: X offset from element center / 相对元素中心的 X 偏移
        tap_offset_y: Y offset from element center / 相对元素中心的 Y 偏移
        pre_screenshot: Take screenshot before tap / 点击前截图
        post_screenshot: Take screenshot after tap / 点击后截图
        out_dir: Output directory / 输出目录
        name: Name for screenshots / 截图名称
        debug_annotate: Draw debug boxes on screenshot / 在截图上绘制调试框
        ocr_fallback: Enable OCR fallback when UIAutomator fails / UIAutomator 失败时启用 OCR 回退
        ocr_lang: OCR language (eng/chi_sim/chi_tra/eng+chi_sim) / OCR 语言
        ocr_psm: Tesseract page segmentation mode / Tesseract 页面分割模式
        template_path: Image template path for image fallback / 图片模板路径（用于图片回退）
        threshold: Image matching threshold / 图片匹配阈值
        grayscale: Use grayscale for image matching / 使用灰度图进行图片匹配
        roi: Region of interest for image matching / 图片匹配的感兴趣区域
        scales: Scales to try for image matching / 图片匹配尝试的缩放比例
        method: Image matching method / 图片匹配方法
        feature_enabled: Enable feature-based matching / 启用特征匹配
        feature_ratio_thresh: Feature matching ratio threshold / 特征匹配比例阈值
        feature_min_inliers: Minimum feature inliers / 最小特征内点数

    Returns:
        Result dict with ok, match, tap info / 包含 ok、match、tap 信息的结果字典
    """
    # Default to text_or_desc if not specified or is text_or_desc
    # Keep resource_id, text, desc, class as specific field types
    if not field or field == "text_or_desc":
        field = "text_or_desc"
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    out_root = ensure_abs(out_dir)
    ts = now_dirname()
    safe = safe_name(name or query or ts)
    serial_part = device_serial or "default"

    before_path = None
    after_path = None
    if pre_screenshot:
        before_path = str(
            await asyncio.to_thread(
                save_screenshot_png,
                device_serial,
                out_root / "find_tap" / f"{ts}_{serial_part}_{safe}_before.png",
            )
        )

    async def _dump_find_and_sig() -> tuple[dict, list[dict], Optional[tuple[int, int, int, int]], Optional[str]]:
        """Dump UI, find matches, get scrollable bounds and signature."""
        res = await asyncio.to_thread(dump_ui_xml, device_serial, compressed=True)
        if not (isinstance(res, dict) and res.get("ok") and res.get("xml")):
            return (
                {"ok": False, "device_serial": device_serial, "error": "ui_dump_failed", "detail": res},
                [],
                None,
                None,
            )
        xml = str(res.get("xml") or "")
        nodes = parse_uiautomator_nodes(xml)
        sig = ui_signature(nodes)
        sb = pick_scrollable_bounds(nodes)
        hits = find_nodes(
            nodes,
            query=query,
            field=field,
            exact=bool(exact),
            case_sensitive=False,
            limit=20,
        )
        payload = {
            "ok": True,
            "device_serial": device_serial,
            "query": query,
            "field": field,
            "exact": bool(exact),
            "case_sensitive": False,
            "count": len(hits),
            "matches": [h.to_dict() for h in hits],
        }
        return payload, payload["matches"], sb, sig

    found, matches, sb0, sig0 = await _dump_find_and_sig()
    if not (isinstance(found, dict) and found.get("ok")):
        return {"ok": False, "error": "find_failed", "detail": found, "before_screenshot": before_path}

    attempts: list[dict] = []
    screen_w = screen_h = None
    try:
        wh = await asyncio.to_thread(get_screen_size, device_serial)
        if wh and isinstance(wh, tuple) and len(wh) == 2:
            screen_w, screen_h = int(wh[0]), int(wh[1])
    except Exception:
        screen_w = screen_h = None

    # Track which method was used for finding
    method_used = "uiautomator"
    ocr_result = None
    image_result = None

    # Fallback 1: OCR BEFORE scrolling (when UIAutomator fails)
    # OCR 回退优先于滑动（UIAutomator 失败时先尝试 OCR）
    if (not matches) and ocr_fallback and query:
        method_used = "ocr"
        try:
            ocr_result = await asyncio.to_thread(
                ocr_screenshot_and_find,
                device_serial,
                out_dir=str(out_root),
                name=safe + "_ocr",
                query=query,
                lang=ocr_lang,
                psm=ocr_psm,
                exact=bool(exact),
                case_sensitive=False,
                limit=20,
            )
            if isinstance(ocr_result, dict) and ocr_result.get("ok"):
                ocr_matches = ocr_result.get("matches") or []
                if ocr_matches:
                    matches = ocr_matches
        except Exception as e:
            ocr_result = {"ok": False, "error": str(e)}

    # Fallback 2: Image matching BEFORE scrolling (when both UIAutomator and OCR fail)
    # 图片匹配回退（UIAutomator 和 OCR 都失败时）
    if (not matches) and template_path:
        method_used = "image"
        import pathlib as _pathlib
        tpl_path = _pathlib.Path(template_path).expanduser().resolve()
        if tpl_path.exists():
            try:
                image_result = await asyncio.to_thread(
                    find_template_on_screen_with_fallback,
                    device_serial,
                    template_path=str(tpl_path),
                    threshold=float(threshold),
                    grayscale=bool(grayscale),
                    roi=roi,
                    scales=scales,
                    method=method,
                    max_results=10,
                    feature_enabled=bool(feature_enabled),
                    feature_ratio_thresh=float(feature_ratio_thresh),
                    feature_min_inliers=int(feature_min_inliers),
                )
                if isinstance(image_result, dict) and image_result.get("ok"):
                    img_matches = image_result.get("matches") or []
                    if not img_matches and image_result.get("match"):
                        img_matches = [image_result.get("match")]
                    if img_matches:
                        matches = img_matches
            except Exception as e:
                image_result = {"ok": False, "error": str(e)}
        else:
            image_result = {"ok": False, "error": f"template_not_found: {template_path}"}

    # Scrolling: Only if all methods (UIAutomator, OCR, Image) failed
    # 滑动查找：仅当所有方法都失败时才滑动
    if (not matches) and bool(scroll_on_fail):
        sb = sb0
        sig_prev = sig0
        if sb and sig_prev:
            dirs = directions or ["up", "down", "left", "right"]
            dirs2: list[str] = []
            for d in dirs:
                d2 = str(d or "").strip().lower()
                if d2 in ("up", "down", "left", "right") and d2 not in dirs2:
                    dirs2.append(d2)
            if not dirs2:
                dirs2 = ["up", "down", "left", "right"]

            try:
                max_n = int(max_swipes)
            except Exception:
                max_n = 4
            max_n = max(1, min(max_n, 12))
            try:
                stuck_n = int(stuck_threshold)
            except Exception:
                stuck_n = 2
            stuck_n = max(1, min(stuck_n, 5))

            x1, y1, x2, y2 = sb
            pad = float(swipe_padding_ratio)
            pad = 0.0 if pad < 0 else (0.3 if pad > 0.3 else pad)
            w = max(1, x2 - x1)
            h = max(1, y2 - y1)
            px = int(round(w * pad))
            py = int(round(h * pad))
            sx1 = x1 + px
            sx2 = x2 - px
            sy1 = y1 + py
            sy2 = y2 - py
            cx = int(round((sx1 + sx2) / 2))
            cy = int(round((sy1 + sy2) / 2))

            fallback_swipe = False

            def _fallback_vertical(direction: str) -> Optional[tuple[int, int, int, int]]:
                if not (screen_w and screen_h and screen_w > 0 and screen_h > 0):
                    return None
                fx = int(round(screen_w * 0.5))
                y_top = int(round(screen_h * 0.36))
                y_bot = int(round(screen_h * 0.74))
                if direction == "up":
                    return fx, y_bot, fx, y_top
                if direction == "down":
                    return fx, y_top, fx, y_bot
                return None

            def swipe_points(direction: str) -> tuple[int, int, int, int]:
                if fallback_swipe and direction in ("up", "down"):
                    fb = _fallback_vertical(direction)
                    if fb:
                        return fb
                if direction == "up":
                    return cx, sy2, cx, sy1
                if direction == "down":
                    return cx, sy1, cx, sy2
                if direction == "left":
                    return sx2, cy, sx1, cy
                return sx1, cy, sx2, cy

            dir_idx = 0
            stuck_count = 0
            for n in range(max_n):
                direction = dirs2[dir_idx]
                ax1, ay1, ax2, ay2 = swipe_points(direction)
                swipe_res = await asyncio.to_thread(
                    input_swipe,
                    device_serial,
                    ax1,
                    ay1,
                    ax2,
                    ay2,
                    duration_ms=int(swipe_duration_ms),
                    wait_s=float(swipe_wait_s),
                )

                found2, matches2, sb2, sig2 = await _dump_find_and_sig()
                if sb2:
                    sb = sb2

                same = bool(sig2 and sig_prev and sig2 == sig_prev)
                if same:
                    stuck_count += 1
                else:
                    stuck_count = 0
                sig_prev = sig2 or sig_prev

                if same and (not fallback_swipe) and direction in ("up", "down"):
                    fallback_swipe = True

                switched = False
                if stuck_count >= stuck_n and (dir_idx + 1) < len(dirs2):
                    dir_idx += 1
                    switched = True
                    stuck_count = 0

                attempts.append(
                    {
                        "try": n + 1,
                        "direction": direction,
                        "switched": switched,
                        "fallback_swipe": fallback_swipe,
                        "stuck_count": stuck_count,
                        "stuck_threshold": stuck_n,
                        "scrollable_bounds": list(sb),
                        "ui_signature_same": same,
                        "swipe": swipe_res,
                        "found_count": len(matches2),
                    }
                )
                if isinstance(found2, dict) and found2.get("ok") and matches2:
                    found = found2
                    matches = matches2
                    break

    try:
        idx = int(match_index)
    except Exception:
        idx = 0
    
    if not matches or idx < 0 or idx >= len(matches):
        return {
            "ok": False,
            "error": "no_match",
            "found": found,
            "attempts": attempts,
            "before_screenshot": before_path,
            "method_tried": method_used,
            "ocr_result": ocr_result,
            "image_result": image_result,
        }

    m = matches[idx]
    cx = m.get("center_x")
    cy = m.get("center_y")
    if cx is None or cy is None:
        return {"ok": False, "error": "match_has_no_center", "match": m, "found": found, "before_screenshot": before_path, "method_used": method_used}

    try:
        ox = int(tap_offset_x)
    except Exception:
        ox = 0
    try:
        oy = int(tap_offset_y)
    except Exception:
        oy = 0
    tx = int(cx) + ox
    ty = int(cy) + oy
    clicker = Clicker(device_serial=device_serial)
    tap_res = await asyncio.to_thread(clicker.tap, tx, ty)

    if post_screenshot:
        after_path = str(
            await asyncio.to_thread(
                save_screenshot_png,
                device_serial,
                out_root / "find_tap" / f"{ts}_{serial_part}_{safe}_after.png",
            )
        )

    result = {
        "ok": bool(isinstance(tap_res, dict) and tap_res.get("ok")),
        "device_serial": device_serial,
        "query": query,
        "template_path": template_path,
        "exact": bool(exact),
        "match_index": idx,
        "match": m,
        "method_used": method_used,
        "tap_point": {"x": tx, "y": ty, "offset_x": ox, "offset_y": oy},
        "tap": tap_res,
        "attempts": attempts,
        "before_screenshot": before_path,
        "after_screenshot": after_path,
    }
    if ocr_result:
        result["ocr_result"] = ocr_result
    if image_result:
        result["image_result"] = image_result

    if debug_annotate and m:
        out_root = ensure_abs(out_dir)
        ts = now_dirname()
        safe = safe_name(name or f"find_tap_{query or ts}")
        serial_part = device_serial or "default"
        base_path = before_path
        if not base_path:
            shot_path = out_root / "find_tap_debug" / f"{ts}_{serial_part}_{safe}.png"
            shot_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(save_screenshot_png, device_serial, shot_path)
            base_path = str(shot_path)
        boxes = [
            {
                "x": int(m.get("x", 0)),
                "y": int(m.get("y", 0)),
                "w": int(m.get("w", 0)),
                "h": int(m.get("h", 0)),
                "label": str(m.get("text") or m.get("content_desc") or m.get("resource_id") or ""),
            }
        ]
        ann = await asyncio.to_thread(annotate_boxes_on_image_file, image_path=str(base_path), boxes=boxes)
        if isinstance(ann, dict) and ann.get("ok") and isinstance(ann.get("annotated_png"), (bytes, bytearray)):
            out_path = out_root / "find_tap_debug" / f"{ts}_{serial_part}_{safe}_annotated.png"
            out_path.write_bytes(bytes(ann["annotated_png"]))
            result["annotated_path"] = str(out_path)

    return result


# Common popup dismiss targets
# 常见弹窗关闭目标
POPUP_TARGETS = [
    # English
    {"query": "Allow", "field": "text", "exact": False},
    {"query": "OK", "field": "text", "exact": True},
    {"query": "Skip", "field": "text", "exact": False},
    {"query": "Later", "field": "text", "exact": False},
    {"query": "Not now", "field": "text", "exact": False},
    {"query": "Deny", "field": "text", "exact": False},
    {"query": "Cancel", "field": "text", "exact": False},
    {"query": "Close", "field": "text", "exact": False},
    {"query": "Got it", "field": "text", "exact": False},
    {"query": "Continue", "field": "text", "exact": False},
    # Chinese
    {"query": "允许", "field": "text", "exact": False},
    {"query": "确定", "field": "text", "exact": False},
    {"query": "确认", "field": "text", "exact": False},
    {"query": "跳过", "field": "text", "exact": False},
    {"query": "以后再说", "field": "text", "exact": False},
    {"query": "稍后", "field": "text", "exact": False},
    {"query": "取消", "field": "text", "exact": False},
    {"query": "关闭", "field": "text", "exact": False},
    {"query": "知道了", "field": "text", "exact": False},
    {"query": "我知道了", "field": "text", "exact": False},
    # Japanese
    {"query": "許可", "field": "text", "exact": False},
    {"query": "OK", "field": "text", "exact": True},
    {"query": "スキップ", "field": "text", "exact": False},
    # Resource IDs (common system dialogs)
    {"query": "com.android.permissioncontroller:id/permission_allow_button", "field": "resource_id", "exact": True},
    {"query": "android:id/button1", "field": "resource_id", "exact": True},
    {"query": "android:id/button2", "field": "resource_id", "exact": True},
]


async def dismiss_popups_impl(
    device_serial: Optional[str],
    out_dir: str = "./recordings",
    name: str = "dismiss_popups",
    max_rounds: int = 3,
    max_taps: int = 6,
    settle_s: float = 0.35,
    pre_screenshot: bool = False,
    post_screenshot: bool = True,
    extra_targets: Optional[list[dict]] = None,
) -> dict:
    """
    Best-effort dismiss common popups/permission dialogs.
    尽力关闭常见弹窗/权限对话框。

    Useful when workflows are interrupted by:
    当工作流被以下情况中断时很有用：
    - Runtime permission dialogs / 运行时权限对话框
    - Update prompts / 更新提示
    - "OK/确认/允许/跳过" dialogs / "OK/确认/允许/跳过"对话框

    Args:
        device_serial: Device serial number / 设备序列号
        out_dir: Output directory / 输出目录
        name: Name for screenshots / 截图名称
        max_rounds: Maximum rounds of popup checking / 最大弹窗检查轮数
        max_taps: Maximum total taps / 最大总点击数
        settle_s: Wait time after each tap / 每次点击后等待时间
        pre_screenshot: Take screenshot before / 之前截图
        post_screenshot: Take screenshot after / 之后截图
        extra_targets: Additional popup targets / 额外的弹窗目标

    Returns:
        Result dict with tap history / 包含点击历史的结果字典
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    out_root = ensure_abs(out_dir)
    ts = now_dirname()
    safe = safe_name(name or ts)
    serial_part = device_serial or "default"

    before_path = None
    after_path = None
    if pre_screenshot:
        try:
            before_path = str(
                await asyncio.to_thread(
                    save_screenshot_png,
                    device_serial,
                    out_root / "dismiss_popups" / f"{ts}_{serial_part}_{safe}_before.png",
                )
            )
        except Exception:
            pass

    targets = list(POPUP_TARGETS)
    if extra_targets:
        targets.extend(extra_targets)

    taps: list[dict] = []
    max_rounds_i = max(1, min(int(max_rounds), 10))
    max_taps_i = max(1, min(int(max_taps), 20))

    for round_n in range(max_rounds_i):
        if len(taps) >= max_taps_i:
            break

        res = await asyncio.to_thread(dump_ui_xml, device_serial, compressed=True)
        if not (isinstance(res, dict) and res.get("ok") and res.get("xml")):
            break

        xml = str(res.get("xml") or "")
        nodes = parse_uiautomator_nodes(xml)
        if not nodes:
            break

        tapped_this_round = False
        for t in targets:
            if len(taps) >= max_taps_i:
                break
            q = str(t.get("query") or "")
            f = str(t.get("field") or "text")
            ex = bool(t.get("exact", False))
            hits = find_nodes(nodes, query=q, field=f, exact=ex, case_sensitive=False, limit=1)
            if not hits:
                continue
            h = hits[0]
            c = h.center()
            if not c:
                continue
            cx, cy = c
            clicker = Clicker(device_serial=device_serial)
            tap_res = await asyncio.to_thread(clicker.tap, int(cx), int(cy))
            taps.append({
                "round": round_n + 1,
                "target": t,
                "node": h.to_dict(),
                "tap": tap_res,
            })
            tapped_this_round = True
            await asyncio.to_thread(time.sleep, float(settle_s))
            break

        if not tapped_this_round:
            break

    if post_screenshot:
        try:
            after_path = str(
                await asyncio.to_thread(
                    save_screenshot_png,
                    device_serial,
                    out_root / "dismiss_popups" / f"{ts}_{serial_part}_{safe}_after.png",
                )
            )
        except Exception:
            pass

    return {
        "ok": True,
        "device_serial": device_serial,
        "rounds": max_rounds_i,
        "max_taps": max_taps_i,
        "tap_count": len(taps),
        "taps": taps,
        "before_screenshot": before_path,
        "after_screenshot": after_path,
    }


async def find_on_screen_impl(
    device_serial: Optional[str],
    query: str = "",
    field: str = "text_or_desc",
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
    debug_annotate: bool = False,
) -> dict:
    """
    Find UI elements on screen by text/description/resource_id (UIAutomator-based, not OCR).
    通过文字/描述/资源ID在屏幕上查找 UI 元素（基于 UIAutomator，非 OCR）。

    Args / 参数:
    - query: Text to match / 要匹配的内容
    - field: text|desc|text_or_desc|resource_id|class / 搜索字段
    - exact: Exact match vs substring / 精确匹配 vs 子串匹配
    - case_sensitive: Case sensitive matching / 是否区分大小写
    - debug_annotate: Draw boxes on screenshot for debugging / 调试用，画框并保存

    Returns / 返回:
    - matches: List of matches with bounds and center coordinates / 匹配列表，包含边界和中心坐标
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    res = await asyncio.to_thread(dump_ui_xml, device_serial, compressed=True)
    if not (isinstance(res, dict) and res.get("ok") and res.get("xml")):
        return {"ok": False, "device_serial": device_serial, "error": "ui_dump_failed", "detail": res}
    xml = str(res.get("xml") or "")
    nodes = parse_uiautomator_nodes(xml)
    hits = find_nodes(
        nodes,
        query=query,
        field=field,
        exact=bool(exact),
        case_sensitive=bool(case_sensitive),
        limit=int(limit),
    )
    result = {
        "ok": True,
        "device_serial": device_serial,
        "query": query,
        "field": field,
        "exact": bool(exact),
        "case_sensitive": bool(case_sensitive),
        "count": len(hits),
        "matches": [h.to_dict() for h in hits],
    }
    if debug_annotate and hits:
        out_root = ensure_abs(out_dir)
        ts = now_dirname()
        safe = safe_name(name or f"find_on_screen_{query or ts}")
        serial_part = device_serial or "default"
        shot_path = out_root / "find_on_screen_debug" / f"{ts}_{serial_part}_{safe}.png"
        shot_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(save_screenshot_png, device_serial, shot_path)
        boxes = []
        for h in hits:
            b = h.bounds_tuple()
            if not b:
                continue
            x1, y1, x2, y2 = b
            boxes.append(
                {
                    "x": int(x1),
                    "y": int(y1),
                    "w": int(max(1, x2 - x1)),
                    "h": int(max(1, y2 - y1)),
                    "label": (h.text or h.content_desc or h.resource_id or h.class_name),
                }
            )
        ann = await asyncio.to_thread(annotate_boxes_on_image_file, image_path=str(shot_path), boxes=boxes)
        if isinstance(ann, dict) and ann.get("ok") and isinstance(ann.get("annotated_png"), (bytes, bytearray)):
            out_path = out_root / "find_on_screen_debug" / f"{ts}_{serial_part}_{safe}_annotated.png"
            out_path.write_bytes(bytes(ann["annotated_png"]))
            result["annotated_path"] = str(out_path)
    return result


async def launch_from_home_impl(
    device_serial: Optional[str],
    query: str = "",
    out_dir: str = "./recordings",
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
    out_root = ensure_abs(out_dir)

    if reset_home:
        await asyncio.to_thread(reset_to_home, device_serial, presses=int(home_presses))

    # Default: home is paged horizontally
    dirs = directions or ["left", "right"]

    tap_res = await find_and_tap_impl(
        device_serial=device_serial,
        query=query,
        field="text",
        exact=False,
        match_index=0,
        scroll_on_fail=True,
        max_swipes=int(max_swipes),
        directions=dirs,
        stuck_threshold=int(stuck_threshold),
        tap_offset_x=int(tap_offset_x),
        tap_offset_y=int(tap_offset_y),
        pre_screenshot=True,
        post_screenshot=True,
        out_dir=str(out_root),
        name=f"launch_{safe_name(query or 'app')}",
    )
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
        await asyncio.to_thread(append_jsonl, _launch_profiles_index_path(out_root), rec)
        saved = True

    return {"ok": True, "focus": focus, "component": component, "adb_cmd": adb_cmd, "saved": saved, "record": rec}
