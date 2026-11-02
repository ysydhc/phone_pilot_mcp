"""Page State Tools (LLM-Optimized).

MCP tools and helpers for querying page state and tapping elements by index.
Extracted from server.py for modularity.
"""

from __future__ import annotations

import asyncio
import base64
import io
from typing import Any, Optional

from phone_pilot.core import ui_snapshot_cache
from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _resolve_device_serial


PAGE_ELEMENTS_CACHE_TTL_S: float = 30.0


def _cache_page_elements(
    plat: str,
    serial: str,
    include_invisible: bool,
    elements: list[dict],
    scrollable: list[dict],
    texts: list[str],
) -> None:
    """Cache collected page elements with a 30s TTL for subsequent tap_element calls."""
    if not serial:
        return
    ui_snapshot_cache.put_snapshot(
        (plat, serial, "page_elements", bool(include_invisible)),
        (elements, scrollable, texts),
        ttl_s=PAGE_ELEMENTS_CACHE_TTL_S,
    )


def _get_cached_page_elements(
    plat: str,
    serial: str,
    include_invisible: bool,
) -> Optional[tuple[list[dict], list[dict], list[str]]]:
    """Retrieve cached (elements, scrollable, texts) if still valid."""
    if not serial:
        return None
    return ui_snapshot_cache.get_snapshot(
        (plat, serial, "page_elements", bool(include_invisible))
    )


def _encode_screenshot_b64(
    img_bytes: bytes,
    compress: bool = True,
    max_side: int = 1080,
    quality: int = 75,
) -> str:
    """Encode screenshot bytes to base64, optionally downscaling and compressing as JPEG."""
    if not compress:
        return base64.b64encode(img_bytes).decode("ascii")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(img_bytes)) as img:
            rgb_img = img.convert("RGB")
            w, h = rgb_img.size
            if max(w, h) > max_side and max(w, h) > 0:
                scale = max_side / float(max(w, h))
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                resample = getattr(
                    getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC
                )
                rgb_img = rgb_img.resize((new_w, new_h), resample)
            out = io.BytesIO()
            rgb_img.save(out, format="JPEG", quality=quality)
            return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:
        return base64.b64encode(img_bytes).decode("ascii")


def _build_post_action_page_state(
    driver: Any,
    plat: str,
    serial: str,
    include_invisible: bool = False,
) -> dict:
    """Collect post-action page state and refresh the 30s page_elements snapshot."""
    activity = None
    try:
        if hasattr(driver, "ui") and hasattr(driver.ui, "get_current_activity"):
            act = driver.ui.get_current_activity()
            if isinstance(act, dict) or act is None or not hasattr(driver, "screen"):
                activity = act
            else:
                scr_act = driver.screen.get_current_activity()
                activity = scr_act if isinstance(scr_act, dict) else act
        elif hasattr(driver, "screen") and hasattr(driver.screen, "get_current_activity"):
            activity = driver.screen.get_current_activity()
    except Exception:
        try:
            activity = driver.screen.get_current_activity()
        except Exception:
            activity = None

    elements, scrollable, all_texts = _collect_page_elements(
        driver, include_invisible=include_invisible
    )
    _cache_page_elements(
        plat, serial, include_invisible, elements, scrollable, all_texts
    )
    return {
        "activity": activity,
        "elements": elements,
        "element_count": len(elements),
        "elements_count": len(elements),
        "scrollable_areas": scrollable,
        "all_texts": all_texts,
    }


# =============================================================================
# Page State Tool (LLM-Optimized)
# =============================================================================


def _classify_element(class_name: str | None) -> str:
    """Classify a UI element into a human-readable type for LLM consumption."""
    cn = (class_name or "").lower()
    if "edittext" in cn or "textfield" in cn:
        return "input"
    if "checkbox" in cn or "switch" in cn or "togglebutton" in cn:
        return "toggle"
    if "button" in cn or "imagebutton" in cn:
        return "button"
    if "radiobutton" in cn:
        return "radio"
    if "imageview" in cn:
        return "image"
    if "textview" in cn:
        return "text"
    if "recyclerview" in cn or "listview" in cn or "gridview" in cn:
        return "list"
    if "scrollview" in cn or "horizontalscrollview" in cn:
        return "scroll_container"
    if "viewpager" in cn:
        return "pager"
    if "webview" in cn:
        return "webview"
    if "tabwidget" in cn or "tablayout" in cn:
        return "tab"
    if "progressbar" in cn or "seekbar" in cn:
        return "progress"
    if "spinner" in cn:
        return "dropdown"
    return "view"


