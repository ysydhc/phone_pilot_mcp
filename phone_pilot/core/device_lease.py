"""
Cross-process device lease and concurrency locking mechanism.

Solves the multi-agent / multi-project resource contention problem:
When multiple agents work concurrently on different projects, they may compete
for the same mobile device (Android / HarmonyOS / iOS), leading to scrambled UI
states and test failures.

This module provides:
1. File-based mutex locking with kernel-level flock (fcntl).
2. TTL-based lease management to prevent deadlocks on agent crash.
3. Orphaned/dead process detection.
4. Blocking/queueing with timeout support.
5. Context manager for safe RAII acquisition and cleanup.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)

# Default lock directory across processes
_DEFAULT_LOCK_DIR = os.getenv(
    "PHONE_PILOT_LOCK_DIR",
    os.path.join(tempfile.gettempdir(), "phone_pilot", "locks"),
)


def _sanitize_serial(serial: str) -> str:
    """Sanitize device serial for safe filesystem naming."""
    clean = re.sub(r"[^\w\-\.]", "_", (serial or "").strip())
    return clean or "unknown_device"


def _get_lock_dir() -> Path:
    """Ensure and return the lock directory."""
    path = Path(_DEFAULT_LOCK_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running on the local system."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        return e.errno == errno.EPERM


@dataclass
class LockInfo:
    device_serial: str
    session_id: str
    holder_pid: int
    holder_name: str
    purpose: str
    acquired_at: float
    expires_at: float
    ttl_s: float

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.expires_at - time.time())

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["remaining_s"] = round(self.remaining_s, 2)
        d["is_expired"] = self.is_expired
        return d


class DeviceLeaseManager:
    """Manages cross-process leases for mobile devices."""

    def __init__(self, lock_dir: Optional[str | Path] = None):
        self.lock_dir = Path(lock_dir) if lock_dir else _get_lock_dir()
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def _get_paths(self, serial: str) -> tuple[Path, Path]:
        safe = _sanitize_serial(serial)
        flock_file = self.lock_dir / f"{safe}.flock"
        meta_file = self.lock_dir / f"{safe}.json"
        return flock_file, meta_file

    def get_lock_status(self, serial: str) -> dict[str, Any]:
        """
        Check if a device is currently locked and retrieve lock metadata.
        """
        if not serial:
            return {"ok": False, "error": "device_serial_required"}

        flock_file, meta_file = self._get_paths(serial)
        if not meta_file.exists():
            return {"ok": True, "locked": False, "device_serial": serial}

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            info = LockInfo(**data)
        except Exception:
            # Corrupted or unreadable lock file
            return {"ok": True, "locked": False, "device_serial": serial, "note": "corrupted_lock_cleared"}

        # Check expiration or dead PID
        if info.is_expired:
            return {
                "ok": True,
                "locked": False,
                "device_serial": serial,
                "stale_reason": "ttl_expired",
                "last_lock": info.to_dict(),
            }

        if not _is_pid_alive(info.holder_pid):
            return {
                "ok": True,
                "locked": False,
                "device_serial": serial,
                "stale_reason": "holder_pid_dead",
                "last_lock": info.to_dict(),
            }

        return {
            "ok": True,
            "locked": True,
            "device_serial": serial,
            "lock_info": info.to_dict(),
        }

    def acquire_lock(
        self,
        serial: str,
        session_id: Optional[str] = None,
        purpose: str = "",
        ttl_s: float = 120.0,
        wait_timeout_s: float = 0.0,
        holder_name: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Acquire exclusive device lease.

        Args:
            serial: Device serial number.
            session_id: Unique session ID of the caller (auto-generated if omitted).
            purpose: Human-readable intention (e.g. 'project-A checkout E2E test').
            ttl_s: Lease time-to-live in seconds (auto-expires to prevent deadlocks).
            wait_timeout_s: Maximum seconds to block waiting if locked by another agent.
            holder_name: Name of caller/agent (defaults to PID or env var).
        """
        if not serial:
            return {"ok": False, "error": "device_serial_required"}

        ttl_s = max(5.0, float(ttl_s))
        start_time = time.time()
        session_id = session_id or f"session_{os.getpid()}_{int(time.time() * 1000)}"
        holder_name = (
            holder_name
            or os.getenv("PHONE_PILOT_AGENT_NAME")
            or os.getenv("USER")
            or f"agent_pid_{os.getpid()}"
        )

        flock_file, meta_file = self._get_paths(serial)

        # Polling loop if wait_timeout_s > 0
        while True:
            # Step 1: Open flock file and attempt non-blocking kernel lock
            try:
                fd = os.open(str(flock_file), os.O_CREAT | os.O_RDWR, 0o644)
            except Exception as e:
                return {"ok": False, "error": f"cannot_open_lock_file: {e}", "device_serial": serial}

            acquired_flock = False
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired_flock = True
            except (BlockingIOError, OSError):
                acquired_flock = False

            if acquired_flock:
                try:
                    # Step 2: Under flock protection, check existing metadata
                    current_status = self.get_lock_status(serial)
                    is_locked = current_status.get("locked", False)
                    lock_info_dict = current_status.get("lock_info")

                    # If already locked by the SAME session, treat as a reentrant renewal
                    if is_locked and lock_info_dict and lock_info_dict.get("session_id") == session_id:
                        is_locked = False

                    if not is_locked:
                        now = time.time()
                        info = LockInfo(
                            device_serial=serial,
                            session_id=session_id,
                            holder_pid=os.getpid(),
                            holder_name=holder_name,
                            purpose=purpose or "Automated agent verification",
                            acquired_at=now,
                            expires_at=now + ttl_s,
                            ttl_s=ttl_s,
                        )
                        # Atomic write metadata
                        temp_meta = meta_file.with_suffix(f".tmp.{os.getpid()}")
                        with open(temp_meta, "w", encoding="utf-8") as f:
                            json.dump(asdict(info), f, indent=2)
                        temp_meta.replace(meta_file)

                        return {
                            "ok": True,
                            "acquired": True,
                            "device_serial": serial,
                            "session_id": session_id,
                            "lock_info": info.to_dict(),
                            "note": f"Device {serial} locked successfully for {ttl_s}s",
                        }
                    else:
                        busy_info = lock_info_dict
                finally:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
                    os.close(fd)
            else:
                # Kernel lock busy (another thread/process is currently mutating lock state)
                busy_status = self.get_lock_status(serial)
                busy_info = busy_status.get("lock_info")

            # If we reached here, lock is held by another session
            elapsed = time.time() - start_time
            if elapsed >= wait_timeout_s:
                return {
                    "ok": False,
                    "error": "device_busy",
                    "device_serial": serial,
                    "requested_by": session_id,
                    "current_holder": busy_info,
                    "elapsed_s": round(elapsed, 2),
                    "note": f"Device is occupied by another session ({busy_info.get('session_id') if busy_info else 'unknown'}). Wait timed out.",
                }

            # Sleep briefly and retry
            time.sleep(min(0.5, max(0.1, (wait_timeout_s - elapsed) / 4)))

    def release_lock(
        self,
        serial: str,
        session_id: Optional[str] = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Release exclusive device lease.

        Args:
            serial: Device serial number.
            session_id: Session ID that currently owns the lock.
            force: If True, release regardless of session_id ownership (admin override).
        """
        if not serial:
            return {"ok": False, "error": "device_serial_required"}

        flock_file, meta_file = self._get_paths(serial)
        if not meta_file.exists():
            return {
                "ok": True,
                "released": True,
                "device_serial": serial,
                "note": "Lock file did not exist (already released)",
            }

        try:
            fd = os.open(str(flock_file), os.O_CREAT | os.O_RDWR, 0o644)
        except Exception as e:
            return {"ok": False, "error": f"cannot_open_lock_file: {e}", "device_serial": serial}

        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                if not meta_file.exists():
                    return {"ok": True, "released": True, "device_serial": serial}

                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                info = LockInfo(**data)

                if not force and session_id and info.session_id != session_id:
                    return {
                        "ok": False,
                        "error": "permission_denied",
                        "device_serial": serial,
                        "owner_session_id": info.session_id,
                        "caller_session_id": session_id,
                        "note": "Cannot release device lock owned by another session (use force=True if needed)",
                    }

                # Safe removal
                try:
                    meta_file.unlink(missing_ok=True)
                except Exception:
                    pass

                return {
                    "ok": True,
                    "released": True,
                    "device_serial": serial,
                    "session_id": info.session_id,
                    "held_duration_s": round(time.time() - info.acquired_at, 2),
                    "note": f"Device {serial} released successfully",
                }
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        except Exception as e:
            return {"ok": False, "error": str(e), "device_serial": serial}

    def renew_lock(
        self,
        serial: str,
        session_id: str,
        ttl_s: float = 120.0,
    ) -> dict[str, Any]:
        """Extend the lease duration of an existing lock."""
        if not serial:
            return {"ok": False, "error": "device_serial_required"}
        if not session_id:
            return {"ok": False, "error": "session_id_required"}

        ttl_s = max(5.0, float(ttl_s))
        flock_file, meta_file = self._get_paths(serial)
        if not meta_file.exists():
            return {"ok": False, "error": "lock_not_found", "device_serial": serial}

        try:
            fd = os.open(str(flock_file), os.O_CREAT | os.O_RDWR, 0o644)
        except Exception as e:
            return {"ok": False, "error": f"cannot_open_lock_file: {e}", "device_serial": serial}

        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                if not meta_file.exists():
                    return {"ok": False, "error": "lock_not_found", "device_serial": serial}

                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                info = LockInfo(**data)

                if info.session_id != session_id:
                    return {
                        "ok": False,
                        "error": "permission_denied",
                        "device_serial": serial,
                        "owner_session_id": info.session_id,
                        "caller_session_id": session_id,
                    }

                now = time.time()
                info.expires_at = now + ttl_s
                info.ttl_s = ttl_s

                temp_meta = meta_file.with_suffix(f".tmp.{os.getpid()}")
                with open(temp_meta, "w", encoding="utf-8") as f:
                    json.dump(asdict(info), f, indent=2)
                temp_meta.replace(meta_file)

                return {
                    "ok": True,
                    "renewed": True,
                    "device_serial": serial,
                    "session_id": session_id,
                    "lock_info": info.to_dict(),
                    "note": f"Lease renewed for {ttl_s}s",
                }
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        except Exception as e:
            return {"ok": False, "error": str(e), "device_serial": serial}


# Global singleton instance
_lease_manager = DeviceLeaseManager()


def get_device_lock_status(serial: str) -> dict[str, Any]:
    return _lease_manager.get_lock_status(serial)


def acquire_device_lock(
    serial: str,
    session_id: Optional[str] = None,
    purpose: str = "",
    ttl_s: float = 120.0,
    wait_timeout_s: float = 0.0,
    holder_name: Optional[str] = None,
) -> dict[str, Any]:
    return _lease_manager.acquire_lock(
        serial=serial,
        session_id=session_id,
        purpose=purpose,
        ttl_s=ttl_s,
        wait_timeout_s=wait_timeout_s,
        holder_name=holder_name,
    )


def release_device_lock(
    serial: str,
    session_id: Optional[str] = None,
    force: bool = False,
) -> dict[str, Any]:
    return _lease_manager.release_lock(serial=serial, session_id=session_id, force=force)


def renew_device_lock(
    serial: str,
    session_id: str,
    ttl_s: float = 120.0,
) -> dict[str, Any]:
    return _lease_manager.renew_lock(serial=serial, session_id=session_id, ttl_s=ttl_s)


@contextmanager
def device_lease(
    serial: str,
    session_id: Optional[str] = None,
    purpose: str = "",
    ttl_s: float = 120.0,
    wait_timeout_s: float = 10.0,
    auto_teardown_fn: Optional[Any] = None,
) -> Generator[dict[str, Any], None, None]:
    """
    Context manager for safe device acquisition, usage, and release.

    Usage:
        with device_lease("emulator-5554", purpose="Verify checkout flow") as lock_res:
            if not lock_res.get("ok"):
                raise RuntimeError(f"Could not lock device: {lock_res}")
            # ... perform device actions ...
    """
    res = acquire_device_lock(
        serial=serial,
        session_id=session_id,
        purpose=purpose,
        ttl_s=ttl_s,
        wait_timeout_s=wait_timeout_s,
    )
    sid = res.get("session_id")
    try:
        yield res
    finally:
        if auto_teardown_fn and callable(auto_teardown_fn):
            try:
                auto_teardown_fn(serial)
            except Exception as e:
                logger.warning("Error running auto_teardown_fn for %s: %s", serial, e)
        if sid:
            release_device_lock(serial, session_id=sid)
