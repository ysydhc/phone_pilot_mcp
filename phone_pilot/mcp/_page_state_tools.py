"""Page State Tools (LLM-Optimized).

MCP tools and helpers for querying page state and tapping elements by index.
Extracted from server.py for modularity.
"""

from __future__ import annotations

import base64

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _resolve_device_serial


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
) -> dict:
    """获取当前页面完整状态快照（LLM 最优交互工具）。
    Get complete page state snapshot in one call — optimized for LLM decision-making.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: "android" | "harmony" | "auto" / Platform type
        include_invisible: 是否包含不可见元素，默认 False / Include invisible elements
        include_screenshot: 是否返回 base64 截图，默认 False / Include base64 screenshot
        annotate_elements: 是否返回元素标注截图，默认 False / Include annotated screenshot with element boxes and indices

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

        # 3. Collect elements via shared helper
        elements, scrollable_areas, all_texts = _collect_page_elements(
            driver, include_invisible=include_invisible
        )

        # 4. Screenshot (raw and/or annotated)
        screenshot_b64 = None
        annotated_b64 = None
        need_screenshot = include_screenshot or annotate_elements

        png_bytes: bytes | None = None
        if need_screenshot:
            try:
                png_bytes = driver.screen.screenshot()
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception:
                pass

        if annotate_elements and png_bytes:
            try:
                from phone_pilot.extensions.vision.annotate import (
                    annotate_elements_on_screenshot,
                )

                annotated_png = annotate_elements_on_screenshot(png_bytes, elements)
                annotated_b64 = base64.b64encode(annotated_png).decode("ascii")
            except Exception:
                pass

        return {
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

    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": serial}


@mcp.tool()
async def phone_tap_element(
    index: int,
    device_serial: str = "",
    platform: str = "auto",
    wait_ms: int = 500,
) -> dict:
    """通过元素编号点击页面元素（配合 phone_get_page_state 使用）。
    Tap a page element by its index from phone_get_page_state.

    Parameters / 参数:
        index: 元素编号（来自 phone_get_page_state 返回的 elements[].index）/ Element index
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: "android" | "harmony" | "auto" / Platform type
        wait_ms: 点击后等待毫秒数，默认 500 / Wait after tap in ms, default 500

    Returns / 返回值:
        dict: {"ok": True, "index": int, "center": [x, y], "label": str, "type": str}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 先 dump UI → 用与 phone_get_page_state 相同的过滤/排序逻辑构建 elements 列表
        - 取 elements[index] 的中心坐标进行点击
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

        # Collect elements with same logic as phone_get_page_state
        elements, _scrollable, _texts = _collect_page_elements(driver)

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

        # Tap
        import time as _time

        driver.input.tap(cx, cy)
        if wait_ms > 0:
            _time.sleep(wait_ms / 1000.0)

        return {
            "ok": True,
            "device_serial": serial,
            "platform": plat,
            "index": index,
            "center": [cx, cy],
            "label": elem.get("label", ""),
            "type": elem.get("type", "view"),
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": serial}
