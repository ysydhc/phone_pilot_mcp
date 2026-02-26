#!/usr/bin/env python3
"""
Android screen recording helpers (via `adb shell screenrecord`).

Two modes:
1) Detached (device-side background):
   - start: `adb shell 'screenrecord ... <remote.mp4> & echo $!'`
   - stop:  `adb shell kill -2 <pid>` then `adb pull <remote.mp4> <local.mp4>`
   This can survive MCP server restarts as long as you persist pid/remote_path.

2) Host-managed (for workflow full-run recording):
   - Run `adb shell screenrecord --time-limit N <remote>` as a host subprocess.
   - If workflow exceeds the Android time limit, we rotate segments automatically.
"""

from __future__ import annotations

import datetime as _dt
import pathlib
import re
import signal
import subprocess
import sys
import threading
import time
from typing import Optional

from phone_pilot.android.adb.utils import adb_prefix


def _iso_now() -> str:
    """Return current local time in ISO seconds format."""
    return _dt.datetime.now().isoformat(timespec="seconds")


def _safe_size(size: Optional[str]) -> Optional[str]:
    """
    Validate screenrecord --size argument. Expected format: "WIDTHxHEIGHT".
    """
    if size is None:
        return None
    s = str(size).strip().lower()
    if not s:
        return None
    if not re.fullmatch(r"\d{2,5}x\d{2,5}", s):
        raise ValueError("size must be like '720x1280'")
    return s


def _clamp_time_limit_s(v: int) -> int:
    """
    Android `screenrecord` has a max time limit on many devices (commonly 180s).
    We clamp to [1, 180] for safety.
    """
    try:
        x = int(v)
    except Exception:
        x = 180
    return max(1, min(x, 180))


def _pull_file(device_serial: Optional[str], remote_path: str, local_path: pathlib.Path) -> dict:
    """Pull a remote file to local_path and return transfer metadata."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = adb_prefix(device_serial) + ["pull", remote_path, str(local_path)]
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    size = None
    try:
        if local_path.exists():
            size = int(local_path.stat().st_size)
    except Exception:
        size = None
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "cmd": " ".join(cmd),
        "remote_path": remote_path,
        "local_path": str(local_path),
        "local_size": size,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _rm_remote(device_serial: Optional[str], remote_path: str) -> dict:
    """Remove a remote file and return execution metadata."""
    cmd = adb_prefix(device_serial) + ["shell", "rm", "-f", remote_path]
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "cmd": " ".join(cmd),
        "remote_path": remote_path,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def start_screenrecord_detached(
    device_serial: Optional[str],
    *,
    remote_path: str,
    bit_rate: int = 8_000_000,
    size: Optional[str] = None,
    time_limit_s: int = 180,
) -> dict:
    """
    Start screenrecord in background on device and return the device PID.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    tl = _clamp_time_limit_s(int(time_limit_s))
    sz = _safe_size(size)
    br = max(100_000, int(bit_rate))

    args = ["screenrecord", "--bit-rate", str(br), "--time-limit", str(tl)]
    if sz:
        args += ["--size", sz]
    args.append(remote_path)

    # NOTE: We run through `sh -c` to get background pid ($!).
    sh_cmd = " ".join(args) + " >/dev/null 2>&1 & echo $!"
    cmd = adb_prefix(device_serial) + ["shell", sh_cmd]
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    out = (proc.stdout or "").strip()
    pid = None
    try:
        pid = int(out.splitlines()[-1].strip())
    except Exception:
        pid = None
    ok = proc.returncode == 0 and pid is not None and pid > 0
    return {
        "ok": ok,
        "device_serial": device_serial,
        "pid": pid,
        "remote_path": remote_path,
        "bit_rate": br,
        "size": sz,
        "time_limit_s": tl,
        "cmd": " ".join(cmd),
        "stdout": out,
        "stderr": (proc.stderr or "").strip(),
        "note": "detached 模式受 screenrecord time-limit 限制（常见 180s）。",
    }


