"""Recording tools for phone_pilot MCP server.

Provides screen recording functionality:
- phone_start_recording: Start screen recording on a device
- phone_recording_status: Poll recording status (for background starts)
- phone_stop_recording: Stop recording and pull file to local
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import get_driver, _resolve_device_serial
from phone_pilot.mcp._state import (
    _state_lock,
    _recording_state,
    _recording_driver_cache,
    _recording_status,
)


def _phone_start_recording_sync(
    device_serial: Optional[str],
    name: str,
    out_dir: str,
    platform: str,
) -> dict:
    """Sync body for phone_start_recording (run in thread to avoid blocking event loop)."""
    try:
        from phone_pilot.android.adb.utils import ensure_adb_env

        ensure_adb_env()

        # 快速路径：调用方已提供 device_serial 时跳过耗时的 adb devices
        if device_serial and str(device_serial).strip():
            resolved_serial = str(device_serial).strip()
            resolved_platform = (platform or "auto").strip().lower()
            if resolved_platform == "auto":
                resolved_platform = "android"
        else:
            resolved_serial, resolved_platform, err = _resolve_device_serial(
                device_serial, platform
            )
            if err:
                return err

        with _state_lock:
            if resolved_serial in _recording_state:
                return {"ok": False, "error": "already_recording"}

        ts = int(time.time())
        local_path = Path(out_dir) / "recordings" / f"{name}_{ts}.mp4"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        remote_filename = f"phone_pilot_rec_{ts}.mp4"
        driver = get_driver(resolved_serial, resolved_platform or "android")
        result = driver.screen.start_screenrecord(path=remote_filename)

        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error", "start_screenrecord_failed"),
            }

        remote_path = result.get("remote_path") or f"/sdcard/{remote_filename}"
        with _state_lock:
            _recording_state[resolved_serial] = {
                "remote_path": remote_path,
                "local_path": str(local_path),
            }
            _recording_driver_cache[resolved_serial] = driver

        return {
            "ok": True,
            "local_path": str(local_path),
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": str(device_serial or "")}


async def _start_recording_background(
    device_serial: str, name: str, out_dir: str, platform: str
) -> None:
    """后台执行录屏启动，完成后更新 _recording_status（方案 B）."""
    try:
        result = await asyncio.to_thread(
            _phone_start_recording_sync,
            device_serial,
            name,
            out_dir,
            platform,
        )
        if result.get("ok"):
            with _state_lock:
                _recording_status[device_serial] = {
                    "status": "started",
                    "local_path": result.get("local_path", ""),
                }
        else:
            with _state_lock:
                _recording_status[device_serial] = {
                    "status": "failed",
                    "error": result.get("error", "unknown"),
                }
    except Exception as e:
        with _state_lock:
            _recording_status[device_serial] = {
                "status": "failed",
                "error": str(e),
            }


@mcp.tool()
async def phone_start_recording(
    device_serial: str = "",
    name: str = "rec",
    out_dir: str = "./.recordings",
    platform: str = "auto",
) -> dict:
    """开始录屏（方案 B：带 device_serial 时立即返回，后台启动，可轮询 phone_recording_status）。
    Start screen recording on device.

    Parameters / 参数:
        device_serial: 设备序列号；**传入时立即返回**，录屏在后台启动，请轮询 phone_recording_status 至 started 后再调 stop / Device serial; when provided, returns immediately and starts in background
        name: 录屏文件名前缀 / Recording file name prefix
        out_dir: 输出根目录 / Output root directory
        platform: 平台类型 / Platform type

    Returns / 返回值:
        若传入 device_serial: {"ok": True, "status": "starting", "device_serial": str}，请轮询 phone_recording_status 至 started 后调用 phone_stop_recording
        若未传 device_serial: 同步等待结果，{"ok": True, "local_path", "device_serial"} 或 {"ok": False, "error": str}
    """
    serial = (device_serial or "").strip()
    if serial:
        # 方案 B：立即返回，后台执行，避免客户端读超时断开
        with _state_lock:
            if serial in _recording_state:
                return {"ok": False, "error": "already_recording"}
            _recording_status[serial] = {"status": "starting"}
        asyncio.create_task(
            _start_recording_background(serial, name, out_dir, platform)
        )
        return {
            "ok": True,
            "status": "starting",
            "device_serial": serial,
            "message": "录屏已在后台启动，请轮询 phone_recording_status 至 status=started 后调用 phone_stop_recording",
        }
    # 未传 device_serial：保持同步行为，兼容旧调用方
    return await asyncio.to_thread(
        _phone_start_recording_sync,
        None,
        name,
        out_dir,
        platform,
    )


@mcp.tool()
async def phone_recording_status(device_serial: str) -> dict:
    """查询指定设备的录屏状态（方案 B：轮询用）。
    Get recording status for a device (for polling after phone_start_recording).

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial

    Returns / 返回值:
        dict: {"ok": True, "status": "starting"|"started"|"not_recording"|"failed", "local_path"?: str, "error"?: str}
    """
    if not (device_serial and str(device_serial).strip()):
        return {
            "ok": False,
            "error": "device_serial is required",
            "status": "not_recording",
        }
    serial = str(device_serial).strip()
    with _state_lock:
        if serial in _recording_state:
            return {
                "ok": True,
                "status": "started",
                "local_path": _recording_state[serial].get("local_path", ""),
            }
        if serial in _recording_status:
            return {"ok": True, **_recording_status[serial]}
    return {"ok": True, "status": "not_recording"}


def _phone_stop_recording_sync(device_serial: Optional[str], platform: str) -> dict:
    """Sync body for phone_stop_recording (run in thread to avoid blocking event loop)."""
    try:
        from phone_pilot.android.adb.utils import ensure_adb_env

        ensure_adb_env()

        if device_serial and str(device_serial).strip():
            resolved_serial = str(device_serial).strip()
        else:
            resolved_serial, _, err = _resolve_device_serial(device_serial, platform)
            if err:
                return err

        with _state_lock:
            state = _recording_state.pop(resolved_serial, None)
        if not state:
            return {"ok": False, "error": "not_recording"}

        with _state_lock:
            driver = _recording_driver_cache.pop(resolved_serial, None)
        if not driver:
            return {
                "ok": False,
                "error": "not_recording",
                "detail": "no_cached_driver",
            }

        driver.screen.stop_screenrecord()
        driver.pull_file(state["remote_path"], state["local_path"])
        driver.remove_remote_file(state["remote_path"])
        with _state_lock:
            _recording_status.pop(resolved_serial, None)

        return {
            "ok": True,
            "local_path": state["local_path"],
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": str(device_serial or "")}


@mcp.tool()
async def phone_stop_recording(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """停止录屏并拉取文件到本地（方案 B：仅当 phone_recording_status 为 started 时可调用）。
    Stop screen recording and pull file to local.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "local_path": str}
        或 {"ok": False, "error": "recording_still_starting"|"not_recording", "message": str} / {"ok": False, "error": str}
    """
    serial = (device_serial or "").strip()
    if serial:
        with _state_lock:
            in_state = serial in _recording_state
            st = _recording_status.get(serial, {})
        if not in_state:
            # 方案 B：未就绪时直接返回错误，不阻塞
            if st.get("status") == "starting":
                return {
                    "ok": False,
                    "error": "recording_still_starting",
                    "message": "请稍后重试或先调用 phone_recording_status 确认 status=started 后再调用 phone_stop_recording",
                }
            return {
                "ok": False,
                "error": "not_recording",
                "message": "请先调用 phone_start_recording（传入 device_serial）并轮询 phone_recording_status 至 started 后再调用 phone_stop_recording",
            }
    return await asyncio.to_thread(
        _phone_stop_recording_sync, device_serial or None, platform
    )
