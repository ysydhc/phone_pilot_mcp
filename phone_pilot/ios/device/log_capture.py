"""
iOS device log capture — background syslog process management.

Mirrors Harmony's `_log_captures` pattern: an output_path → process mapping so
stop_log_capture can find and terminate the right process. Form-factor split
(design ADR-6): real device → `pymobiledevice3 syslog`; simulator →
`xcrun simctl spawn <udid> log stream`.
"""

from __future__ import annotations

import os
import pathlib

from phone_pilot.ios.pmd import utils as pmd_utils

# output_path → {"proc": Popen, "fh": file handle}
_log_captures: dict[str, dict] = {}
_retained_log_captures: dict[str, dict] = {}


def _build_cmd(serial: str, *, level: str, process: str = "") -> list[str]:
    """Build the syslog stream command for the device's form factor."""
    form = pmd_utils.detect_form_factor(serial)
    if form == "simulator":
        exe = pmd_utils.xcrun_executable()
        cmd = [exe, "simctl", "spawn", serial, "log", "stream"]
        lvl = (level or "").strip().upper()
        if lvl in ("D", "DEBUG"):
            cmd += ["--level", "debug"]
        elif lvl in ("I", "INFO"):
            cmd += ["--level", "info"]
        proc_kw = (process or "").strip()
        if proc_kw:
            cmd += ["--predicate", f'process CONTAINS "{proc_kw}"']
        return cmd
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
    if os.name != "posix":
        return {
            "ok": False,
            "error": "log capture requires POSIX process groups",
            "platform": "ios",
        }
    if output_path in _retained_log_captures:
        return {"ok": False, "error": "retained_capture_use_new_path", "path": output_path, "platform": "ios"}
    if output_path in _log_captures:
        return {
            "ok": False,
            "error": f"log capture already running for {output_path}",
            "platform": "ios",
        }

    proc_kw = process.strip() if process else ""
    out = pathlib.Path(output_path)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = _build_cmd(serial, level=level, process=proc_kw)
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "error": str(exc), "platform": "ios"}

    # Build a grep pipeline for tag/exclude/process filtering (best-effort).
    # On simulator, `process` is already filtered natively via `log stream --predicate`.
    is_sim_predicate = "--predicate" in cmd
    filters = []
    include = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    excludes = (
        [t.strip() for t in exclude_tags.split(",") if t.strip()]
        if exclude_tags
        else []
    )
    need_proc_grep = bool(proc_kw and not is_sim_predicate)

    import re
    from phone_pilot.core.capture_pipeline import start_pipeline

    if include:
        filters.append(["grep", "--line-buffered", "-E", "|".join(re.escape(t) for t in include)])
    for ex in excludes:
        filters.append(["grep", "--line-buffered", "-v", re.escape(ex)])
    if need_proc_grep:
        filters.append(["grep", "--line-buffered", re.escape(proc_kw)])
    fh = None
    try:
        fh = open(out, "w", encoding="utf-8")
        info = start_pipeline(cmd, filters, fh)
    except Exception as exc:
        if fh is not None:
            fh.close()
        return {"ok": False, "error": str(exc), "platform": "ios"}
    _log_captures[output_path] = info
    proc = info["proc"]
    return {"ok": True, "pid": proc.pid, "path": output_path}


def stop_log_capture(serial: str, output_path: str) -> dict:
    """Stop background syslog capture for the given output_path; tolerate exit."""
    info = _log_captures.get(output_path)
    if info is None:
        return {
            "ok": False,
            "error": f"no log capture running for {output_path}",
            "platform": "ios",
        }

    from phone_pilot.core.capture_pipeline import stop_pipeline

    result = stop_pipeline(info)
    if not result.get("ok"):
        if result.get("terminal") is True and result.get("stopped") is True:
            _retained_log_captures[output_path] = info
            _log_captures.pop(output_path)
        return {**result, "path": output_path, "platform": "ios"}
    _log_captures.pop(output_path)

    lines = 0
    out = pathlib.Path(output_path)
    if out.exists():
        try:
            lines = sum(1 for _ in open(out, encoding="utf-8", errors="replace"))
        except Exception:
            pass
    return {"ok": True, "path": output_path, "lines": lines}


__all__ = ["start_log_capture", "stop_log_capture"]
