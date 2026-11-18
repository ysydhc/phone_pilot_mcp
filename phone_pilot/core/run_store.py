#!/usr/bin/env python3
"""
RunSession — 管理单次脚本运行的全部产物。

目录结构::

    .recordings/runs/<script>_<git>_<ts>/
    ├── run_meta.json          # 运行元信息
    ├── steps.json             # 全部步骤记录（单文件）
    ├── script.py              # 执行脚本的副本
    ├── git_info.json          # git 上下文
    ├── git_diff.patch         # 未提交的变更（若有）
    ├── console.log            # 完整控制台输出
    ├── screenshots/           # 截图（步骤自动截图 + 手动截图）
    │   ├── 001_find_image.png
    │   └── ...
    ├── recordings/            # 录屏文件
    │   └── ad_playback.mp4
    ├── logcat/                # logcat 捕获
    │   └── ad_verify.txt
    └── meminfo/               # 内存快照与分析
        ├── before.json
        ├── after.json
        └── diff.json
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import tempfile
from phone_pilot.core.safe_io import safe_directory, normalize_trusted_aliases
import subprocess
import time
import uuid

from phone_pilot.core.business_report import BusinessReport
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _git_info(cwd: Optional[str] = None) -> dict:
    """Collect git context from the working directory. Never raises."""
    info: dict[str, Any] = {
        "commit": None,
        "commit_full": None,
        "branch": None,
        "dirty": False,
        "dirty_files": [],
        "dirty_files_count": 0,
        "has_patch": False,
    }
    kw = {
        "cwd": cwd,
        "check": False,
        "capture_output": True,
        "text": True,
        "timeout": 2,
        "stdin": subprocess.DEVNULL,
    }
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], **kw)
        if r.returncode == 0:
            info["commit"] = (r.stdout or "").strip()
        r2 = subprocess.run(["git", "rev-parse", "HEAD"], **kw)
        if r2.returncode == 0:
            info["commit_full"] = (r2.stdout or "").strip()
        r3 = subprocess.run(["git", "branch", "--show-current"], **kw)
        if r3.returncode == 0:
            info["branch"] = (r3.stdout or "").strip() or None
        r4 = subprocess.run(["git", "status", "--porcelain"], **kw)
        if r4.returncode == 0:
            lines = [ln for ln in (r4.stdout or "").splitlines() if ln.strip()]
            if lines:
                info["dirty"] = True
                info["dirty_files"] = lines[:200]
                info["dirty_files_count"] = len(lines)
    except Exception:
        pass
    return info


def _git_diff_patch(cwd: Optional[str] = None) -> Optional[str]:
    """Return `git diff HEAD` output, or None."""
    try:
        r = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
            stdin=subprocess.DEVNULL,
        )
        patch = (r.stdout or "").strip()
        return patch if patch else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Safe naming
# ---------------------------------------------------------------------------

_UNSAFE_RE = re.compile(r"[^\w\-]+")


def _safe(name: str, max_len: int = 60) -> str:
    return _UNSAFE_RE.sub("_", name.strip()).strip("_")[:max_len] or "script"


# ---------------------------------------------------------------------------
# Recordings root resolver
# ---------------------------------------------------------------------------


def run_recordings_root(recordings_dir: Optional[str] = None) -> pathlib.Path:
    """Resolve the .recordings root for run artifacts.

    Priority:
    1. Explicit ``recordings_dir`` parameter
    2. ``PHONE_PILOT_RECORDINGS_DIR`` environment variable
    3. ``<cwd>/.recordings``
    """
    if recordings_dir:
        return normalize_trusted_aliases(recordings_dir)
    env = os.getenv("PHONE_PILOT_RECORDINGS_DIR")
    if env:
        return normalize_trusted_aliases(env)
    return normalize_trusted_aliases(pathlib.Path.cwd() / ".recordings")


# ---------------------------------------------------------------------------
# RunSession
# ---------------------------------------------------------------------------


class RunSession:
    """Manages artifacts for a single script run."""

    def __init__(self, run_dir: pathlib.Path, *, script_name: str = ""):
        run_dir = normalize_trusted_aliases(run_dir)
        self.run_dir = run_dir
        self.script_name = script_name
        self.screenshots_dir = run_dir / "screenshots"
        self.recordings_dir = run_dir / "recordings"
        self.logcat_dir = run_dir / "logcat"
        self.meminfo_dir = run_dir / "meminfo"
        self.started_at = _dt.datetime.now().astimezone()
        self._monotonic_start_ns = time.monotonic_ns()
        self._business = BusinessReport(run_dir)
        self._console_log_fh = None
        self._console_capture_path = None
        self._console_capture_error = ""
        self._console_capture_incomplete = False
        self._git_info: dict = {}
        self._device_info: dict = {}
        self._script_path: Optional[str] = None
        # Consolidated steps list (written to steps.json on finalize)
        self._steps: list[dict] = []

    # ---- Factory ----

    @classmethod
    def create(
        cls,
        script_name: str,
        *,
        script_path: Optional[str] = None,
        recordings_dir: Optional[str] = None,
        device_serial: Optional[str] = None,
        platform: str = "android",
        git_cwd: Optional[str] = None,
    ) -> "RunSession":
        """Create a new run session with directory, git info, and console log."""
        root = run_recordings_root(recordings_dir)
        runs_dir = root / "runs"
        with safe_directory(runs_dir, create=True, writable=True):
            pass

        ginfo = _git_info(git_cwd)
        commit = ginfo.get("commit") or "nogit"
        dirty_tag = "_dirty" if ginfo.get("dirty") else ""
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = (
            f"{_safe(script_name)}_{commit}{dirty_tag}_{ts}_{uuid.uuid4().hex[:8]}"
        )

        run_dir = runs_dir / folder_name
        with safe_directory(run_dir, create=True, writable=True):
            pass

        session = cls(run_dir, script_name=script_name)
        session._git_info = ginfo
        session._script_path = script_path
        session._device_info = {"serial": device_serial, "platform": platform}

        # Create subdirs
        with safe_directory(run_dir, writable=True) as folder:
            for name in ("screenshots", "recordings", "logcat", "meminfo"):
                with folder.subdir(name, create=True):
                    pass

        # Save git info (commit + dirty); diff in background thread
        _write_json(run_dir / "git_info.json", ginfo)

        def _write_diff_async() -> None:
            try:
                patch = _git_diff_patch(git_cwd)
                if patch and ginfo.get("dirty"):
                    with safe_directory(run_dir, writable=True) as folder:
                        folder.write_bytes(
                            "git_diff.patch", patch.encode("utf-8"), replace=True
                        )
            except Exception:
                pass

        if ginfo.get("dirty"):
            import threading

            _t = threading.Thread(target=_write_diff_async, daemon=True)
            _t.start()

        # Copy script source
        if script_path:
            src = pathlib.Path(script_path).expanduser().absolute()
            if src.exists():
                try:
                    with safe_directory(run_dir, writable=True) as folder:
                        folder.copy_file("script.py", src)
                except Exception:
                    pass

        # Open console log and tee
        try:
            # Driver/tee output never opens an untrusted archive pathname.
            session._console_log_fh = tempfile.NamedTemporaryFile(
                mode="w+", encoding="utf-8", prefix="pp-console-", delete=False
            )
            session._console_capture_path = normalize_trusted_aliases(
                session._console_log_fh.name
            )
            from phone_pilot.core.log import set_console_log_file

            set_console_log_file(session._console_log_fh)
        except Exception as exc:
            session._console_capture_error = f"console initialization failed: {exc}"
            session._console_capture_incomplete = True

        return session

    # ---- Business acceptance (independent of tool counters / exit code) ----

    def declare_cases(self, cases: list[dict]) -> None:
        """Declare cases/steps and all required evidence before executing them."""
        self._business.declare_cases(cases)

    def begin_business_step(
        self, case_id: str, business_step_id: str, **kwargs
    ) -> dict:
        """Begin a declared attempt; tool calls inherit this explicit parent."""
        return self._business.begin_business_step(case_id, business_step_id, **kwargs)

    def end_business_step(
        self, status: str = "PASS", actual: str = "", **kwargs
    ) -> dict:
        """Finish an attempt, downgrading PASS when required evidence is missing."""
        return self._business.end_business_step(status, actual, **kwargs)

    def add_evidence(self, kind: str, **kwargs) -> dict:
        """Add package-local evidence (or an explicit capture_error)."""
        return self._business.add_evidence(kind, **kwargs)

    def add_assertion(
        self, dimension: str, status: str, expected: str, actual: str, **kwargs
    ) -> dict:
        """Record an assertion with explicit evidence references."""
        return self._business.add_assertion(
            dimension, status, expected, actual, **kwargs
        )

    @property
    def business_report(self) -> dict:
        """Return a freshly integrity-checked business snapshot."""
        return self._business.snapshot()

    # ---- Step recording (consolidated) ----

    def add_step(
        self,
        step_num: int,
        action: str,
        status: str = "ok",
        detail: str = "",
        *,
        png_bytes: Optional[bytes] = None,
        extra: Optional[dict] = None,
    ) -> None:
        """Record a step into the in-memory list. Screenshot saved to screenshots/."""
        screenshot_name: Optional[str] = None
        if png_bytes:
            screenshot_name = self.save_screenshot(
                f"{step_num:03d}_{_safe(action, 30)}", png_bytes
            ).name

        entry: dict[str, Any] = {
            "step": step_num,
            "action": action,
            "timestamp": _dt.datetime.now()
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "tool_call_id": f"tool-{uuid.uuid4().hex}",
            "status": status,
            "detail": detail,
            "screenshot": screenshot_name,
        }
        if extra:
            entry.update(extra)
        parent = self._business.active
        if parent:
            entry.update(
                parent_case_id=parent["case_id"],
                parent_step_id=parent["business_step_id"],
                parent_attempt_id=parent["attempt_id"],
            )
        self._steps.append(entry)

    def save_screenshot(self, name: str, png_bytes: bytes) -> pathlib.Path:
        """Save a named screenshot to screenshots/."""
        p = self.screenshots_dir / f"{_safe(name, 60)}_{uuid.uuid4().hex}.png"
        with safe_directory(self.run_dir, writable=True) as root:
            with root.subdir("screenshots", create=True) as folder:
                folder.write_bytes(p.name, png_bytes)
        return p

    def save_logcat(self, name: str, text: str) -> pathlib.Path:
        """Save logcat dump to logcat/ subdir."""
        p = self.logcat_dir / f"{_safe(name, 60)}.txt"
        with safe_directory(p.parent, writable=True) as folder:
            folder.write_bytes(p.name, text.encode("utf-8"), replace=True)
        return p

    def save_recording(self, name: str, data: bytes) -> pathlib.Path:
        """Save a screen recording file to recordings/."""
        p = self.recordings_dir / f"{_safe(name, 60)}.mp4"
        with safe_directory(p.parent, writable=True) as folder:
            folder.write_bytes(p.name, data, replace=True)
        return p

    def save_meminfo(self, name: str, data: dict) -> pathlib.Path:
        """Save a meminfo snapshot or diff to meminfo/ as JSON."""
        p = self.meminfo_dir / f"{_safe(name, 60)}.json"
        _write_json(p, data)
        return p

    def save_healing_events(self, events: list[dict]) -> pathlib.Path:
        """Save self-healing events to healing_events.json.

        Parameters:
            events: 自愈事件列表 / List of healing event dicts

        Returns:
            pathlib.Path: 保存的文件路径 / Saved file path
        """
        p = self.run_dir / "healing_events.json"
        _write_json(p, {"healing_events": events, "count": len(events)})
        return p

    # ---- Finalize ----

    def finalize(
        self,
        *,
        exit_code: int = 0,
        steps_total: int = 0,
        steps_passed: int = 0,
        steps_failed: int = 0,
        error_message: str = "",
    ) -> pathlib.Path:
        """Write steps.json + run_meta.json, close console log."""
        finished_at = _dt.datetime.now().astimezone()
        duration = (time.monotonic_ns() - self._monotonic_start_ns) / 1_000_000_000
        self.close()
        business = self._business.save()

        # Write consolidated steps.json
        _write_json(self.run_dir / "steps.json", {"steps": self._steps})

        # Count artifacts
        n_screenshots = sum(1 for _ in self.screenshots_dir.glob("*.png"))
        n_recordings = sum(1 for _ in self.recordings_dir.glob("*"))
        n_logcat = sum(1 for _ in self.logcat_dir.glob("*"))
        n_meminfo = sum(1 for _ in self.meminfo_dir.glob("*"))
        console_lines = 0
        log_path = self.run_dir / "console.log"
        if log_path.exists():
            try:
                with safe_directory(self.run_dir) as folder:
                    console_lines = sum(
                        block.count(b"\n")
                        for block in folder.iter_chunks("console.log")
                    )
            except Exception:
                pass

        status = "passed" if exit_code == 0 else "failed"

        meta = {
            "script_name": self.script_name,
            "script_path": self._script_path,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "duration_s": round(duration, 1),
            "exit_code": exit_code,
            "status": status,
            "error_message": error_message,
            "capture_errors": {"console": self._console_capture_error}
            if self._console_capture_error
            else {},
            "retained_console_capture_path": str(self._console_capture_path)
            if self._console_capture_error and self._console_capture_path
            else None,
            "business_status": business["summary"]["status"],
            "business_summary": business["summary"],
            "device": self._device_info,
            "git": {
                "commit": self._git_info.get("commit"),
                "branch": self._git_info.get("branch"),
                "dirty": self._git_info.get("dirty", False),
                "dirty_files_count": self._git_info.get("dirty_files_count", 0),
            },
            "steps": {
                "total": steps_total,
                "passed": steps_passed,
                "failed": steps_failed,
            },
            "artifacts": {
                "screenshots": n_screenshots,
                "screen_recordings": n_recordings,
                "logcat_dumps": n_logcat,
                "meminfo_snapshots": n_meminfo,
                "console_log_lines": console_lines,
            },
            "run_dir": str(self.run_dir),
        }

        meta_path = self.run_dir / "run_meta.json"
        _write_json(meta_path, meta)

        # Update global index
        try:
            _append_run_index(
                self.run_dir.parent / "index.json",
                {
                    "script_name": self.script_name,
                    "status": status,
                    "exit_code": exit_code,
                    "duration_s": round(duration, 1),
                    "started_at": self.started_at.isoformat(timespec="seconds"),
                    "run_dir": self.run_dir.name,
                    "git_commit": self._git_info.get("commit"),
                    "git_dirty": self._git_info.get("dirty", False),
                    "device_serial": (self._device_info or {}).get("serial"),
                },
            )
        except Exception:
            pass

        return meta_path

    def close(self) -> None:
        """Detach tee and export; failed exports retain a named private capture.

        Closing must not abort steps/meta finalization. A later close can retry the
        retained file, while capture_errors records export failure explicitly.
        """
        if self._console_log_fh:
            try:
                from phone_pilot.core.log import (
                    set_console_log_file,
                    get_console_log_error,
                )

                tee_error = get_console_log_error(self._console_log_fh)
                if tee_error:
                    self._console_capture_error = tee_error
                    self._console_capture_incomplete = True
                set_console_log_file(None)
            except Exception:
                pass
            try:
                self._console_log_fh.flush()
            except (OSError, ValueError) as exc:
                self._console_capture_error = f"console flush failed: {exc}"
                self._console_capture_incomplete = True
            try:
                self._console_log_fh.close()
            except (OSError, ValueError) as exc:
                self._console_capture_error += (
                    "; " if self._console_capture_error else ""
                ) + f"console close failed: {exc}"
                self._console_capture_incomplete = True
            finally:
                self._console_log_fh = None
        if self._console_capture_incomplete:
            # Exporting remaining bytes cannot establish that the failed capture
            # is complete. Retain it and preserve the error across retry/finalize.
            return
        if self._console_capture_path:
            try:
                with safe_directory(self.run_dir, writable=True) as folder:
                    folder.copy_file(
                        "console.log", self._console_capture_path, replace=True
                    )
            except (OSError, ValueError) as exc:
                cause = exc
                while cause.__cause__ is not None:
                    cause = cause.__cause__
                self._console_capture_error = f"console archive export failed: {cause}"
                return
            # Only unlink the verified private capture after successful archive copy.
            capture = self._console_capture_path
            try:
                with safe_directory(capture.parent, writable=True) as folder:
                    os.unlink(capture.name, dir_fd=folder.fd)
            except (OSError, ValueError) as exc:
                self._console_capture_error = f"console capture cleanup failed: {exc}"
                return
            self._console_capture_path = None
            self._console_capture_error = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write_json(path: pathlib.Path, obj: Any) -> None:
    with safe_directory(path.parent, create=True, writable=True) as folder:
        folder.write_bytes(
            path.name,
            (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            replace=True,
        )


def _append_run_index(index_path: pathlib.Path, record: dict) -> None:
    with safe_directory(index_path.parent, create=True, writable=True):
        pass
    data: dict = {}
    if index_path.exists():
        try:
            with safe_directory(index_path.parent) as folder:
                data = json.loads(folder.read_bytes(index_path.name))
        except Exception:
            data = {}
    records = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(records, list):
        records = []
    records.append(record)
    _write_json(index_path, {"runs": records})


__all__ = ["RunSession", "run_recordings_root"]
