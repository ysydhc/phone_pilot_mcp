"""UI element tools for phone_pilot MCP server.

Provides tools for finding UI elements and querying the current foreground
activity on a connected device.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _with_device


def _phone_find_element_sync(
    device_serial: str,
    platform: str,
    text: Optional[str],
    text_contains: Optional[str],
    desc: Optional[str],
    desc_contains: Optional[str],
    resource_id: Optional[str],
    class_name: Optional[str],
    clickable: Optional[bool],
    enabled: Optional[bool],
) -> dict:
    """Sync body for phone_find_element (run in thread to avoid blocking event loop)."""
    try:
        driver = get_driver(device_serial, platform)

        selector: dict[str, Any] = {}
        if text is not None:
            selector["text"] = text
        if text_contains is not None:
            selector["text_contains"] = text_contains
        if desc is not None:
            selector["desc"] = desc
        if desc_contains is not None:
            selector["desc_contains"] = desc_contains
        if resource_id is not None:
            selector["resource_id"] = resource_id
        if class_name is not None:
            selector["class_name"] = class_name
        if clickable is not None:
            selector["clickable"] = clickable
        if enabled is not None:
            selector["enabled"] = enabled

        elements = driver.ui.find_elements(selector)

        return {
            "ok": True,
            "device_serial": device_serial,
            "platform": platform,
            "count": len(elements),
            "elements": elements,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_find_element(
    device_serial: str,
    platform: str = "android",
    text: Optional[str] = None,
    text_contains: Optional[str] = None,
    desc: Optional[str] = None,
    desc_contains: Optional[str] = None,
    resource_id: Optional[str] = None,
    class_name: Optional[str] = None,
    clickable: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> dict:
    """查找匹配条件的 UI 元素。
    Find UI elements matching UIAutomator criteria.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type
        text: 精确文本匹配 / Exact text match
        text_contains: 包含文本匹配 / Partial text match
        desc: content-desc 精确匹配 / Content description exact match
        desc_contains: content-desc 包含匹配 / Content description partial match
        resource_id: 资源 ID 匹配 / Resource ID match
        class_name: 类名匹配 / Class name match
        clickable: 是否可点击 / Is clickable
        enabled: 是否启用 / Is enabled

    Returns / 返回值:
        dict: {"ok": True, "count": int, "elements": [{text, bounds, center_x, center_y, ...}]}
        或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_find_element_sync,
        device_serial,
        platform,
        text,
        text_contains,
        desc,
        desc_contains,
        resource_id,
        class_name,
        clickable,
        enabled,
    )


@mcp.tool()
async def phone_get_current_activity(
    device_serial: str,
    platform: str = "android",
) -> dict:
    """获取当前前台 Activity 信息。
    Get current foreground activity info.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "package", "activity", "device_serial", "platform"}
        或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _with_device,
        lambda s, p: get_driver(s, p).ui.get_current_activity(),
        device_serial,
        platform,
    )
