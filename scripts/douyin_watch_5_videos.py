#!/usr/bin/env python3
"""MCP 前置链路验证：通过 phone-pilot-call 快速确认设备/链路可用。

本脚本仅用于 agent 通过 MCP 调用做前置验证，确认环境能跑通。
实际可运行、需随安装包交付给外部项目的脚本是：
  python easy_use/douyin_watch_5_videos.py
该脚本使用 phone_pilot.script_api（与 example_script.py 一致），安装 phone_pilot 后即可运行。

用法（前置验证）:
  python scripts/douyin_watch_5_videos.py   # 调用 MCP 检查设备并打印应运行的脚本路径
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    # 通过 MCP 做最小链路验证：list_devices
    cmd = [
        sys.executable,
        "-m",
        "phone_pilot.mcp_call",
        "phone_list_devices",
        "--args",
        "{}",
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=ROOT,
        )
    except subprocess.TimeoutExpired:
        print("MCP 前置验证超时（phone_list_devices）", file=sys.stderr)
        print("可运行脚本: python easy_use/douyin_watch_5_videos.py", file=sys.stderr)
        return 1
    text = (r.stdout or "").strip()
    if not text:
        print("MCP 前置验证无输出", file=sys.stderr)
        print("可运行脚本: python easy_use/douyin_watch_5_videos.py", file=sys.stderr)
        return 1
    try:
        data = json.loads(text)
        content = data.get("content") or []
        if content and isinstance(content[0], dict):
            inner = json.loads(content[0].get("text") or "{}")
            if inner.get("ok") and inner.get("devices"):
                print("MCP 前置验证通过，设备:", [d.get("serial") for d in inner["devices"]])
            else:
                print("MCP 返回异常:", inner, file=sys.stderr)
    except Exception as e:
        print("MCP 前置验证解析失败:", e, file=sys.stderr)
    print("可运行脚本（交付用）: python easy_use/douyin_watch_5_videos.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