def stop_screenrecord_detached(
    device_serial: Optional[str],
    *,
    pid: Optional[int],
    remote_path: str,
    local_path: pathlib.Path,
    remove_remote: bool = True,
    settle_s: float = 0.5,
) -> dict:
    """
    Stop a detached screenrecord by pid, then pull remote mp4 to local_path.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    kill_res = None
    if pid:
        cmd = adb_prefix(device_serial) + ["shell", "kill", "-2", str(int(pid))]
        proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
        kill_res = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "cmd": " ".join(cmd),
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
        }
    if settle_s and settle_s > 0:
        time.sleep(float(settle_s))

    pull_res = _pull_file(device_serial, remote_path, local_path)
    rm_res = _rm_remote(device_serial, remote_path) if remove_remote else None
    ok = bool(pull_res.get("ok"))
    return {
        "ok": ok,
        "device_serial": device_serial,
        "pid": pid,
        "remote_path": remote_path,
        "local_path": str(local_path),
        "killed": kill_res,
        "pulled": pull_res,
        "removed_remote": rm_res,
    }


class WorkflowScreenRecorder:
    """
    Host-managed screen recording with automatic segment rotation.

    This is designed for workflow recording:
    - Start once, keep recording across the entire workflow.
    - If `screenrecord` hits its time limit, rotate to a new segment.
    - On stop, terminate current segment and pull everything to local.
    """

    def __init__(
        self,
        *,
        device_serial: Optional[str],
        out_dir: pathlib.Path,
        device_info_out_dir: Optional[pathlib.Path] = None,
        workflow_id: str,
        name: str,
        bit_rate: int = 8_000_000,
        size: Optional[str] = None,
        segment_time_limit_s: int = 170,
        remote_dir: str = "/sdcard/Download",
    ) -> None:
        """Initialize workflow recorder state and segment preferences."""
        self.device_serial = device_serial
        self.out_dir = out_dir
        self.device_info_out_dir = device_info_out_dir
        self.workflow_id = workflow_id
        self.name = name
        self.bit_rate = max(100_000, int(bit_rate))
        self.size = _safe_size(size)
        self.segment_time_limit_s = _clamp_time_limit_s(int(segment_time_limit_s))
        self.remote_dir = remote_dir

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cur_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._started_at: Optional[str] = None
        self._stopped_at: Optional[str] = None
        self._segments: list[dict] = []

    @property
    def segments(self) -> list[dict]:
        """Return a snapshot of recorded segment metadata."""
        return list(self._segments)

    def start(self) -> dict:
        """Start background recording loop and return start metadata."""
        if not self.device_serial:
            return {"ok": False, "error": "device_serial is required"}
        if self._thread and self._thread.is_alive():
            return {"ok": True, "already_running": True}
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._started_at = _iso_now()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"screenrecord_{self.workflow_id}", daemon=True)
        self._thread.start()
        return {"ok": True, "started_at": self._started_at, "out_dir": str(self.out_dir)}

    def stop(self, *, join_timeout_s: float = 25.0) -> dict:
        """Stop recording loop, terminate adb process, and return results."""
        self._stop.set()
        # Best-effort: interrupt the current adb process.
        with self._lock:
            proc = self._cur_proc
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                pass
            try:
                proc.wait(timeout=3.0)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
        if self._thread:
            self._thread.join(timeout=float(join_timeout_s))
        self._stopped_at = _iso_now()
        return {
            "ok": True,
            "started_at": self._started_at,
            "stopped_at": self._stopped_at,
            "out_dir": str(self.out_dir),
            "segment_count": len(self._segments),
            "segments": list(self._segments),
            "note": "workflow 全程录屏会按 segment_time_limit_s 自动分段；可用 segments[*].local_path 获取 mp4。",
        }

    def _run(self) -> None:
        """Background loop to rotate screenrecord segments and pull files."""
        seg = 0
        # Fallback profiles when device fails to start codec.
        profiles: list[tuple[int, Optional[str]]] = []
        
        # Try to load device preferences (if device store is available)
        try:
            from phone_pilot.android.device.store import get_device_screenrecord_prefs
            if self.device_serial and self.device_info_out_dir:
                prefs = get_device_screenrecord_prefs(self.device_serial, out_dir=str(self.device_info_out_dir))
                pref = prefs.get("preferred") if isinstance(prefs, dict) else None
                if isinstance(pref, dict):
                    br0 = pref.get("bit_rate")
                    sz0 = pref.get("size")
                    br1 = max(100_000, int(br0)) if br0 is not None else None
                    sz1 = (str(sz0).strip() if sz0 is not None else "") or None
                    if br1:
                        profiles.append((br1, sz1))
        except Exception:
            pass

        # Default probing order
        profiles += [
            (self.bit_rate, self.size),
            (min(self.bit_rate, 4_000_000), self.size or "720x1280"),
            (2_000_000, "720x1280"),
            (1_000_000, "640x360"),
        ]
        
        # Deduplicate
        seen_prof: set[str] = set()
        deduped: list[tuple[int, Optional[str]]] = []
        for br, sz in profiles:
            k = f"{(sz or '').strip()}::{int(br)}"
            if k in seen_prof:
                continue
            seen_prof.add(k)
            deduped.append((int(br), (str(sz).strip() if sz is not None else "") or None))
        profiles = deduped
        prof_i = 0
        consecutive_fail = 0
        
        while not self._stop.is_set():
            bit_rate, size = profiles[min(prof_i, len(profiles) - 1)]
            seg_id = f"{seg:03d}"
            remote_path = f"{self.remote_dir}/pt_workflow_{self.workflow_id}_{seg_id}.mp4"
            local_path = self.out_dir / f"screenrecord_{seg_id}.mp4"
            started_at = _iso_now()

            cmd = adb_prefix(self.device_serial) + ["shell", "screenrecord", "--bit-rate", str(int(bit_rate)), "--time-limit", str(self.segment_time_limit_s)]
            if size:
                cmd += ["--size", str(size)]
            cmd.append(remote_path)

            print(f"[screenrecord] start: {' '.join(cmd)}", file=sys.stderr)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with self._lock:
                self._cur_proc = proc

            # Wait until segment ends or stop is requested.
            while proc.poll() is None and not self._stop.is_set():
                time.sleep(0.2)
            if self._stop.is_set() and proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGINT)
                except Exception:
                    pass
                try:
                    proc.wait(timeout=3.0)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass

            rc = proc.wait(timeout=10.0) if proc.poll() is None else proc.returncode
            try:
                _out, _err = proc.communicate(timeout=0.2)
            except Exception:
                _out, _err = b"", b""
            finished_at = _iso_now()

            pull_res = _pull_file(self.device_serial, remote_path, local_path)
            rm_res = _rm_remote(self.device_serial, remote_path)

            stderr_tail = (_err or b"")[-800:].decode("utf-8", errors="replace").strip()
            
            # Prune empty/tiny files
            pruned_local = False
            try:
                local_size2 = pull_res.get("local_size") if isinstance(pull_res, dict) else None
                if isinstance(local_size2, int) and 0 <= local_size2 < 4096:
                    if local_path.exists():
                        local_path.unlink()
                        pruned_local = True
            except Exception:
                pruned_local = False
            if isinstance(pull_res, dict):
                pull_res["pruned_local"] = pruned_local
                
            self._segments.append({
                "segment_index": seg,
                "remote_path": remote_path,
                "local_path": str(local_path),
                "started_at": started_at,
                "finished_at": finished_at,
                "returncode": rc,
                "stderr_tail": stderr_tail,
                "profile": {"bit_rate": int(bit_rate), "size": size, "time_limit_s": int(self.segment_time_limit_s)},
                "pulled": pull_res,
                "removed_remote": rm_res,
            })

            # Detect failures
            fail = False
            try:
                fail = (rc is None) or (int(rc) != 0)
            except Exception:
                fail = True
            local_size = pull_res.get("local_size") if isinstance(pull_res, dict) else None
            if isinstance(local_size, int) and local_size < 4096:
                fail = True

            # Record success to device store
            if not fail:
                try:
                    from phone_pilot.android.device.store import record_device_screenrecord_success
                    if self.device_serial and self.device_info_out_dir:
                        record_device_screenrecord_success(
                            self.device_serial,
                            bit_rate=int(bit_rate),
                            size=size,
                            out_dir=str(self.device_info_out_dir),
                            source="workflow",
                        )
                except Exception:
                    pass

            if fail:
                consecutive_fail += 1
                if consecutive_fail >= 2 and prof_i < len(profiles) - 1:
                    prof_i += 1
                    consecutive_fail = 0
                if prof_i >= len(profiles) - 1 and consecutive_fail >= 3:
                    self._stop.set()
            else:
                consecutive_fail = 0

            seg += 1
            if self._stop.is_set():
                break
            time.sleep(0.15)


__all__ = [
    "start_screenrecord_detached",
    "stop_screenrecord_detached",
    "WorkflowScreenRecorder",
]
