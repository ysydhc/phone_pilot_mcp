#!/usr/bin/env python3
"""
Convert getevent raw logs into Monkey .mks scripts.

Keeps conversion/scaling logic out of the MCP server glue.

将 getevent 原始日志转换为 Monkey .mks 脚本。

此模块的目的是将转换与坐标缩放等逻辑从 MCP 服务端主流程中分离出来，便于维护和复用。

主要流程如下：
- 解析 getevent 触摸事件日志文件，提取触摸动作（parse_getevent）。
- 获取设备（或指定保持的设备）的屏幕分辨率和触摸屏最大 ABS 坐标值，用于后续坐标缩放。
- 若设备信息可用，则自动将原始 ABS 触摸坐标按屏幕分辨率缩放，确保输出 Monkey 脚本时为像素级可复现坐标，可选地裁剪至屏幕边界。
- 将处理后的动作列表序列化为 Monkey .mks 脚本格式，写入目标输出文件。
- 返回本次转换的相关信息，如动作数、脚本行数、缩放参数、屏幕和触摸硬件信息等，便于追踪和调试。

该模块主要给 MCP 自动化流程、命令行工具等调用，确保格式兼容和可移植性。


"""

from __future__ import annotations

import pathlib
import re
from typing import Any, Dict, Optional

from android_tool.android_device_utils import get_screen_size, get_touch_abs_max
from android_tool.covert_touch import Action, PointerAction, parse_getevent, scale_actions, to_monkey_script


def convert_raw_to_mks(
    raw_log: pathlib.Path,
    mks_out: pathlib.Path,
    keep_device: Optional[str],
    slot: int,
    device_serial: Optional[str],
) -> Dict[str, Any]:
    with open(raw_log, "r", encoding="utf-8") as f:
        actions: list[Action] = list(parse_getevent(f, keep_device, slot))

    # Auto-scale raw ABS coordinates to screen pixels when device is available.
    screen = get_screen_size(device_serial)
    abs_max = get_touch_abs_max(device_serial, keep_device)
    scale_x = 1.0
    scale_y = 1.0
    clamp = None
    if screen and abs_max and abs_max[0] > 0 and abs_max[1] > 0:
        w, h = screen
        max_x, max_y = abs_max
        # Monkey expects pixel coordinates; clamp to visible screen area.
        scale_x = w / max_x
        scale_y = h / max_y
        clamp = (w - 1, h - 1)
        # Scale only pointer actions; keep key actions intact.
        ptr_actions: list[PointerAction] = [a for a in actions if isinstance(a, PointerAction)]
        scaled_ptr = scale_actions(ptr_actions, scale_x=scale_x, scale_y=scale_y, clamp=clamp)
        it = iter(scaled_ptr)
        rebuilt: list[Action] = []
        for a in actions:
            if isinstance(a, PointerAction):
                rebuilt.append(next(it))
            else:
                rebuilt.append(a)
        actions = rebuilt

    lines = to_monkey_script(actions)
    mks_out.parent.mkdir(parents=True, exist_ok=True)
    mks_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pointer_action_count = sum(1 for a in actions if isinstance(a, PointerAction))
    key_action_count = len(actions) - pointer_action_count
    out: Dict[str, Any] = {
        "action_count": len(actions),
        "pointer_action_count": pointer_action_count,
        "key_action_count": key_action_count,
        "line_count": len(lines),
    }
    if screen and abs_max:
        out.update(
            {
                "screen_w": screen[0],
                "screen_h": screen[1],
                "abs_max_x": abs_max[0],
                "abs_max_y": abs_max[1],
                "scale_x": scale_x,
                "scale_y": scale_y,
            }
        )
    return out


_DISPATCH_RE = re.compile(r"^(?P<prefix>\s*DispatchPointer\()(?P<body>.*?)(?P<suffix>\)\s*)$")


def scale_mks_for_screen(
    mks_in: pathlib.Path,
    mks_out: pathlib.Path,
    *,
    src_w: int,
    src_h: int,
    dst_w: int,
    dst_h: int,
    clamp: bool = True,
) -> Dict[str, Any]:
    """
    Scale an existing Monkey .mks (DispatchPointer X/Y) from src screen size to dst screen size.

    This is for cross-device replay compatibility when the source and target screen resolutions differ.
    """
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        raise ValueError("screen size must be positive")

    scale_x = dst_w / src_w
    scale_y = dst_h / src_h

    src = mks_in.read_text(encoding="utf-8")
    out_lines: list[str] = []
    scaled_points = 0

    for raw in src.splitlines():
        m = _DISPATCH_RE.match(raw.strip())
        if not m:
            out_lines.append(raw)
            continue

        # Keep original indentation by using raw's leading whitespace.
        leading = raw[: len(raw) - len(raw.lstrip())]
        body = m.group("body")
        parts = [p.strip() for p in body.split(",")]
        # DispatchPointer(downTime,eventTime,action,x,y,...)
        if len(parts) < 6:
            out_lines.append(raw)
            continue

        try:
            x = float(parts[3])
            y = float(parts[4])
        except ValueError:
            out_lines.append(raw)
            continue

        nx = x * scale_x
        ny = y * scale_y
        if clamp:
            nx = max(0.0, min(float(dst_w - 1), nx))
            ny = max(0.0, min(float(dst_h - 1), ny))

        # Monkey scripts often use one decimal; keep consistent.
        parts[3] = f"{nx:.1f}"
        parts[4] = f"{ny:.1f}"
        out_lines.append(leading + "DispatchPointer(" + ",".join(parts) + ")")
        scaled_points += 1

    mks_out.parent.mkdir(parents=True, exist_ok=True)
    mks_out.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return {
        "scaled_points": scaled_points,
        "src_w": src_w,
        "src_h": src_h,
        "dst_w": dst_w,
        "dst_h": dst_h,
        "scale_x": scale_x,
        "scale_y": scale_y,
    }