def _build_element_label(node) -> str:
    """Build a concise display label for a UI node."""
    parts = []
    if node.text:
        parts.append(node.text)
    if node.content_desc and node.content_desc != node.text:
        parts.append(f"[{node.content_desc}]")
    if node.hint and node.hint != node.text:
        parts.append(f"(hint: {node.hint})")
    return " ".join(parts) if parts else ""


def _collect_page_elements(
    driver,
    *,
    include_invisible: bool = False,
) -> tuple[list[dict], list[dict], list[str]]:
    """从 UI 层级中收集交互元素、可滚动区域和全部可见文本。
    Collect interactive elements, scrollable areas and all visible texts from UI hierarchy.

    提取自 phone_get_page_state 的核心逻辑，供多个 MCP 工具复用
    （phone_get_page_state、phone_tap_element 等）。
    Core logic extracted from phone_get_page_state for reuse across MCP tools.

    Args:
        driver: DeviceDriver 实例 / DeviceDriver instance
        include_invisible: 包含不可见元素 / Include invisible elements

    Returns:
        (elements, scrollable_areas, all_texts)
        - elements: 已按视觉位置排序并重新编号的元素列表
        - scrollable_areas: 可滚动区域
        - all_texts: 页面全部可见文本
    """
    # Dump UI hierarchy
    nodes = driver.ui.dump_ui_nodes()

    # Collect all visible texts
    from phone_pilot.core.ui_node import collect_ui_texts

    all_texts = collect_ui_texts(nodes)

    # Build interactive elements list
    elements: list[dict] = []
    scrollable_areas: list[dict] = []

    for node in nodes:
        # Detect scrollable areas
        if node.scrollable:
            b = node.bounds_tuple()
            if b:
                x1, y1, x2, y2 = b
                w_b = max(1, x2 - x1)
                h_b = max(1, y2 - y1)
                direction = "horizontal" if w_b > h_b * 1.2 else "vertical"
                scrollable_areas.append(
                    {
                        "bounds": list(b),
                        "direction": direction,
                        "class": node.class_name or "",
                    }
                )

        # Filter elements: keep those with text/desc, or interactive
        has_text = bool(node.text or node.content_desc or node.hint)
        is_interactive = bool(
            node.clickable
            or node.long_clickable
            or node.checkable
            or node.focusable
            or node.scrollable
        )
        is_input = "edittext" in (node.class_name or "").lower()

        if (
            not include_invisible
            and not has_text
            and not is_interactive
            and not is_input
        ):
            continue

        # Skip pure container nodes with no useful info
        if not has_text and not is_interactive and not is_input:
            cn = (node.class_name or "").lower()
            if any(
                skip in cn
                for skip in [
                    "framelayout",
                    "linearlayout",
                    "relativelayout",
                    "constraintlayout",
                    "coordinatorlayout",
                    "viewgroup",
                ]
            ):
                continue

        center = node.center()
        bounds = node.bounds_tuple()
        if not center:
            continue  # Skip nodes without position

        # Simplify resource_id (remove package prefix)
        rid = node.resource_id or ""
        if ":id/" in rid:
            rid = rid.split(":id/", 1)[1]

        label = _build_element_label(node)
        elem_type = _classify_element(node.class_name)

        elements.append(
            {
                "index": len(elements),
                "type": elem_type,
                "label": label,
                "center": list(center),
                "bounds": list(bounds) if bounds else None,
                "clickable": bool(node.clickable),
                "scrollable": bool(node.scrollable),
                "resource_id": rid,
            }
        )

    # Sort elements by visual position (top-to-bottom, left-to-right)
    elements.sort(key=lambda e: (e["center"][1], e["center"][0]))
    # Re-index after sorting
    for i, elem in enumerate(elements):
        elem["index"] = i

    return elements, scrollable_areas, all_texts


