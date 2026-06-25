"""Navigation & Device Management MCP tools.

Provides phone_go_home, phone_go_back, phone_unlock,
phone_clear_background, phone_clear_data, and phone_open_deeplink.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _with_device, _resolve_device_serial


def _phone_go_home_sync(device_serial: Optional[str], platform: str) -> dict:
    """Sync body for phone_go_home to run in thread (avoid blocking event loop)."""
    if device_serial and str(device_serial).strip():
        resolved = str(device_serial).strip()
        plat = (platform or "auto").strip().lower()
        plat = "android" if plat == "auto" else plat
    else:
        resolved, plat, err = _resolve_device_serial(device_serial, platform)
        if err:
            return err
    if plat == "android":
        from phone_pilot.android.adb.utils import ensure_adb_env

        ensure_adb_env()
    try:
        driver = get_driver(resolved, plat)
        result = driver.go_home()
        result["platform"] = plat
        result["device_serial"] = resolved
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": str(device_serial or "")}


@mcp.tool()
async def phone_go_home(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """返回桌面。
    Navigate to home screen.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(_phone_go_home_sync, device_serial or None, platform)


@mcp.tool()
async def phone_go_back(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """返回上一页。
    Press back button.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _with_device,
        lambda s, p: get_driver(s, p).go_back(),
        device_serial or None,
        platform,
    )


@mcp.tool()
async def phone_unlock(
    device_serial: str = "",
    pin: str = "",
    platform: str = "auto",
) -> dict:
    """解锁设备屏幕。
    Wake and unlock device screen.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        pin: 解锁 PIN 码，空则仅唤醒并滑动解锁 / PIN code, empty for swipe unlock only
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _with_device,
        lambda s, p: get_driver(s, p).unlock(pin=pin or None),
        device_serial or None,
        platform,
    )


@mcp.tool()
async def phone_clear_background(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """清除后台应用进程。
    Clear background apps.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _with_device,
        lambda s, p: get_driver(s, p).clear_background(),
        device_serial or None,
        platform,
    )


@mcp.tool()
async def phone_clear_data(
    package: str,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """清除应用数据。
    Clear application data.

    Parameters / 参数:
        package: 应用包名 / Package name
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "package": str, "platform": str} 或 {"ok": False, "error": str}
    """
    if not package or not package.strip():
        return {"ok": False, "error": "package is required"}
    return await asyncio.to_thread(
        _with_device,
        lambda s, p: (lambda r: {**r, "package": package})(
            get_driver(s, p).app.clear_data(package)
        ),
        device_serial or None,
        platform,
    )


@mcp.tool()
async def phone_open_deeplink(
    uri: str,
    package: str = "",
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """通过深链接打开页面。
    Open a page via deeplink/scheme URI.

    Parameters / 参数:
        uri: 深链接地址 / Deeplink URI (e.g. "myapp://page/detail")
        package: 可选目标应用包名 / Optional target package name
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "uri": str, "platform": str} 或 {"ok": False, "error": str}
    """
    if not uri or not uri.strip():
        return {"ok": False, "error": "uri is required"}
    return await asyncio.to_thread(
        _with_device,
        lambda s, p: (lambda r: {**r, "uri": uri})(
            get_driver(s, p).open_deeplink(uri, package=package or None)
        ),
        device_serial or None,
        platform,
    )
