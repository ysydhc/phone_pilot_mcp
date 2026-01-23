#!/usr/bin/env python3
"""
Command runner utility for monkey_agent.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import List


class CommandRunner:
    @staticmethod
    def run(
        cmd: List[str],
        *,
        check: bool = True,
        delay_s: float | None = None,
        log_output: bool = True,
        max_log_chars: int = 4000,
        timeout_s: float | None = None,
    ) -> subprocess.CompletedProcess:
        # IMPORTANT: In MCP stdio servers, stdout is reserved for protocol messages.
        # Always write logs to stderr to avoid corrupting the transport.
        print(f"[agent] run: {' '.join(cmd)}", file=sys.stderr)
        try:
            proc = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as e:
            proc = subprocess.CompletedProcess(
                cmd,
                124,
                stdout=(e.stdout or ""),
                stderr=(e.stderr or "") + f"\n[agent] timeout after {timeout_s}s",
            )
        if log_output:
            if proc.stdout:
                out = proc.stdout
                if max_log_chars and len(out) > max_log_chars:
                    out = out[:max_log_chars] + "\n...[truncated stdout]...\n"
                print(out, end="", file=sys.stderr)
            if proc.stderr:
                err = proc.stderr
                if max_log_chars and len(err) > max_log_chars:
                    err = err[:max_log_chars] + "\n...[truncated stderr]...\n"
                print(err, end="", file=sys.stderr)
        # Default post-command delay (helps avoid flakiness when the device UI/app is still settling).
        if delay_s is None:
            try:
                delay_s = float(os.getenv("PHONE_TOUCH_CMD_DELAY_S", "0.4"))
            except ValueError:
                delay_s = 0.4
        if delay_s and delay_s > 0:
            time.sleep(delay_s)
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
            )
        return proc

