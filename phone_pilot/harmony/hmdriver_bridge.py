"""
Bridge layer between phone_pilot and hmdriver2.

Manages hmdriver2 Driver instance lifecycle with caching and fallback
to HDC-only mode when uitest daemon is unavailable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lazy-imported to avoid hard crash if hmdriver2 is not installed
_HmDriver = None
_cache: dict[str, object] = {}
_failed_serials: set[str] = set()


def _ensure_hdc_in_path():
    """Ensure the hdc executable is on PATH for hmdriver2."""
    import os
    import shutil

    if shutil.which("hdc"):
        return  # Already available

    # Try to find hdc from our known paths
    try:
        from phone_pilot.harmony.hdc.utils import hdc_executable
        hdc_path = hdc_executable()
        if hdc_path and hdc_path != "hdc":
            hdc_dir = os.path.dirname(hdc_path)
            if hdc_dir and hdc_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = hdc_dir + os.pathsep + os.environ.get("PATH", "")
                logger.info("Added %s to PATH for hmdriver2", hdc_dir)
    except Exception as exc:
        logger.debug("Failed to locate hdc for PATH: %s", exc)


def _ensure_hmdriver_class():
    """Lazy import hmdriver2.driver.Driver."""
    global _HmDriver
    if _HmDriver is not None:
        return

    # Ensure hdc is on PATH before importing hmdriver2
    _ensure_hdc_in_path()

    try:
        from hmdriver2.driver import Driver as _Cls
        _HmDriver = _Cls
    except ImportError as exc:
        raise ImportError(
            "hmdriver2 is required for HarmonyOS support. "
            "Install it with: pip install phone-pilot[harmony]"
        ) from exc


def get_hmdriver(serial: str):
    """
    Get or create a cached hmdriver2 Driver instance for a device.

    Args:
        serial: Device serial number (from ``hdc list targets``).

    Returns:
        hmdriver2.driver.Driver instance.

    Raises:
        RuntimeError: If the uitest daemon cannot be started on the device
            (e.g. device offline, uitest not supported).
        ImportError: If hmdriver2 is not installed.
    """
    _ensure_hmdriver_class()

    if serial in _cache:
        return _cache[serial]

    if serial in _failed_serials:
        raise RuntimeError(
            f"hmdriver2 previously failed to connect to device [{serial}]. "
            "Call reset_hmdriver(serial) to retry."
        )

    try:
        driver = _HmDriver(serial)
        _cache[serial] = driver
        logger.info("hmdriver2 connected to device [%s]", serial)
        return driver
    except Exception as exc:
        _failed_serials.add(serial)
        raise RuntimeError(
            f"Failed to initialize hmdriver2 for device [{serial}]: {exc}"
        ) from exc


def has_hmdriver(serial: str) -> bool:
    """Check if an hmdriver2 instance already exists for a serial."""
    return serial in _cache


def reset_hmdriver(serial: str) -> None:
    """
    Release and remove a cached hmdriver2 instance.

    This also clears any failure flags, allowing a fresh connection attempt.
    """
    _failed_serials.discard(serial)
    drv = _cache.pop(serial, None)
    if drv is not None:
        try:
            del drv
        except Exception:
            pass
        logger.info("hmdriver2 instance released for device [%s]", serial)


def is_hmdriver_available(serial: str) -> bool:
    """
    Probe whether hmdriver2 can connect to a device.

    Non-destructive: returns True/False without raising.
    """
    try:
        get_hmdriver(serial)
        return True
    except Exception:
        return False


def release_all() -> None:
    """Release all cached hmdriver2 instances."""
    for serial in list(_cache):
        reset_hmdriver(serial)


__all__ = [
    "get_hmdriver",
    "has_hmdriver",
    "reset_hmdriver",
    "is_hmdriver_available",
    "release_all",
]
