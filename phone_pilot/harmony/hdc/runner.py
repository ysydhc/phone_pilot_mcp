#!/usr/bin/env python3
"""
Command runner utility for HarmonyOS HDC automation.

Provides a unified interface for executing HDC commands with:
- Configurable timeouts
- Output logging (3 levels: quiet / normal / verbose)
- Post-command delays (to allow device UI to settle)
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import List

# Reuse shared logging infra from core + Android runner.
from phone_pilot.core.log import _log, get_console_log_file
from phone_pilot.android.adb.runner import (
    _current_log_level,
    _shorten_cmd,
)


def _friendly_cmd(cmd: List[str]) -> str | None:
    """Return a localized friendly label for common HDC commands."""
    try:
        s = " ".join(cmd)
    except Exception:
        return None
    if " shell input tap " in s:
        return "点击屏幕坐标"
    if " shell input swipe " in s:
        return "滑动屏幕"
    if " shell screencap -p" in s:
        return "截取屏幕"
    if " shell aa start" in s:
        return "启动应用"
    if " shell aa force-stop" in s or " shell aa stop" in s:
        return "结束应用"
    if " shell bm dump" in s:
        return "列出应用包信息"
    return None


class HdcCommandRunner:
    """Execute HDC commands with consistent logging and delay handling."""

    @staticmethod
    def run(
        cmd: List[str],
        *,
        check: bool = True,
        delay_s: float | None = None,
        log_output: bool = True,
        max_log_chars: int = 4000,
        timeout_s: float | None = None,
        silent: bool = False,
    ) -> subprocess.CompletedProcess:
        """
        Run a shell command and return the result.

        Args:
            cmd: Command and arguments as a list
            check: If True, raise CalledProcessError on non-zero exit
            delay_s: Post-command delay (defaults to PHONE_PILOT_CMD_DELAY_S env var or 0.4s)
            log_output: If True, log stdout/stderr to stderr
            max_log_chars: Maximum characters to log (truncate if exceeded)
            timeout_s: Command timeout in seconds
            silent: If True, suppress ALL console output for this command

        Returns:
            subprocess.CompletedProcess with stdout/stderr captured
        """
        short = _shorten_cmd(cmd)
        level = _current_log_level()

        if not silent:
            friendly = _friendly_cmd(cmd)
            if level >= 2:
                if friendly:
                    _log(f"  {friendly} | {short}")
                else:
                    _log(f"  > {short}")
            elif level >= 1 and friendly:
                _log(f"  {friendly}")
        _clf = get_console_log_file()
        if _clf:
            try:
                _clf.write(f"[cmd] {short}\n")
                _clf.flush()
            except Exception:
                pass

        try:
            proc = subprocess.run(
                cmd, check=False, text=True, capture_output=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired as e:
            proc = subprocess.CompletedProcess(
                cmd,
                124,
                stdout=(e.stdout or ""),
                stderr=(e.stderr or "") + f"\n[agent] timeout after {timeout_s}s",
            )
        if log_output and not silent:
            if proc.stdout:
                out = proc.stdout
                if max_log_chars and len(out) > max_log_chars:
                    out = out[:max_log_chars] + "\n...[truncated]...\n"
                _log(out, end="")
            if proc.stderr:
                err = proc.stderr
                if max_log_chars and len(err) > max_log_chars:
                    err = err[:max_log_chars] + "\n...[truncated]...\n"
                _log(err, end="")
        if delay_s is None:
            try:
                delay_s = float(os.getenv("PHONE_PILOT_CMD_DELAY_S", "0.4"))
            except ValueError:
                delay_s = 0.4
        if delay_s and delay_s > 0:
            time.sleep(delay_s)
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(
                proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
            )
        return proc


__all__ = ["HdcCommandRunner"]
