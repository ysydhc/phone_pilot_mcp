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
import shutil
import subprocess
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
    kw = {"cwd": cwd, "check": False, "capture_output": True, "text": True, "timeout": 2}
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
            cwd=cwd, check=False, capture_output=True, text=True, timeout=3,
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
        return pathlib.Path(recordings_dir).expanduser().resolve()
    env = os.getenv("PHONE_PILOT_RECORDINGS_DIR")
    if env:
        return pathlib.Path(env).expanduser().resolve()
    return (pathlib.Path.cwd() / ".recordings").resolve()


# ---------------------------------------------------------------------------
# RunSession
# ---------------------------------------------------------------------------

class RunSession:
    """Manages artifacts for a single script run."""

    def __init__(self, run_dir: pathlib.Path, *, script_name: str = ""):
        self.run_dir = run_dir
        self.script_name = script_name
        self.screenshots_dir = run_dir / "screenshots"
        self.recordings_dir = run_dir / "recordings"
        self.logcat_dir = run_dir / "logcat"
        self.meminfo_dir = run_dir / "meminfo"
        self.started_at = _dt.datetime.now()
        self._console_log_fh = None
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
        runs_dir.mkdir(parents=True, exist_ok=True)

        ginfo = _git_info(git_cwd)
        commit = ginfo.get("commit") or "nogit"
        dirty_tag = "_dirty" if ginfo.get("dirty") else ""
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{_safe(script_name)}_{commit}{dirty_tag}_{ts}"

        run_dir = runs_dir / folder_name
        run_dir.mkdir(parents=True, exist_ok=True)

        session = cls(run_dir, script_name=script_name)
        session._git_info = ginfo
        session._script_path = script_path
        session._device_info = {"serial": device_serial, "platform": platform}

        # Create subdirs
        session.screenshots_dir.mkdir(exist_ok=True)
        session.recordings_dir.mkdir(exist_ok=True)
        session.logcat_dir.mkdir(exist_ok=True)
        session.meminfo_dir.mkdir(exist_ok=True)

        # Save git info (commit + dirty); diff in background thread
        _write_json(run_dir / "git_info.json", ginfo)

        def _write_diff_async() -> None:
            try:
                patch = _git_diff_patch(git_cwd)
                if patch and ginfo.get("dirty"):
                    (run_dir / "git_diff.patch").write_text(patch, encoding="utf-8")
            except Exception:
                pass

        if ginfo.get("dirty"):
            import threading
            _t = threading.Thread(target=_write_diff_async, daemon=True)
            _t.start()

        # Copy script source
        if script_path:
            src = pathlib.Path(script_path).expanduser().resolve()
            if src.exists():
                try:
                    shutil.copy2(str(src), str(run_dir / "script.py"))
                except Exception:
                    pass

        # Open console log and tee
        try:
            session._console_log_fh = open(run_dir / "console.log", "w", encoding="utf-8")
            from phone_pilot.core.log import set_console_log_file
            set_console_log_file(session._console_log_fh)
        except Exception:
            pass

        return session

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
            screenshot_name = f"{step_num:03d}_{_safe(action, 30)}.png"
            (self.screenshots_dir / screenshot_name).write_bytes(png_bytes)

        entry: dict[str, Any] = {
            "step": step_num,
            "action": action,
            "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
            "status": status,
            "detail": detail,
            "screenshot": screenshot_name,
        }
        if extra:
            entry.update(extra)
        self._steps.append(entry)

    def save_screenshot(self, name: str, png_bytes: bytes) -> pathlib.Path:
        """Save a named screenshot to screenshots/."""
        p = self.screenshots_dir / f"{_safe(name, 60)}.png"
        p.write_bytes(png_bytes)
        return p

    def save_logcat(self, name: str, text: str) -> pathlib.Path:
        """Save logcat dump to logcat/ subdir."""
        p = self.logcat_dir / f"{_safe(name, 60)}.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def save_recording(self, name: str, data: bytes) -> pathlib.Path:
        """Save a screen recording file to recordings/."""
        p = self.recordings_dir / f"{_safe(name, 60)}.mp4"
        p.write_bytes(data)
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
        finished_at = _dt.datetime.now()
        duration = (finished_at - self.started_at).total_seconds()

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
                console_lines = sum(1 for _ in open(log_path, encoding="utf-8"))
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
            _append_run_index(self.run_dir.parent / "index.json", {
                "script_name": self.script_name,
                "status": status,
                "exit_code": exit_code,
                "duration_s": round(duration, 1),
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "run_dir": self.run_dir.name,
                "git_commit": self._git_info.get("commit"),
                "git_dirty": self._git_info.get("dirty", False),
                "device_serial": (self._device_info or {}).get("serial"),
            })
        except Exception:
            pass

        self.close()
        return meta_path

    def close(self) -> None:
        """Close the console log file handle and detach tee."""
        if self._console_log_fh:
            try:
                from phone_pilot.core.log import set_console_log_file
                set_console_log_file(None)
            except Exception:
                pass
            try:
                self._console_log_fh.close()
            except Exception:
                pass
            self._console_log_fh = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_run_index(index_path: pathlib.Path, record: dict) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    records = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(records, list):
        records = []
    records.append(record)
    _write_json(index_path, {"runs": records})


__all__ = ["RunSession", "run_recordings_root"]