@mcp.tool()
async def phone_get_page_state(
    device_serial: str = "",
    platform: str = "auto",
    include_invisible: bool = False,
    include_screenshot: bool = False,
    annotate_elements: bool = False,
    focus: str = "",
    compress_screenshot: bool = True,
) -> dict:
    """获取当前页面完整状态快照（LLM 最优交互工具）。
    Get complete page state snapshot in one call — optimized for LLM decision-making.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: "android" | "harmony" | "auto" / Platform type
        include_invisible: 是否包含不可见元素，默认 False / Include invisible elements
        include_screenshot: 是否返回 base64 截图，默认 False / Include base64 screenshot
        annotate_elements: 是否返回元素标注截图，默认 False / Include annotated screenshot with element boxes and indices
        focus: 可选聚焦关键词或正则，非空时对返回元素做语义投影压缩 / Optional focus query or regex for semantic projection
        compress_screenshot: 是否对返回截图做缩放与 JPEG 压缩，默认 True / Compress screenshot as JPEG, default True

    Returns / 返回值:
        dict: {
            "ok": True,
            "activity": {"package": str, "activity": str},
            "screen": {"width": int, "height": int},
            "elements": [
                {
                    "index": int,           # 元素编号（用于 phone_tap_element）
                    "type": str,            # 元素类型 (button/input/text/toggle/image/...)
                    "label": str,           # 显示文本/描述
                    "center": [x, y],       # 中心坐标
                    "bounds": [x1,y1,x2,y2],# 边界坐标
                    "clickable": bool,
                    "scrollable": bool,
                    "resource_id": str,     # 资源 ID（精简，去包名前缀）
                }
            ],
            "scrollable_areas": [           # 可滚动区域
                {"bounds": [x1,y1,x2,y2], "direction": "vertical"/"horizontal"}
            ],
            "element_count": int,           # 交互元素总数
            "all_texts": [str],             # 页面所有可见文本
            "screenshot_base64": str | None,          # 仅 include_screenshot=True 时返回
            "screenshot_annotated_base64": str | None # 仅 annotate_elements=True 时返回
        }

    Notes / 特殊逻辑:
        - 一次调用返回页面全部关键信息，避免 LLM 多次往返
        - elements 按视觉位置排序（从上到下、从左到右）
        - 使用 index 引用元素：如 "点击元素 3" → phone_tap_element(index=3)
        - resource_id 自动去除包名前缀（如 com.example:id/btn → btn）
        - 默认只返回可交互或有文本的元素，设置 include_invisible=True 返回全部
        - annotate_elements=True 时自动截图并在图上标注每个元素的编号和边框（自动隐含 screenshot）
    """
    # Resolve device
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial or None, platform
    )
    if err:
        return err

    serial = resolved_serial or ""
    plat = resolved_platform or "android"

    try:
        driver = get_driver(serial, plat)

        # 1. Get current activity
        activity_info = driver.ui.get_current_activity()

        # 2. Get screen size
        try:
            width, height = driver.screen.get_screen_size()
        except Exception:
            width, height = 0, 0

        # 3. Collect elements via shared helper and cache for 30s
        elements, scrollable_areas, all_texts = _collect_page_elements(
            driver, include_invisible=include_invisible
        )
        _cache_page_elements(
            plat, serial, include_invisible, elements, scrollable_areas, all_texts
        )

        # 4. Screenshot (raw and/or annotated)
        screenshot_b64 = None
        annotated_b64 = None
        need_screenshot = include_screenshot or annotate_elements

        png_bytes: bytes | None = None
        if need_screenshot:
            try:
                png_bytes = driver.screen.screenshot()
                if include_screenshot and png_bytes is not None:
                    screenshot_b64 = _encode_screenshot_b64(
                        png_bytes, compress=compress_screenshot
                    )
            except Exception:
                pass

        if annotate_elements and png_bytes:
            try:
                from phone_pilot.extensions.vision.annotate import (
                    annotate_elements_on_screenshot,
                )

                annotated_png = annotate_elements_on_screenshot(png_bytes, elements)
                annotated_b64 = _encode_screenshot_b64(
                    annotated_png, compress=compress_screenshot
                )
            except Exception:
                if screenshot_b64 is None:
                    try:
                        screenshot_b64 = _encode_screenshot_b64(
                            png_bytes, compress=compress_screenshot
                        )
                    except Exception:
                        pass

        result = {
            "ok": True,
            "device_serial": serial,
            "platform": plat,
            "activity": activity_info,
            "screen": {"width": width, "height": height},
            "elements": elements,
            "element_count": len(elements),
            "scrollable_areas": scrollable_areas,
            "all_texts": all_texts,
            "screenshot_base64": screenshot_b64,
            "screenshot_annotated_base64": annotated_b64,
        }
        if focus and focus.strip():
            from phone_pilot.core.ui.projection import project_page_state

            projected = project_page_state(result, focus=focus, compact=True)
            projected["device_serial"] = serial
            projected["platform"] = plat
            projected["screenshot_base64"] = screenshot_b64
            projected["screenshot_annotated_base64"] = annotated_b64
            return projected
        return result

    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": serial}


