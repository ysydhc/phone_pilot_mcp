"""App management MCP tools: launch, force-stop, and list installed packages."""

from __future__ import annotations

import asyncio
from typing import Optional

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _with_device


def _phone_launch_app_sync(
    device_serial: str,
    package: str,
    platform: str,
    activity: Optional[str],
) -> dict:
    """Sync body for phone_launch_app (run in thread to avoid blocking event loop)."""
    if not package or not package.strip():
        return {"ok": False, "error": "package is required"}
    try:
        driver = get_driver(device_serial, platform)
        result = driver.app.launch_app(package, activity)
        result["device_serial"] = device_serial
        result["platform"] = platform
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_launch_app(
    device_serial: str,
    package: str,
    platform: str = "android",
    activity: Optional[str] = None,
) -> dict:
    """启动应用程序。
    Launch an application.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        package: 应用包名 / Package name
        platform: 平台类型 / Platform type
        activity: 可选，指定启动 Activity / Optional Activity to launch

    Returns / 返回值:
        dict: {"ok": True, "package", "device_serial", "platform"} 或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_launch_app_sync, device_serial, package, platform, activity
    )


def _phone_force_stop_sync(device_serial: str, package: str, platform: str) -> dict:
    """Sync body for phone_force_stop to run in thread (avoid blocking event loop)."""
    if not package or not package.strip():
        return {"ok": False, "error": "package is required"}
    from phone_pilot.android.adb.utils import ensure_adb_env

    ensure_adb_env()
    plat = (platform or "android").strip().lower()
    plat = "android" if plat == "auto" else plat
    try:
        driver = get_driver(device_serial.strip(), plat)
        result = driver.app.force_stop(package)
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["package"] = (
            result.get("package") or result.get("PackageName") or package
        )
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_force_stop(
    device_serial: str,
    package: str,
    platform: str = "android",
) -> dict:
    """强制停止应用程序。
    Force stop an application.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        package: 应用包名 / Package name
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "package", "device_serial", "platform"} 或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _phone_force_stop_sync, device_serial, package, platform
    )


@mcp.tool()
async def phone_list_packages(
    device_serial: str,
    platform: str = "android",
    include_system: bool = False,
) -> dict:
    """列出已安装的应用包。
    List installed packages.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type
        include_system: 是否包含系统应用，默认 False / Include system packages, default False

    Returns / 返回值:
        dict: {"ok": True, "count", "packages": [str], "device_serial", "platform"}
        或 {"ok": False, "error": str}
    """

    def _do(s, p):
        pkgs = get_driver(s, p).app.list_packages(include_system=include_system)
        return {"ok": True, "packages": pkgs, "count": len(pkgs)}

    return await asyncio.to_thread(_with_device, _do, device_serial, platform)
