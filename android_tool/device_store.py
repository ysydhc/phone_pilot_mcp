#!/usr/bin/env python3
"""
Device profile capture + persistence.

Goal:
- Record device information (name/model/brand, OS version, screen info, etc.) independently.
- Store it under the recordings out_dir and let each recording reference the device by device_id.

Storage layout (under out_root, default: ./recordings):
- devices/<device_id>.json           # latest snapshot per device
- devices/index.jsonl                # append-only events for audit/debug

device_id:
- Currently uses ADB serial for stability and simplicity.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Optional

from android_tool.android_device_utils import (
    get_android_os_info,
    get_device_profile,
    get_screen_density,
    get_screen_size,
    get_touch_abs_max,
    get_app_info,
    get_installed_apps,
)
from android_tool.recordings_store import append_jsonl, ensure_abs, iso_now, now_ts, read_json, write_json


def devices_dir(out_root: pathlib.Path) -> pathlib.Path:
    return out_root / "devices"


def devices_index_path(out_root: pathlib.Path) -> pathlib.Path:
    return devices_dir(out_root) / "index.jsonl"


def device_profile_path(out_root: pathlib.Path, device_id: str) -> pathlib.Path:
    return devices_dir(out_root) / f"{device_id}.json"


def android_device_info_path(out_root: pathlib.Path) -> pathlib.Path:
    """
    A single, human-friendly device info file containing the latest snapshot for each device_serial.

    Stored at:
      <out_dir>/Android_device_info.json

    Format:
      {
        "schema_version": 1,
        "updated_at": "...",
        "devices": { "<device_serial>": { ...device profile... }, ... }
      }
    """
    return out_root / "Android_device_info.json"


def _normalize_android_device_info(data: Any) -> dict[str, Any]:
    """
    Normalize legacy Android_device_info.json formats into the current schema.
    """
    if isinstance(data, dict) and "devices" in data and isinstance(data.get("devices"), dict):
        return data
    if isinstance(data, dict):
        # Legacy: { "<device_serial>": { ...profile... }, ... }
        return {"schema_version": 1, "updated_at": None, "devices": data}
    return {"schema_version": 1, "updated_at": None, "devices": {}}


def _update_android_device_info(out_root: pathlib.Path, *, device_serial: str, profile: dict[str, Any]) -> None:
    """
    Best-effort update the aggregate Android_device_info.json.
    """
    p = android_device_info_path(out_root)
    cur: dict[str, Any] = {}
    if p.exists():
        try:
            cur = read_json(p)
        except Exception:
            cur = {}
    cur = _normalize_android_device_info(cur)
    devices = cur.get("devices") if isinstance(cur, dict) else None
    if not isinstance(devices, dict):
        devices = {}
    devices[str(device_serial)] = profile
    cur["devices"] = devices
    cur["schema_version"] = 1
    cur["updated_at"] = iso_now()
    write_json(p, cur)

def _fold_index(index_file: pathlib.Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not index_file.exists():
        return latest
    with open(index_file, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except Exception:
                continue
            did = rec.get("device_id")
            if not did:
                continue
            prev = latest.get(did, {})
            latest[did] = {**prev, **rec}
    return latest


def _capture_installed_apps(device_serial: Optional[str], *, include_system: bool = True, cache_dir: Optional[str] = None) -> dict[str, Any]:
    """
    Best-effort capture installed apps with label + package name.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required", "include_system": include_system, "count": 0, "apps": []}
    try:
        res = get_installed_apps(device_serial, include_system=include_system)
    except Exception as e:
        return {"ok": False, "error": f"get_installed_apps failed: {e}", "include_system": include_system, "count": 0, "apps": []}
    if not (isinstance(res, dict) and res.get("ok")):
        return {
            "ok": False,
            "error": (res.get("stderr") if isinstance(res, dict) else "get_installed_apps failed"),
            "include_system": include_system,
            "count": 0,
            "apps": [],
        }
    apps_raw = list(res.get("apps") or [])
    apps: list[dict[str, Any]] = []
    for app in apps_raw:
        pkg = (app.get("package_name") if isinstance(app, dict) else None) or ""
        apk_path = (app.get("apk_path") if isinstance(app, dict) else None) or None
        if not pkg:
            continue
        info = get_app_info(device_serial, pkg, apk_path_on_device=apk_path, cache_dir=cache_dir)
        apps.append(
            {
                "PackageName": info.get("PackageName") if isinstance(info, dict) else pkg,
                "AppName": info.get("AppName") if isinstance(info, dict) else pkg,
                "VersionCode": info.get("VersionCode") if isinstance(info, dict) else None,
                "VersionName": info.get("VersionName") if isinstance(info, dict) else None,
            }
        )
    return {
        "ok": True,
        "include_system": include_system,
        "count": len(apps),
        "apps": apps,
    }