@mcp.tool()
async def phone_tap_element(
    index: int,
    device_serial: str = "",
    platform: str = "auto",
    wait_ms: int = 500,
    include_invisible: bool = False,
    return_page_state: bool = False,
) -> dict:
    """通过元素编号点击页面元素（配合 phone_get_page_state 使用）。
    Tap a page element by its index from phone_get_page_state.

    Parameters / 参数:
        index: 元素编号（来自 phone_get_page_state 返回的 elements[].index）/ Element index
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: "android" | "harmony" | "auto" / Platform type
        wait_ms: 点击后等待毫秒数，默认 500 / Wait after tap in ms, default 500
        include_invisible: 是否包含不可见元素，默认 False / Include invisible elements
        return_page_state: 点击后是否直接采集并返回新页面状态，默认 False / Return post-tap page state

    Returns / 返回值:
        dict: {"ok": True, "index": int, "center": [x, y], "label": str, "type": str, "snapshot_hit": bool}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 优先复用 30s 内的 phone_get_page_state 元素快照（snapshot_hit=True）
        - 快照未命中或越界时回退实时 dump UI（snapshot_hit=False）
        - index 越界时返回 error="index_out_of_range"
    """
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial or None, platform
    )
    if err:
        return err

    serial = resolved_serial or ""
    plat = resolved_platform or "android"

    try:
        driver = get_driver(serial, plat)

        cached = _get_cached_page_elements(plat, serial, include_invisible)
        if cached is not None and 0 <= index < len(cached[0]):
            elements = cached[0]
            snapshot_hit = True
        else:
            elements, _scrollable, _texts = _collect_page_elements(
                driver, include_invisible=include_invisible
            )
            snapshot_hit = False

        if index < 0 or index >= len(elements):
            return {
                "ok": False,
                "error": "index_out_of_range",
                "index": index,
                "element_count": len(elements),
                "note": f"有效范围 0-{len(elements) - 1}"
                if elements
                else "页面无可交互元素",
            }

        elem = elements[index]
        cx, cy = elem["center"]

        # Tap and immediately invalidate pre-tap UI snapshot
        tap_result = driver.input.tap(cx, cy)
        ui_snapshot_cache.invalidate_device_ui(serial, platform=plat)

        await asyncio.sleep(max(wait_ms, 0) / 1000.0)

        res: dict[str, Any] = {
            "ok": True,
            "device_serial": serial,
            "platform": plat,
            "index": index,
            "center": [cx, cy],
            "label": elem.get("label", ""),
            "type": elem.get("type", "view"),
            "snapshot_hit": snapshot_hit,
        }

        tap_ok = tap_result.get("ok", True) if isinstance(tap_result, dict) else True
        if return_page_state and tap_ok:
            post_state = _build_post_action_page_state(
                driver, plat, serial, include_invisible=include_invisible
            )
            res["page_state"] = post_state
            res["page_state_updated"] = True

        return res

    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": serial}
