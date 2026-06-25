"""
iOS device log capture — background syslog process management.

Mirrors Harmony's `_log_captures` pattern: an output_path → process mapping so
stop_log_capture can find and terminate the right process. Form-factor split
(design ADR-6): real device → `pymobiledevice3 syslog`; simulator →
`xcrun simctl spawn <udid> log stream`.
"""

from __future__ import annotations

import pathlib
import subprocess

from phone_pilot.ios.pmd import utils as pmd_utils

# output_path → {"proc": Popen, "fh": file handle}
_log_captures: dict[str, dict] = {}


def _build_cmd(serial: str, *, level: str) -> list[str]:
    """Build the syslog stream command for the device's form factor."""
    form = pmd_utils.detect_form_factor(serial)
    if form == "simulator":
        exe = pmd_utils.xcrun_executable()
        return [exe, "simctl", "spawn", serial, "log", "stream"]
    # real device (and "unknown" → assume real)
    exe = pmd_utils.pmd_executable()
    cmd = [exe, "syslog"]
    if level:
        cmd += ["--level", level]
    return cmd


def start_log_capture(
    serial: str,
    output_path: str,
    *,
    tags: str = "",
    exclude_tags: str = "",
    level: str = "",
    process: str = "",
) -> dict:
    """
    Start background syslog capture to a file.

    Returns {"ok": True, "pid": int, "path": str} or an error envelope.
    """
    if output_path in _log_captures:
        return {
            "ok": False,
            "error": f"log capture already running for {output_path}",
            "platform": "ios",
        }

    out = pathlib.Path(output_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = _build_cmd(serial, level=level)
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "error": str(exc), "platform": "ios"}

    # Build a grep pipeline for tag/exclude/process filtering (best-effort).
    filters = []
    include = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    excludes = (
        [t.strip() for t in exclude_tags.split(",") if t.strip()]
        if exclude_tags
        else []
    )
    proc_kw = process.strip() if process else ""

    try:
        fh = open(out, "w", encoding="utf-8")
        if include or excludes or proc_kw:
            import re as _re

            shell_cmd = " ".join(cmd)
            if include:
                pat = "|".join(_re.escape(t) for t in include)
                shell_cmd += f" | grep -E '{pat}'"
            for ex in excludes:
                shell_cmd += f" | grep -v '{_re.escape(ex)}'"
            if proc_kw:
                shell_cmd += f" | grep '{_re.escape(proc_kw)}'"
            proc = subprocess.Popen(
                shell_cmd, shell=True, stdout=fh, stderr=subprocess.DEVNULL
            )
            filters = [shell_cmd]
        else:
            proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "platform": "ios"}

    _log_captures[output_path] = {"proc": proc, "fh": fh, "filters": filters}
    return {"ok": True, "pid": proc.pid, "path": output_path}


def stop_log_capture(serial: str, output_path: str) -> dict:
    """Stop background syslog capture for the given output_path; tolerate exit."""
    info = _log_captures.pop(output_path, None)
    if info is None:
        return {
            "ok": False,
            "error": f"no log capture running for {output_path}",
            "platform": "ios",
        }

    proc = info.get("proc")
    fh = info.get("fh")
    try:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
    except (ProcessLookupError, OSError):
        pass
    try:
        if fh is not None:
            fh.close()
    except Exception:
        pass

    lines = 0
    out = pathlib.Path(output_path)
    if out.exists():
        try:
            lines = sum(1 for _ in open(out, encoding="utf-8", errors="replace"))
        except Exception:
            pass
    return {"ok": True, "path": output_path, "lines": lines}


__all__ = ["start_log_capture", "stop_log_capture"]