def capture_device_profile(
    device_serial: Optional[str],
    *,
    out_dir: str = "./recordings",
    keep_device: Optional[str] = None,
    include_system: bool = True,
) -> dict[str, Any]:
    """
    Capture current device profile and persist it.

    Returns the persisted profile dict (includes device_id and profile_path).
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    out_root = ensure_abs(out_dir)
    device_id = device_serial

    prof = get_device_profile(device_serial)
    os_info = get_android_os_info(device_serial)
    screen = get_screen_size(device_serial)
    density = get_screen_density(device_serial)
    abs_max = get_touch_abs_max(device_serial, keep_device)

    now = iso_now()
    cache_dir = str((ensure_abs(out_dir) / "apps_apk_cache").resolve())
    apps = _capture_installed_apps(device_serial, include_system=include_system, cache_dir=cache_dir)
    obj: dict[str, Any] = {
        "device_id": device_id,
        "device_serial": device_serial,
        "captured_at": now,
        "updated_at": now,
        "ts": now_ts(),
        # Identification
        "display_name": prof.get("display_name"),
        "market_name": prof.get("market_name"),
        "model": prof.get("model"),
        "brand": prof.get("brand"),
        "manufacturer": prof.get("manufacturer"),
        "device": prof.get("device"),
        "product": prof.get("product"),
        "adb": prof.get("adb"),
        # OS / build
        "os": os_info,
        # Screen / input hardware
        "screen": {
            "w": screen[0] if screen else None,
            "h": screen[1] if screen else None,
            "density_dpi": density,
        },
        "touch": {
            "abs_max_x": abs_max[0] if abs_max else None,
            "abs_max_y": abs_max[1] if abs_max else None,
            "keep_device": keep_device,
        },
        "apps": {
            **(apps if isinstance(apps, dict) else {}),
            "captured_at": now,
        },
    }

    path = device_profile_path(out_root, device_id)
    # Merge with existing (if present) to avoid losing previously captured fields.
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = read_json(path)
        except Exception:
            existing = {}
    merged = {**existing, **obj}
    write_json(path, merged)
    # Also update the aggregate file for convenience.
    _update_android_device_info(out_root, device_serial=device_serial, profile=merged)

    # Append an event to devices/index.jsonl
    append_jsonl(
        devices_index_path(out_root),
        {
            "device_id": device_id,
            "device_serial": device_serial,
            "display_name": merged.get("display_name"),
            "model": merged.get("model"),
            "brand": merged.get("brand"),
            "android_release": (merged.get("os") or {}).get("android_release"),
            "sdk_int": (merged.get("os") or {}).get("sdk_int"),
            "screen_w": (merged.get("screen") or {}).get("w"),
            "screen_h": (merged.get("screen") or {}).get("h"),
            "density_dpi": (merged.get("screen") or {}).get("density_dpi"),
            "updated_at": now,
            "ts": now_ts(),
        },
    )

    return {"ok": True, "device_id": device_id, "profile_path": str(path), "profile": merged}


def get_device_profile_from_store(device_id: str, *, out_dir: str = "./recordings") -> dict[str, Any]:
    out_root = ensure_abs(out_dir)
    path = device_profile_path(out_root, device_id)
    if not path.exists():
        return {"ok": False, "error": f"device not found: {device_id}", "profile_path": str(path)}
    try:
        prof = read_json(path)
    except Exception as e:
        return {"ok": False, "error": f"failed to read device profile: {e}", "profile_path": str(path)}
    return {"ok": True, "device_id": device_id, "profile_path": str(path), "profile": prof}


def list_devices_from_store(
    *,
    out_dir: str = "./recordings",
    query: Optional[str] = None,
    limit: int = 20,
) -> dict[str, Any]:
    out_root = ensure_abs(out_dir)
    idx = devices_index_path(out_root)
    latest = _fold_index(idx)
    items = list(latest.values())

    q = (query or "").strip().lower()
    if q:
        def hit(it: dict) -> bool:
            hay = " ".join(
                [
                    str(it.get("device_id", "")),
                    str(it.get("device_serial", "")),
                    str(it.get("display_name", "")),
                    str(it.get("brand", "")),
                    str(it.get("model", "")),
                    str(it.get("android_release", "")),
                ]
            ).lower()
            return q in hay

        items = [it for it in items if hit(it)]

    items.sort(key=lambda x: float(x.get("ts") or 0.0), reverse=True)
    return {
        "ok": True,
        "out_dir": str(out_root),
        "devices_index_path": str(idx),
        "count": len(items),
        "items": items[: max(1, int(limit))],
    }


def set_device_unlock_pin(
    device_id: str,
    pin: str,
    *,
    out_dir: str = "./recordings",
) -> dict[str, Any]:
    """
    Persist an unlock PIN for a device in devices/<device_id>.json.

    Security note:
    - This stores the PIN in local plaintext JSON under your recordings directory.
    - We intentionally DO NOT write the PIN into devices/index.jsonl to avoid accidental leaks.
    """
    did = (device_id or "").strip()
    p = (pin or "").strip()
    if not did:
        return {"ok": False, "error": "device_id is required"}
    if not p:
        return {"ok": False, "error": "pin is required"}
    out_root = ensure_abs(out_dir)
    path = device_profile_path(out_root, did)
    if not path.exists():
        return {"ok": False, "error": f"device not found: {did}", "profile_path": str(path)}
    try:
        existing: dict[str, Any] = read_json(path)
    except Exception:
        existing = {}
    now = iso_now()
    secrets = dict((existing.get("secrets") or {}) if isinstance(existing.get("secrets"), dict) else {})
    secrets["unlock_pin"] = p
    secrets["unlock_pin_updated_at"] = now
    merged = {**existing, "secrets": secrets, "updated_at": now, "ts": now_ts()}
    write_json(path, merged)
    # Keep aggregate device info in sync (includes secrets by user request).
    _update_android_device_info(out_root, device_serial=did, profile=merged)
    # Append a redacted event
    append_jsonl(
        devices_index_path(out_root),
        {
            "device_id": did,
            "device_serial": merged.get("device_serial"),
            "event": "unlock_pin_set",
            "unlock_pin_set": True,
            "updated_at": now,
            "ts": now_ts(),
        },
    )
    return {"ok": True, "device_id": did, "profile_path": str(path), "unlock_pin_set": True, "updated_at": now}


def set_device_unlock_method(
    device_id: str,
    unlock_method: str,
    *,
    out_dir: str = "./recordings",
) -> dict[str, Any]:
    """
    Persist an unlock method hint for a device in devices/<device_id>.json.

    unlock_method:
    - "pin": device requires PIN entry
    - "swipe_only": swipe up is sufficient

    Note: this is not sensitive and may be written to devices/index.jsonl.
    """
    did = (device_id or "").strip()
    m = (unlock_method or "").strip().lower()
    if not did:
        return {"ok": False, "error": "device_id is required"}
    if m not in ("pin", "swipe_only"):
        return {"ok": False, "error": "invalid_unlock_method", "unlock_method": m, "hint": "expected 'pin' or 'swipe_only'."}
    out_root = ensure_abs(out_dir)
    path = device_profile_path(out_root, did)
    if not path.exists():
        return {"ok": False, "error": f"device not found: {did}", "profile_path": str(path)}
    try:
        existing: dict[str, Any] = read_json(path)
    except Exception:
        existing = {}
    now = iso_now()
    secrets = dict((existing.get("secrets") or {}) if isinstance(existing.get("secrets"), dict) else {})
    secrets["unlock_method"] = m
    secrets["unlock_method_updated_at"] = now
    merged = {**existing, "secrets": secrets, "updated_at": now, "ts": now_ts()}
    write_json(path, merged)
    _update_android_device_info(out_root, device_serial=did, profile=merged)
    append_jsonl(
        devices_index_path(out_root),
        {
            "device_id": did,
            "device_serial": merged.get("device_serial"),
            "event": "unlock_method_set",
            "unlock_method": m,
            "updated_at": now,
            "ts": now_ts(),
        },
    )
    return {"ok": True, "device_id": did, "profile_path": str(path), "unlock_method": m, "updated_at": now}


def get_device_unlock_method(
    device_id: str,
    *,
    out_dir: str = "./recordings",
) -> dict[str, Any]:
    """
    Read the stored unlock method hint for a device (if any).
    """
    did = (device_id or "").strip()
    if not did:
        return {"ok": False, "error": "device_id is required"}
    out_root = ensure_abs(out_dir)
    path = device_profile_path(out_root, did)
    if not path.exists():
        return {"ok": False, "error": f"device not found: {did}", "profile_path": str(path)}
    try:
        prof: dict[str, Any] = read_json(path)
    except Exception as e:
        return {"ok": False, "error": f"failed to read device profile: {e}", "profile_path": str(path)}
    secrets = (prof.get("secrets") or {}) if isinstance(prof.get("secrets"), dict) else {}
    m = (secrets.get("unlock_method") or "")
    if not isinstance(m, str) or not m.strip():
        return {"ok": False, "device_id": did, "profile_path": str(path), "error": "unlock_method_not_set"}
    return {
        "ok": True,
        "device_id": did,
        "profile_path": str(path),
        "unlock_method": m.strip(),
        "unlock_method_updated_at": secrets.get("unlock_method_updated_at"),
    }


def get_device_unlock_pin(
    device_id: str,
    *,
    out_dir: str = "./recordings",
) -> dict[str, Any]:
    """
    Read the stored unlock PIN for a device (if any) from devices/<device_id>.json.
    """
    did = (device_id or "").strip()
    if not did:
        return {"ok": False, "error": "device_id is required"}
    out_root = ensure_abs(out_dir)
    path = device_profile_path(out_root, did)
    if not path.exists():
        return {"ok": False, "error": f"device not found: {did}", "profile_path": str(path)}
    try:
        prof: dict[str, Any] = read_json(path)
    except Exception as e:
        return {"ok": False, "error": f"failed to read device profile: {e}", "profile_path": str(path)}
    secrets = (prof.get("secrets") or {}) if isinstance(prof.get("secrets"), dict) else {}
    pin = (secrets.get("unlock_pin") or "")
    if not isinstance(pin, str) or not pin.strip():
        return {"ok": False, "device_id": did, "profile_path": str(path), "error": "unlock_pin_not_set"}
    return {
        "ok": True,
        "device_id": did,
        "profile_path": str(path),
        "unlock_pin_set": True,
        "unlock_pin_updated_at": secrets.get("unlock_pin_updated_at"),
        "pin": pin,
    }


def get_device_screenrecord_prefs(
    device_id: str,
    *,
    out_dir: str = "./recordings",
) -> dict[str, Any]:
    """
    Read device screenrecord preferences (best-effort) from devices/<device_id>.json.
    """
    did = (device_id or "").strip()
    if not did:
        return {"ok": False, "error": "device_id is required"}
    out_root = ensure_abs(out_dir)
    path = device_profile_path(out_root, did)
    if not path.exists():
        return {"ok": False, "error": f"device not found: {did}", "profile_path": str(path)}
    try:
        prof: dict[str, Any] = read_json(path)
    except Exception as e:
        return {"ok": False, "error": f"failed to read device profile: {e}", "profile_path": str(path)}

    caps = (prof.get("capabilities") or {}) if isinstance(prof.get("capabilities"), dict) else {}
    sr = (caps.get("screenrecord") or {}) if isinstance(caps.get("screenrecord"), dict) else {}
    preferred = sr.get("preferred") if isinstance(sr.get("preferred"), dict) else None
    supported = sr.get("supported_profiles") if isinstance(sr.get("supported_profiles"), list) else []
    supported_sizes = sr.get("supported_sizes") if isinstance(sr.get("supported_sizes"), list) else []
    return {
        "ok": True,
        "device_id": did,
        "profile_path": str(path),
        "preferred": preferred,
        "supported_profiles": supported,
        "supported_sizes": supported_sizes,
    }


def record_device_screenrecord_success(
    device_id: str,
    *,
    bit_rate: int,
    size: Optional[str],
    out_dir: str = "./recordings",
    source: str = "workflow",
) -> dict[str, Any]:
    """
    Persist a successful screenrecord profile for a device.

    - Writes into devices/<device_id>.json under capabilities.screenrecord:
      - preferred: {bit_rate, size, source, updated_at}
      - supported_profiles: deduped list of {bit_rate, size}
      - supported_sizes: deduped list of size strings
    """
    did = (device_id or "").strip()
    if not did:
        return {"ok": False, "error": "device_id is required"}
    out_root = ensure_abs(out_dir)
    path = device_profile_path(out_root, did)
    if not path.exists():
        return {"ok": False, "error": f"device not found: {did}", "profile_path": str(path)}

    try:
        existing: dict[str, Any] = read_json(path)
    except Exception:
        existing = {}

    now = iso_now()
    br = max(100_000, int(bit_rate))
    sz = (str(size).strip() if size is not None else "") or None

    caps = dict((existing.get("capabilities") or {}) if isinstance(existing.get("capabilities"), dict) else {})
    sr = dict((caps.get("screenrecord") or {}) if isinstance(caps.get("screenrecord"), dict) else {})

    # Supported profiles (dedupe by (size, bitrate))
    supported_profiles0 = sr.get("supported_profiles")
    supported_profiles: list[dict[str, Any]] = []
    if isinstance(supported_profiles0, list):
        supported_profiles = [x for x in supported_profiles0 if isinstance(x, dict)]

    key = f"{sz or ''}::{br}"
    seen = set()
    out_profiles: list[dict[str, Any]] = []
    for it in supported_profiles + [{"bit_rate": br, "size": sz}]:
        b0 = it.get("bit_rate")
        s0 = (it.get("size") or None)
        try:
            b1 = max(100_000, int(b0))
        except Exception:
            continue
        s1 = (str(s0).strip() if s0 is not None else "") or None
        k1 = f"{s1 or ''}::{b1}"
        if k1 in seen:
            continue
        seen.add(k1)
        out_profiles.append({"bit_rate": b1, "size": s1})

    # Supported sizes (dedupe)
    supported_sizes0 = sr.get("supported_sizes")
    supported_sizes: list[str] = []
    if isinstance(supported_sizes0, list):
        for x in supported_sizes0:
            if isinstance(x, str) and x.strip():
                supported_sizes.append(x.strip())
    if sz and sz not in supported_sizes:
        supported_sizes.append(sz)

    sr["preferred"] = {"bit_rate": br, "size": sz, "source": str(source or "workflow"), "updated_at": now}
    sr["supported_profiles"] = out_profiles
    sr["supported_sizes"] = supported_sizes
    caps["screenrecord"] = sr

    merged = {**existing, "capabilities": caps, "updated_at": now, "ts": now_ts()}
    write_json(path, merged)
    # Keep aggregate device info in sync.
    _update_android_device_info(out_root, device_serial=did, profile=merged)

    # Append a non-sensitive event
    append_jsonl(
        devices_index_path(out_root),
        {
            "device_id": did,
            "device_serial": merged.get("device_serial"),
            "event": "screenrecord_profile_success",
            "screenrecord_size": sz,
            "screenrecord_bit_rate": br,
            "source": str(source or "workflow"),
            "updated_at": now,
            "ts": now_ts(),
        },
    )
    return {"ok": True, "device_id": did, "profile_path": str(path), "preferred": sr["preferred"]}


