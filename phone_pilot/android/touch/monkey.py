#!/usr/bin/env python3
"""
Monkey command builder/executor for Android automation.

MonkeyRunner 类用于构建并执行 Android monkey 命令，
主要负责通过 ADB 在指定设备上运行 monkey 测试工具，
可以指定包名、脚本路径、事件数、节流(ms)和种子等参数。
该类通常用于自动化测试及回放，简化命令组织和调用流程。
"""

from __future__ import annotations

import sys
from typing import Optional

from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner


class MonkeyRunner:
    """Execute Monkey scripts on Android devices."""

    @staticmethod
    def run_monkey(
        device: Optional[str],
        package: Optional[str],
        remote_script: str,
        throttle_ms: int,
        seed: int,
        count: int,
    ) -> None:
        """
        Run a Monkey script on the device.

        Args:
            device: Device serial number
            package: Target package name (optional)
            remote_script: Path to script on device
            throttle_ms: Delay between events in milliseconds
            seed: Random seed
            count: Number of events
        """
        # adb shell monkey -p <pkg> -f <file> <count> --throttle ... --seed ...
        cmd = adb_prefix(device) + ["shell", "monkey"]
        if package:
            cmd += ["-p", package]
        cmd += [
            "-f",
            remote_script,
            str(count),
            "--throttle",
            str(throttle_ms),
            "--seed",
            str(seed),
            "--ignore-crashes",
            "--ignore-timeouts",
            "--pct-syskeys",
            "0",
            "--pct-anyevent",
            "0",
        ]
        # IMPORTANT: stderr for logs so MCP stdio transport isn't corrupted.
        print(f"[agent] running monkey command: {' '.join(cmd)}", file=sys.stderr)
        CommandRunner.run(cmd)


__all__ = ["MonkeyRunner"]
