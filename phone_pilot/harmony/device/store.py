"""
HarmonyOS device profile storage.

Captures and persists device profiles, reusing core/storage infrastructure.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Optional

from phone_pilot.core.storage import recordings_root, read_json, write_json

logger = logging.getLogger(__name__)


def _devices_dir(out_root: Optional[str] = None) -> pathlib.Path:
    """Get the devices storage directory."""
    root = pathlib.Path(out_root) if out_root else recordings_root()
    d = root / "devices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def capture_device_profile(
    device_serial: str,
    out_dir: Optional[str] = None,
) -> dict:
    """
    Capture and persist HarmonyOS device profile.

    Uses hmdriver2 device_info for comprehensive data.

    Returns:
        Dict with device profile.
    """
    from phone_pilot.harmony.hmdriver_bridge import get_hmdriver

    hm = get_hmdriver(device_serial)
    info = hm.device_info

    profile = {
        "serial": device_serial,
        "platform": "harmony",
        "productName": info.productName,
        "model": info.model,
        "sdkVersion": info.sdkVersion,
        "sysVersion": info.sysVersion,
        "cpuAbi": info.cpuAbi,
        "wlanIp": info.wlanIp,
        "displaySize": list(info.displaySize) if info.displaySize else None,
        "displayRotation": str(info.displayRotation),
    }

    # Persist to storage
    devices_dir = _devices_dir(out_dir)
    device_id = device_serial.replace(":", "_").replace("/", "_")
    profile_path = devices_dir / f"{device_id}.json"
    write_json(str(profile_path), profile)

    # Update index
    _update_index(devices_dir, device_id, profile)

    logger.info("Device profile captured: %s -> %s", device_serial, profile_path)
    return profile


def get_device_profile_from_store(
    device_id: str,
    out_dir: Optional[str] = None,
) -> Optional[dict]:
    """Load a device profile from store."""
    devices_dir = _devices_dir(out_dir)
    profile_path = devices_dir / f"{device_id}.json"
    if not profile_path.exists():
        return None
    return read_json(str(profile_path))


def list_devices_from_store(out_dir: Optional[str] = None) -> list[dict]:
    """List all known devices from store."""
    devices_dir = _devices_dir(out_dir)
    index_path = devices_dir / "index.json"
    if not index_path.exists():
        return []
    data = read_json(str(index_path))
    if isinstance(data, list):
        return data
    return data.get("devices", []) if isinstance(data, dict) else []


def set_device_unlock_pin(
    device_id: str,
    pin: str,
    out_dir: Optional[str] = None,
) -> None:
    """Persist unlock PIN for a device."""
    devices_dir = _devices_dir(out_dir)
    prefs_path = devices_dir / f"{device_id}_prefs.json"
    prefs = read_json(str(prefs_path)) if prefs_path.exists() else {}
    if not isinstance(prefs, dict):
        prefs = {}
    prefs["unlock_pin"] = pin
    write_json(str(prefs_path), prefs)


def get_device_unlock_pin(
    device_id: str,
    out_dir: Optional[str] = None,
) -> Optional[str]:
    """Read stored unlock PIN."""
    devices_dir = _devices_dir(out_dir)
    prefs_path = devices_dir / f"{device_id}_prefs.json"
    if not prefs_path.exists():
        return None
    prefs = read_json(str(prefs_path))
    return prefs.get("unlock_pin") if isinstance(prefs, dict) else None


def _update_index(devices_dir: pathlib.Path, device_id: str, profile: dict):
    """Update the devices index file."""
    index_path = devices_dir / "index.json"
    index_data = []
    if index_path.exists():
        raw = read_json(str(index_path))
        if isinstance(raw, list):
            index_data = raw
        elif isinstance(raw, dict):
            index_data = raw.get("devices", [])

    # Remove old entry for this device
    index_data = [d for d in index_data if d.get("serial") != profile.get("serial")]
    # Add new entry
    summary = {
        "serial": profile.get("serial"),
        "device_id": device_id,
        "platform": "harmony",
        "model": profile.get("model"),
        "productName": profile.get("productName"),
    }
    index_data.append(summary)
    write_json(str(index_path), {"devices": index_data})


__all__ = [
    "capture_device_profile",
    "get_device_profile_from_store",
    "list_devices_from_store",
    "set_device_unlock_pin",
    "get_device_unlock_pin",
]
