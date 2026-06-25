"""
iOS pmd runner — pymobiledevice3 / xcrun simctl / idevicesyslog subprocess wrapper.

Mirrors harmony/hdc/runner.py: a thin, well-behaved subprocess wrapper that
never raises on non-zero exit (callers inspect returncode), captures text
output, and maps timeouts to returncode 124.
"""

from __future__ import annotations

import subprocess
from typing import List, Optional


def run(
    cmd: List[str],
    *,
    timeout_s: Optional[float] = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a pmd / simctl / idevice* command and return the CompletedProcess.

    Args:
        cmd: Command and arguments as a list.
        timeout_s: Optional command timeout in seconds (maps to returncode 124).
        check: If True, raise CalledProcessError on non-zero exit (default False).

    Returns:
        subprocess.CompletedProcess with text stdout/stderr captured.
        On timeout, returncode is 124.
    """
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as e:
        proc = subprocess.CompletedProcess(
            cmd,
            124,
            stdout=(e.stdout or "") if isinstance(e.stdout, str) else "",
            stderr=((e.stderr or "") if isinstance(e.stderr, str) else "")
            + f"\n[ios] timeout after {timeout_s}s",
        )

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
        )
    return proc


__all__ = ["run"]
