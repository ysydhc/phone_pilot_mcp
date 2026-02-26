#!/usr/bin/env python3
"""
Command runner utility for Android automation.

Provides a unified interface for executing shell commands with:
- Configurable timeouts
- Output logging (3 levels: quiet / normal / verbose)
- Post-command delays (to allow device UI to settle)
- Console log tee (optional, for RunSession)

Log levels (env PHONE_PILOT_LOG_LEVEL):
  quiet   — only script step logs & errors
  normal  — (default) friendly summaries for key actions
  verbose — every adb command with full (shortened) path
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import time
from typing import List


# ---------------------------------------------------------------------------
# Log-level singleton
# ---------------------------------------------------------------------------

_LOG_LEVELS = {"quiet": 0, "normal": 1, "verbose": 2}


def _current_log_level() -> int:
    return _LOG_LEVELS.get(os.getenv("PHONE_PILOT_LOG_LEVEL", "normal").lower(), 1)


# ---------------------------------------------------------------------------
# Console log tee — delegated to core.log (platform-agnostic)
# ---------------------------------------------------------------------------

from phone_pilot.core.log import (  # noqa: E402
    _log,
    get_console_log_file,
)


# ---------------------------------------------------------------------------
# Friendly command labels
# ---------------------------------------------------------------------------

def _friendly_cmd(cmd: List[str]) -> str | None:
    """Return a localized friendly label for common adb commands.

    Only return non-None for commands that users care about seeing.
    Internal system queries (dumpsys, uiautomator dump, etc.) are suppressed.
    """
    try:
        s = " ".join(cmd)
    except Exception:
        return None
    if " logcat -c" in s:
        return "清理日志缓存"
    if " input tap " in s:
        # Extract coordinates for readability
        import re as _re
        m = _re.search(r"input tap (\d+) (\d+)", s)
        if m:
            return f"tap ({m.group(1)}, {m.group(2)})"
        return "tap"
    if " input swipe " in s:
        return "swipe"
    if " am start -n " in s:
        return "启动应用"
    if " am force-stop " in s:
        return None  # 由 clear_background_processes 打摘要
    if " am kill-all" in s:
        return None  # 由 clear_background_processes 打摘要
    if " exec-out screencap -p" in s:
        return None  # 截图操作在 step 日志中体现
    return None


def _shorten_cmd(cmd: List[str]) -> str:
    """Replace long tool paths with short names for display.

    /Users/.../platform-tools/adb  →  adb
    /Applications/.../hdc          →  hdc
    """
    parts: list[str] = []
    for c in cmd:
        if os.sep in c:
            base = pathlib.Path(c).name
            if base in ("adb", "hdc"):
                parts.append(base)
                continue
        parts.append(c)
    return " ".join(parts)


class CommandRunner:
    """Execute shell commands with consistent logging and delay handling."""

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
                    (used for internal/system commands the user doesn't need to see)

        Returns:
            subprocess.CompletedProcess with stdout/stderr captured
        """
        # IMPORTANT: In MCP stdio servers, stdout is reserved for protocol messages.
        # Always write logs to stderr to avoid corrupting the transport.
        short = _shorten_cmd(cmd)
        level = _current_log_level()

        if not silent:
            friendly = _friendly_cmd(cmd)
            if level >= 2:
                # verbose: always print shortened command
                if friendly:
                    _log(f"  {friendly} | {short}")
                else:
                    _log(f"  > {short}")
            elif level >= 1 and friendly:
                # normal: only print when there is a friendly label
                _log(f"  {friendly}")
            # quiet (level 0): print nothing
        _clf = get_console_log_file()
        if _clf:
            # Always write full command to console.log for debugging
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
        # Default post-command delay (helps avoid flakiness when the device UI/app is still settling).
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
