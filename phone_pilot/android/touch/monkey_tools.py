#!/usr/bin/env python3
"""
Monkey Tools - Monkey script execution utilities.
Monkey 工具 - Monkey 脚本执行工具。

This module contains Monkey-related functions extracted from mcp_server.py.
本模块包含从 mcp_server.py 抽取的 Monkey 相关函数。
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from phone_pilot.android.device.utils import (
    get_screen_size,
    reset_to_home,
    restart_app,
)
from phone_pilot.core.storage import (
    ensure_abs,
    infer_start_context,
    load_recording_by_key,
    read_json,
    recordings_root,
)
from phone_pilot.android.touch.mks import scale_mks_for_screen
from phone_pilot.android.recording.touch import push_script
from phone_pilot.android.touch.monkey import MonkeyRunner


async def android_push_and_run_monkey(
    device_serial: Optional[str],
    local_mks_path: str,
    remote_script: str = "/data/local/tmp/touch.mks",
    package: Optional[str] = None,
    restore_start_from: Optional[str] = None,
    out_dir: str = "./.recordings",
    force_device_mismatch: bool = False,
    auto_scale_by_screen: bool = True,
    reset_home: bool = True,
    home_presses: int = 2,
    pre_inject_wait_s: float = 2.0,
    throttle_ms: int = 80,
    seed: int = 1234,
    count: int = 1,
) -> dict:
    """
    把本地 `.mks` 推送到手机，并用 `adb shell monkey -f` 执行回放。

    关键能力：
    - 可选：根据某条录制（restore_start_from）恢复起点（回到桌面/重启 App）
    - 设备不一致保护：默认禁止把 A 设备的脚本跑到 B 设备（除非 force_device_mismatch=true）
    - 跨设备分辨率缩放（auto_scale_by_screen=true）：
      - 若源录制保存了 screen_w/screen_h，并且目标设备分辨率不同，会生成一个 scaled mks 再回放

    参数说明（常用）：
    - device_serial: 目标设备；为空时会尝试用录制的设备 serial
    - local_mks_path: 本地 mks 文件路径
    - remote_script: 推送到设备的路径
    - restore_start_from: 录制 key（id/name），用于恢复起点
    - package: monkey -p 过滤包名（可选）
    - throttle_ms/seed/count: monkey 参数

    返回字段会包含：是否缩放、缩放详情、恢复起点信息等。
    """
    local = ensure_abs(local_mks_path)

    # Device mismatch guard:
    # - If replaying a known recording (restore_start_from), compare target device with recorded device.
    # - If pushing a .mks within a recording folder, try reading sibling meta.json for recorded device.
    expected_device: Optional[str] = None
    item: Optional[dict] = None
    if restore_start_from:
        out_root = recordings_root(out_dir)
        item = await asyncio.to_thread(load_recording_by_key, out_root, restore_start_from)
        if item:
            expected_device = item.get("device_serial")
    else:
        meta_path = local.parent / "meta.json"
        if meta_path.exists():
            try:
                expected_device = read_json(meta_path).get("device_serial")
            except Exception:
                expected_device = None

    # If caller didn't specify device, prefer the recorded device (best UX).
    if device_serial is None and expected_device:
        device_serial = expected_device

    if expected_device and device_serial and expected_device != device_serial and not force_device_mismatch:
        return {
            "ok": False,
            "needs_confirm": True,
            "error": "device_mismatch",
            "expected_device_serial": expected_device,
            "target_device_serial": device_serial,
            "hint": "设备不一致：为避免误操作，本次未执行。确认继续请重新调用并传 force_device_mismatch=true。",
            "restore_start_from": restore_start_from,
            "local_mks_path": str(local),
        }

    restored = None
    if restore_start_from:
        out_root = recordings_root(out_dir)
        if item is None:
            item = await asyncio.to_thread(load_recording_by_key, out_root, restore_start_from)
        if item:
            ctx = infer_start_context(item)
            if ctx["type"] == "app" and ctx["package"]:
                await asyncio.to_thread(
                    restart_app,
                    device_serial,
                    package=ctx["package"],
                    component=ctx["component"],
                    deeplink=ctx["deeplink"],
                    force_stop=ctx["force_stop"],
                    wait_s=ctx["wait_s"],
                )
                restored = {
                    "type": "app",
                    "package": ctx["package"],
                    "component": ctx["component"],
                    "deeplink": ctx["deeplink"],
                }
            else:
                await asyncio.to_thread(reset_to_home, device_serial, presses=home_presses)
                restored = {"type": "home"}
            if pre_inject_wait_s and pre_inject_wait_s > 0:
                await asyncio.to_thread(time.sleep, float(pre_inject_wait_s))
        else:
            restored = {"type": "unknown", "error": f"no recording found for key: {restore_start_from}"}
    elif reset_home:
        await asyncio.to_thread(reset_to_home, device_serial, presses=home_presses)
        if pre_inject_wait_s and pre_inject_wait_s > 0:
            await asyncio.to_thread(time.sleep, float(pre_inject_wait_s))

    # Cross-device screen scaling (best-effort). This scales the *already-generated* mks pixel
    # coordinates when replaying on a device with a different screen resolution.
    scaled_by_screen: Optional[dict] = None
    if auto_scale_by_screen and device_serial:
        src_w = src_h = None
        if item:
            src_w = item.get("screen_w")
            src_h = item.get("screen_h")
        if (src_w is None or src_h is None) and local.parent.joinpath("meta.json").exists():
            try:
                meta2 = read_json(local.parent / "meta.json")
                src_w = meta2.get("screen_w")
                src_h = meta2.get("screen_h")
            except Exception:
                src_w = src_h = None

        target_screen = await asyncio.to_thread(get_screen_size, device_serial)
        if (
            target_screen
            and isinstance(src_w, int)
            and isinstance(src_h, int)
            and src_w > 0
            and src_h > 0
            and (target_screen[0] != src_w or target_screen[1] != src_h)
        ):
            scaled_path = local.with_name(f"{local.stem}.scaled_{target_screen[0]}x{target_screen[1]}{local.suffix}")
            scaled_by_screen = await asyncio.to_thread(
                scale_mks_for_screen,
                local,
                scaled_path,
                src_w=src_w,
                src_h=src_h,
                dst_w=target_screen[0],
                dst_h=target_screen[1],
                clamp=True,
            )
            local = scaled_path
    await asyncio.to_thread(push_script, device_serial, local, remote_script)
    await asyncio.to_thread(
        MonkeyRunner.run_monkey, device_serial, package, remote_script, throttle_ms, seed, count
    )
    return {
        "ok": True,
        "device_serial": device_serial,
        "local_mks_path": str(local),
        "remote_script": remote_script,
        "package": package,
        "restore_start_from": restore_start_from,
        "force_device_mismatch": force_device_mismatch,
        "auto_scale_by_screen": auto_scale_by_screen,
        "scaled_by_screen": scaled_by_screen,
        "restored_start": restored,
        "reset_home": reset_home,
        "home_presses": home_presses,
        "pre_inject_wait_s": pre_inject_wait_s,
        "throttle_ms": throttle_ms,
        "seed": seed,
        "count": count,
    }

