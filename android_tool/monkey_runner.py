#!/usr/bin/env python3
"""
Monkey command builder/executor for monkey_agent.

# MonkeyRunner 类用于构建并执行 Android monkey 命令，
# 主要负责通过 ADB 在指定设备上运行 monkey 测试工具，
# 可以指定包名、脚本路径、事件数、节流(ms)和种子等参数。
# 该类通常用于自动化测试及回放，简化命令组织和调用流程。


"""

from __future__ import annotations

import sys
from typing import Optional

from android_tool.adb_utils import adb_prefix
from android_tool.runner import CommandRunner


class MonkeyRunner:
    @staticmethod
    def run_monkey(
        device: Optional[str],
        package: Optional[str],
        remote_script: str,
        throttle_ms: int,
        seed: int,
        count: int,
    ) -> None:
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

