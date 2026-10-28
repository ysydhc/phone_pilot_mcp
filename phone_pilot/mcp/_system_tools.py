"""System & Device Info MCP tools.

Covers clipboard, notifications, WiFi, airplane mode, shell execution, and
device info.  All tools in this module are Android-only unless noted.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _resolve_device_serial
from phone_pilot.android.device.utils import (
    execute_shell,
    get_clipboard_text,
    get_device_model_and_version,
    get_notifications,
    set_airplane_mode,
    set_wifi_enabled,
)


@mcp.tool()
async def phone_read_clipboard(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """读取设备剪贴板文本。
    Read device clipboard text.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "text": str} 或 {"ok": False, "error": str}
    支持 Android、iOS 与 HarmonyOS / Supports Android, iOS, and HarmonyOS.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat == "ios":
        from phone_pilot.ios.system import get_clipboard

        result = await asyncio.to_thread(get_clipboard, resolved)
        result["device_serial"] = resolved
        result["platform"] = plat
        return result
    if plat == "harmony":
        from phone_pilot.harmony.device.utils import (
            get_clipboard_text as get_harmony_clipboard_text,
        )

        result = await asyncio.to_thread(get_harmony_clipboard_text, resolved)
        result["device_serial"] = resolved
        result["platform"] = plat
        return result
    if plat != "android":
        return {
            "ok": False,
            "error": "phone_read_clipboard supports Android, iOS, and HarmonyOS",
            "platform": plat,
        }
    result = await asyncio.to_thread(get_clipboard_text, resolved)
    result["device_serial"] = resolved
    result["platform"] = plat
    return result


@mcp.tool()
async def phone_get_notifications(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """获取通知栏通知列表。
    Get notification list from notification bar.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "notifications": [{"package": str, "title": str, "text": str}]}
    支持 Android 与 HarmonyOS / Supports Android and HarmonyOS.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat == "harmony":
        from phone_pilot.harmony.device.utils import (
            get_notifications as get_harmony_notifications,
        )

        result = await asyncio.to_thread(get_harmony_notifications, resolved)
        result["device_serial"] = resolved
        result["platform"] = plat
        return result
    if plat != "android":
        return {
            "ok": False,
            "error": "phone_get_notifications only supports Android and HarmonyOS",
            "platform": plat,
        }
    result = await asyncio.to_thread(get_notifications, resolved)
    result["device_serial"] = resolved
    result["platform"] = plat
    return result


@mcp.tool()
async def phone_toggle_wifi(
    enabled: bool = True,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """开关 WiFi。
    Toggle WiFi on/off.

    Parameters / 参数:
        enabled: True 开启 / False 关闭 / True to enable, False to disable
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "wifi_on": bool} 或 {"ok": False, "error": str}
    支持 Android 与 HarmonyOS / Supports Android and HarmonyOS.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat == "harmony":
        from phone_pilot.harmony.device.utils import (
            set_wifi_enabled as set_harmony_wifi_enabled,
        )

        result = await asyncio.to_thread(set_harmony_wifi_enabled, resolved, enabled)
        result["device_serial"] = resolved
        result["platform"] = plat
        return result
    if plat != "android":
        return {
            "ok": False,
            "error": "phone_toggle_wifi only supports Android and HarmonyOS",
            "platform": plat,
        }
    result = await asyncio.to_thread(set_wifi_enabled, resolved, enabled)
    result["device_serial"] = resolved
    result["platform"] = plat
    return result


@mcp.tool()
async def phone_toggle_airplane(
    enabled: bool = True,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """开关飞行模式。
    Toggle airplane mode.

    Parameters / 参数:
        enabled: True 开启 / False 关闭 / True to enable, False to disable
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "airplane_on": bool} 或 {"ok": False, "error": str}
    支持 Android 与 HarmonyOS / Supports Android and HarmonyOS.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat == "harmony":
        from phone_pilot.harmony.device.utils import (
            set_airplane_mode as set_harmony_airplane_mode,
        )

        result = await asyncio.to_thread(set_harmony_airplane_mode, resolved, enabled)
        result["device_serial"] = resolved
        result["platform"] = plat
        return result
    if plat != "android":
        return {
            "ok": False,
            "error": "phone_toggle_airplane only supports Android and HarmonyOS",
            "platform": plat,
        }
    result = await asyncio.to_thread(set_airplane_mode, resolved, enabled)
    result["device_serial"] = resolved
    result["platform"] = plat
    return result


@mcp.tool()
async def phone_execute_shell(
    command: str,
    device_serial: str = "",
    timeout_s: float = 30.0,
    platform: str = "auto",
) -> dict:
    """执行受限 shell 命令（白名单限制）。
    Execute restricted shell command (whitelist limited).

    Parameters / 参数:
        command: shell 命令 / Shell command
        device_serial: 设备序列号 / Device serial
        timeout_s: 超时秒数 / Timeout in seconds
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "stdout": str, "stderr": str, "returncode": int}
        或 {"ok": False, "error": str}
    支持 Android / iOS / HarmonyOS.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat == "ios":
        from phone_pilot.ios.system import execute_shell as ios_execute_shell

        result = await asyncio.to_thread(
            ios_execute_shell, resolved, command, timeout_s
        )
        result["device_serial"] = resolved
        result["platform"] = plat
        return result
    if plat == "harmony":
        from phone_pilot.harmony.device.utils import (
            execute_shell as harmony_execute_shell,
        )

        result = await asyncio.to_thread(
            harmony_execute_shell, resolved, command, timeout_s
        )
        result["device_serial"] = resolved
        result["platform"] = plat
        return result
    if plat != "android":
        return {
            "ok": False,
            "error": "phone_execute_shell supports Android, iOS, and HarmonyOS",
            "platform": plat,
        }
    result = await asyncio.to_thread(execute_shell, resolved, command, timeout_s)
    result["device_serial"] = resolved
    return result


def _phone_get_device_info_sync(device_serial: Optional[str], platform: str) -> dict:
    """Sync body for phone_get_device_info (run in thread to avoid blocking event loop)."""
    resolved, plat, err = _resolve_device_serial(device_serial, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat or "android")
        try:
            width, height = driver.screen.get_screen_size()
            screen = {"width": width, "height": height}
        except Exception:
            screen = {"width": 0, "height": 0}
        try:
            activity = driver.ui.get_current_activity()
        except Exception:
            activity = {"package": None, "activity": None}
        model = ""
        os_version = ""
        if plat == "android":
            model, os_version = get_device_model_and_version(resolved)
        elif plat == "harmony":
            try:
                info = driver.device_info()
                if info.get("ok"):
                    model = info.get("model", "")
                    os_version = info.get("sysVersion", "")
            except Exception:
                pass
            if not model or not os_version:
                from phone_pilot.harmony.device.utils import (
                    get_device_model_and_version as get_harmony_model_and_version,
                )

                m, v = get_harmony_model_and_version(resolved)
                model = model or m
                os_version = os_version or v
        elif plat == "ios":
            try:
                info = driver.device_info()
                if info.get("ok"):
                    model = info.get("model", "")
                    os_version = info.get("os_version", "")
            except Exception:
                pass
        return {
            "ok": True,
            "screen": screen,
            "activity": activity,
            "model": model,
            "os_version": os_version,
            "device_serial": resolved,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_get_device_info(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """获取轻量设备信息。
    Get lightweight device info.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "screen": {"width": int, "height": int}, "activity": {...}, "model": str, "os_version": str}
    """
    return await asyncio.to_thread(
        _phone_get_device_info_sync, device_serial or None, platform
    )
