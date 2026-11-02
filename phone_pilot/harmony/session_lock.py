"""
HarmonyOS session mutex lock to prevent concurrent uitest client preemption.

AAMS / uitest on HarmonyOS enforces single-client exclusivity. Concurrent
dumpLayout or touch operations across threads (e.g. background PopupGuard vs
main test script) can break the active session and cause connection drops.
This module provides per-device reentrant locking.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)

_device_locks: dict[str, threading.RLock] = {}
_global_lock = threading.Lock()


def get_device_lock(serial: str) -> threading.RLock:
    """Get or create the reentrant lock for a specific device serial."""
    with _global_lock:
        if serial not in _device_locks:
            _device_locks[serial] = threading.RLock()
        return _device_locks[serial]


@contextmanager
def harmony_session_lock(
    serial: str, timeout: float = 10.0
) -> Generator[bool, None, None]:
    """
    Context manager acquiring the per-device Harmony uitest session lock.

    Args:
        serial: Device serial number.
        timeout: Maximum seconds to wait for lock acquisition.

    Yields:
        bool: True if lock was acquired, False if timed out.
    """
    lock = get_device_lock(serial)
    acquired = lock.acquire(timeout=timeout)
    if not acquired:
        logger.warning(
            "Failed to acquire Harmony uitest session lock for [%s] within %.1fs",
            serial,
            timeout,
        )
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
