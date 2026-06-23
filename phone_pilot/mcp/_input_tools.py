"""Input tools for the phone_pilot MCP server.

Provides touch/input interaction tools:
- phone_tap: tap at screen coordinates
- phone_tap_text: find a UI element by text and tap it
- phone_swipe: swipe between two coordinates
- phone_long_press: long press at coordinates
- phone_input_text: input text into the focused field
- phone_keyevent: send a key event
"""

from __future__ import annotations

import asyncio
from typing import Any

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _with_device


@mcp.tool()
async def phone_tap(
    device_serial: str,
    x: int,
    y: int,
    platform: str = "android",
    wait_s: float = 0.15,
) -> dict:
    """点击屏幕坐标。
    Tap at screen coordinates.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        x: X 坐标（像素） / X coordinate in pixels
        y: Y 坐标（像素） / Y coordinate in pixels
        platform: 平台类型 / Platform type
        wait_s: 点击后等待秒数，默认 0.15 / Wait seconds after tap, default 0.15

    Returns / 返回值:
        dict: {"ok": True, "x", "y", "platform"} 或 {"ok": False, "error": str}
    """
    if x < 0 or y < 0:
        return {
            "ok": False,
            "error": f"invalid coordinates: x={x}, y={y} (must be >= 0)",
        }

    def _do(s, p):
        result = get_driver(s, p).input.tap(x, y, wait_s=wait_s)
        result["x"] = x
        result["y"] = y
        return result

    return await asyncio.to_thread(_with_device, _do, device_serial, platform)


def _phone_tap_text_sync(
    device_serial: str,
    text: str,
    platform: str,
    match_mode: str,
    index: int,
    wait_s: float,
) -> dict:
    """Sync body for phone_tap_text (run in thread to avoid blocking event loop)."""
    if not text or not text.strip():
        return {
            "ok": False,
            "error": "text is required",
            "device_serial": device_serial,
        }
    try:
        driver = get_driver(device_serial, platform)

        selector: dict[str, Any] = {}
        if match_mode == "exact":
            selector["text"] = text
        else:
            selector["text_contains"] = text

        elements = driver.ui.find_elements(selector)

        if not elements:
            return {
                "ok": False,
                "error": "element_not_found",
                "text": text,
                "match_mode": match_mode,
                "device_serial": device_serial,
            }

        if index < 0 or index >= len(elements):
            return {
                "ok": False,
                "error": "index_out_of_range",
                "text": text,
                "index": index,
                "matched_count": len(elements),
                "device_serial": device_serial,
            }

        elem = elements[index]
        cx = elem.get("center_x") or elem.get("center", [0, 0])[0]
        cy = elem.get("center_y") or elem.get("center", [0, 0])[1]

        driver.input.tap(cx, cy, wait_s=wait_s)

        return {
            "ok": True,
            "text": text,
            "match_mode": match_mode,
            "matched_count": len(elements),
            "index": index,
            "center_x": cx,
            "center_y": cy,
            "element": elem,
            "device_serial": device_serial,
            "platform": platform,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_tap_text(
    device_serial: str,
    text: str,
    platform: str = "android",
    match_mode: str = "contains",
    index: int = 0,
    wait_s: float = 0.5,
) -> dict:
    """通过文本查找 UI 元素并点击（find + tap 合一）。
    Find a UI element by text and tap it.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        text: 要查找的文本 / Text to search for
        platform: 平台类型 / Platform type
        match_mode: "exact" 精确匹配 | "contains" 包含匹配（默认）/ Match mode
        index: 匹配到多个元素时点击第几个（0-based，默认 0）/ Which match to tap
        wait_s: 点击后等待秒数，默认 0.5 / Wait seconds after tap

    Returns / 返回值:
        dict: {"ok": True, "text", "center_x", "center_y", "matched_count", ...}
        或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_tap_text_sync,
        device_serial,
        text,
        platform,
        match_mode,
        index,
        wait_s,
    )


@mcp.tool()
async def phone_swipe(
    device_serial: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    platform: str = "android",
    duration_ms: int = 300,
) -> dict:
    """从 (x1, y1) 滑动到 (x2, y2)。
    Swipe from start coordinates to end coordinates.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        x1, y1: 起始坐标（像素） / Start coordinates in pixels
        x2, y2: 结束坐标（像素） / End coordinates in pixels
        platform: 平台类型 / Platform type
        duration_ms: 滑动持续时间（毫秒），默认 300 / Swipe duration ms, default 300

    Returns / 返回值:
        dict: {"ok": True, "x1", "y1", "x2", "y2", "platform"} 或 {"ok": False, "error": str}
    """
    if any(v < 0 for v in (x1, y1, x2, y2)):
        return {
            "ok": False,
            "error": f"invalid coordinates: ({x1},{y1})->({x2},{y2}) (must be >= 0)",
        }

    def _do(s, p):
        return get_driver(s, p).input.swipe(x1, y1, x2, y2, duration_ms=duration_ms)

    return await asyncio.to_thread(_with_device, _do, device_serial, platform)


@mcp.tool()
async def phone_long_press(
    device_serial: str,
    x: int,
    y: int,
    platform: str = "android",
    duration_ms: int = 500,
) -> dict:
    """在指定坐标长按。
    Long press at coordinates.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        x, y: 坐标（像素） / Coordinates in pixels
        platform: 平台类型 / Platform type
        duration_ms: 按压持续时间（毫秒），默认 500 / Press duration ms, default 500

    Returns / 返回值:
        dict: {"ok": True, "x", "y", "platform"} 或 {"ok": False, "error": str}
    """
    if x < 0 or y < 0:
        return {
            "ok": False,
            "error": f"invalid coordinates: x={x}, y={y} (must be >= 0)",
        }

    def _do(s, p):
        return get_driver(s, p).input.long_press(x, y, duration_ms=duration_ms)

    return await asyncio.to_thread(_with_device, _do, device_serial, platform)


@mcp.tool()
async def phone_input_text(
    device_serial: str,
    text: str,
    platform: str = "android",
    enter: bool = False,
) -> dict:
    """在焦点输入框中输入文本。
    Input text into the focused input field.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        text: 要输入的文本 / Text to input
        platform: 平台类型 / Platform type
        enter: 输入后是否按回车，默认 False / Press Enter after input, default False

    Returns / 返回值:
        dict: {"ok": True, "text", "platform"} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 需先使目标输入框获得焦点（如先点击）
    """
    if not text:
        return {"ok": False, "error": "text is required"}

    def _do(s, p):
        result = get_driver(s, p).input.input_text(text, enter=enter)
        result["text"] = text
        return result

    return await asyncio.to_thread(_with_device, _do, device_serial, platform)


@mcp.tool()
async def phone_keyevent(
    device_serial: str,
    keycode: str,
    platform: str = "android",
) -> dict:
    """发送按键事件。
    Send a key event.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        keycode: 按键代码，如 KEYCODE_HOME / Key code (e.g. KEYCODE_HOME)
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "key", "platform"} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 常用: KEYCODE_HOME(主页), KEYCODE_BACK(返回), KEYCODE_ENTER(确认),
          KEYCODE_VOLUME_UP/DOWN(音量)
    """

    def _do(s, p):
        result = get_driver(s, p).input.keyevent(keycode)
        result["key"] = keycode
        return result

    return await asyncio.to_thread(_with_device, _do, device_serial, platform)
