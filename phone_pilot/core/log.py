"""平台无关的日志工具 / Platform-agnostic logging utilities.

提供统一的控制台输出和可选的文件 tee 功能，供 script_api 和其他模块使用。
Provides unified console output and optional file tee, used by script_api and other modules.
"""

from __future__ import annotations

import os
import sys
from typing import IO, Optional


# ---------------------------------------------------------------------------
# Log-level singleton / 日志级别
# ---------------------------------------------------------------------------

_LOG_LEVELS = {"quiet": 0, "normal": 1, "verbose": 2}


def _current_log_level() -> int:
    """当前日志级别 / Current log level (0=quiet, 1=normal, 2=verbose)."""
    return _LOG_LEVELS.get(os.getenv("PHONE_PILOT_LOG_LEVEL", "normal").lower(), 1)


# ---------------------------------------------------------------------------
# Console log tee / 控制台日志 tee
# ---------------------------------------------------------------------------

_console_log_file: Optional[IO[str]] = None


def set_console_log_file(f: Optional[IO[str]]) -> None:
    """Called by RunSession to tee all runner output to a file.
    由 RunSession 调用，将所有输出同步写入文件。
    """
    global _console_log_file
    _console_log_file = f


def get_console_log_file() -> Optional[IO[str]]:
    """Return current console log file (for direct tee in command runners).
    返回当前控制台日志文件（供命令执行器直接 tee）。
    """
    return _console_log_file


def _log(msg: str, *, end: str = "\n") -> None:
    """Write to stderr AND optionally to the console log file.
    向 stderr 输出，同时可选写入控制台日志文件。
    """
    print(msg, end=end, file=sys.stderr)
    if _console_log_file:
        try:
            _console_log_file.write(msg + end)
            _console_log_file.flush()
        except Exception:
            pass
