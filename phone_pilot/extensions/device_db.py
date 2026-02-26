#!/usr/bin/env python3
"""
SQLite persistence for device profiles and derived data.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any, Optional

from phone_pilot.core.storage import iso_now, migrate_jsonl_to_json, now_ts, read_json, recordings_root


def db_path(out_dir: str) -> pathlib.Path:
    out_root = recordings_root(out_dir)
    new_path = out_root / "devices" / "devices.db"
    legacy = out_root / "devices.db"
    if legacy.exists() and not new_path.exists():
        try:
            new_path.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(new_path)
        except Exception:
            pass
    return new_path


def _connect(out_dir: str) -> sqlite3.Connection:
    path = db_path(out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS devices (
            device_id TEXT PRIMARY KEY,
            device_serial TEXT,
            display_name TEXT,
            market_name TEXT,
            model TEXT,
            brand TEXT,
            manufacturer TEXT,
            device TEXT,
            product TEXT,
            adb TEXT,
            android_release TEXT,
            sdk_int INTEGER,
            security_patch TEXT,
            screen_w INTEGER,
            screen_h INTEGER,
            density_dpi INTEGER,
            abs_max_x INTEGER,
            abs_max_y INTEGER,
            launcher_package TEXT,
            launcher_version_name TEXT,
            launcher_version_code INTEGER,
            captured_at TEXT,
            updated_at TEXT,
            ts REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS apps (
            device_id TEXT,
            package_name TEXT,
            app_name TEXT,
            version_code INTEGER,
            version_name TEXT,
            icon_path TEXT,
            icon_dpi INTEGER,
            icon_source TEXT,
            is_system INTEGER,
            captured_at TEXT,
            PRIMARY KEY (device_id, package_name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS device_events (
            device_id TEXT,
            event TEXT,
            payload_json TEXT,
            updated_at TEXT,
            ts REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS launch_profiles (
            device_id TEXT,
            package_name TEXT,
            activity TEXT,
            component TEXT,
            adb_cmd TEXT,
            source TEXT,
            updated_at TEXT,
            ts REAL,
            PRIMARY KEY (device_id, package_name)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_device ON apps(device_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_pkg ON apps(package_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_launch_profiles_device ON launch_profiles(device_id)")


def ensure_db(out_dir: str) -> sqlite3.Connection:
    path = db_path(out_dir)
    existed = path.exists()
    conn = _connect(out_dir)
    _init_db(conn)
    if not existed:
        _migrate_from_json(out_dir, conn)
    return conn


def _device_row_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    os_info = (profile.get("os") or {}) if isinstance(profile.get("os"), dict) else {}
    screen = (profile.get("screen") or {}) if isinstance(profile.get("screen"), dict) else {}
    touch = (profile.get("touch") or {}) if isinstance(profile.get("touch"), dict) else {}
    launcher = (profile.get("launcher") or {}) if isinstance(profile.get("launcher"), dict) else {}
    return {
        "device_id": profile.get("device_id") or profile.get("device_serial"),
        "device_serial": profile.get("device_serial"),
        "display_name": profile.get("display_name"),
        "market_name": profile.get("market_name"),
        "model": profile.get("model"),
        "brand": profile.get("brand"),
        "manufacturer": profile.get("manufacturer"),
        "device": profile.get("device"),
        "product": profile.get("product"),
        "adb": profile.get("adb"),
        "android_release": os_info.get("android_release"),
        "sdk_int": os_info.get("sdk_int"),
        "security_patch": os_info.get("security_patch"),
        "screen_w": screen.get("w"),
        "screen_h": screen.get("h"),
        "density_dpi": screen.get("density_dpi"),
        "abs_max_x": touch.get("abs_max_x"),
        "abs_max_y": touch.get("abs_max_y"),
        "launcher_package": launcher.get("package"),
        "launcher_version_name": launcher.get("version_name"),
        "launcher_version_code": launcher.get("version_code"),
        "captured_at": profile.get("captured_at"),
        "updated_at": profile.get("updated_at"),
        "ts": profile.get("ts"),
    }


def save_device_snapshot(
    out_dir: str,
    *,
    profile: dict[str, Any],
    apps: list[dict[str, Any]],
    launch_profiles: list[dict[str, Any]],
    events: Optional[list[dict[str, Any]]] = None,
) -> None:
    conn = ensure_db(out_dir)
    try:
        device_row = _device_row_from_profile(profile)
        device_id = device_row.get("device_id")
        if not device_id:
            return
        with conn:
            conn.execute(
                """
                INSERT INTO devices (
                    device_id, device_serial, display_name, market_name, model, brand,
                    manufacturer, device, product, adb, android_release, sdk_int,
                    security_patch, screen_w, screen_h, density_dpi, abs_max_x, abs_max_y,
                    launcher_package, launcher_version_name, launcher_version_code,
                    captured_at, updated_at, ts
                ) VALUES (
                    :device_id, :device_serial, :display_name, :market_name, :model, :brand,
                    :manufacturer, :device, :product, :adb, :android_release, :sdk_int,
                    :security_patch, :screen_w, :screen_h, :density_dpi, :abs_max_x, :abs_max_y,
                    :launcher_package, :launcher_version_name, :launcher_version_code,
                    :captured_at, :updated_at, :ts
                )
                ON CONFLICT(device_id) DO UPDATE SET
                    device_serial=excluded.device_serial,
                    display_name=excluded.display_name,
                    market_name=excluded.market_name,
                    model=excluded.model,
                    brand=excluded.brand,
                    manufacturer=excluded.manufacturer,
                    device=excluded.device,
                    product=excluded.product,
                    adb=excluded.adb,
                    android_release=excluded.android_release,
                    sdk_int=excluded.sdk_int,
                    security_patch=excluded.security_patch,
                    screen_w=excluded.screen_w,
                    screen_h=excluded.screen_h,
                    density_dpi=excluded.density_dpi,
                    abs_max_x=excluded.abs_max_x,
                    abs_max_y=excluded.abs_max_y,
                    launcher_package=excluded.launcher_package,
                    launcher_version_name=excluded.launcher_version_name,
                    launcher_version_code=excluded.launcher_version_code,
                    captured_at=excluded.captured_at,
                    updated_at=excluded.updated_at,
                    ts=excluded.ts
                """,
                device_row,
            )

            conn.execute("DELETE FROM apps WHERE device_id = ?", (device_id,))
            if apps:
                conn.executemany(
                    """
                    INSERT INTO apps (
                        device_id, package_name, app_name, version_code, version_name,
                        icon_path, icon_dpi, icon_source, is_system, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            device_id,
                            _get_app_field(app, "PackageName", "package_name"),
                            _get_app_field(app, "AppName", "app_name"),
                            _get_app_field(app, "VersionCode", "version_code"),
                            _get_app_field(app, "VersionName", "version_name"),
                            _get_app_field(app, "IconPath", "icon_path"),
                            _get_app_field(app, "IconDpi", "icon_dpi"),
                            _get_app_field(app, "IconSource", "icon_source"),
                            _get_app_field(app, "IsSystem", "is_system"),
                            app.get("captured_at") or profile.get("captured_at"),
                        )
                        for app in apps
                    ],
                )

            conn.execute("DELETE FROM launch_profiles WHERE device_id = ?", (device_id,))
            if launch_profiles:
                conn.executemany(
                    """
                    INSERT INTO launch_profiles (
                        device_id, package_name, activity, component, adb_cmd, source,
                        updated_at, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            device_id,
                            lp.get("package_name") or lp.get("package"),
                            lp.get("activity"),
                            lp.get("component"),
                            lp.get("adb_cmd"),
                            lp.get("source"),
                            lp.get("updated_at") or profile.get("updated_at") or iso_now(),
                            lp.get("ts") or profile.get("ts") or now_ts(),
                        )
                        for lp in launch_profiles
                        if (lp.get("package_name") or lp.get("package"))
                    ],
                )

            if events:
                conn.executemany(
                    """
                    INSERT INTO device_events (device_id, event, payload_json, updated_at, ts)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            device_id,
                            ev.get("event"),
                            json.dumps(ev.get("payload") or {}, ensure_ascii=False),
                            ev.get("updated_at") or iso_now(),
                            ev.get("ts") or now_ts(),
                        )
                        for ev in events
                    ],
                )
    finally:
        conn.close()


def load_device_snapshot(
    out_dir: str,
    *,
    device_id: Optional[str] = None,
    device_serial: Optional[str] = None,
) -> dict[str, Any]:
    if not device_id and not device_serial:
        return {"ok": False, "error": "device_id or device_serial is required"}
    conn = ensure_db(out_dir)
    try:
        if device_id:
            row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM devices WHERE device_serial = ? ORDER BY ts DESC LIMIT 1",
                (device_serial,),
            ).fetchone()
        if not row:
            return {"ok": False, "error": "device_not_found"}
        did = row["device_id"]
        apps = conn.execute(
            "SELECT * FROM apps WHERE device_id = ? ORDER BY app_name COLLATE NOCASE ASC",
            (did,),
        ).fetchall()
        launch_profiles = conn.execute(
            "SELECT * FROM launch_profiles WHERE device_id = ? ORDER BY package_name ASC",
            (did,),
        ).fetchall()
        return {
            "ok": True,
            "device": dict(row),
            "apps": [dict(a) for a in apps],
            "launch_profiles": [dict(lp) for lp in launch_profiles],
        }
    finally:
        conn.close()


def _get_app_field(app: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in app:
            return app.get(k)
    return None


def _migrate_from_json(out_dir: str, conn: sqlite3.Connection) -> None:
    out_root = recordings_root(out_dir)
    devices_dir = out_root / "devices"
    if not devices_dir.exists():
        return

    launch_profiles_by_device: dict[str, list[dict[str, Any]]] = {}
    launch_idx = out_root / "launch_profiles" / "index.json"
    legacy_idx = out_root / "launch_profiles" / "index.jsonl"
    migrate_jsonl_to_json(launch_idx, legacy_idx)
    if launch_idx.exists():
        try:
            data = read_json(launch_idx)
            records = data.get("records") if isinstance(data, dict) else None
            if not isinstance(records, list):
                records = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                did = rec.get("device_serial")
                pkg = rec.get("package")
                if not did or not pkg:
                    continue
                launch_profiles_by_device.setdefault(did, []).append(
                    {
                        "package": pkg,
                        "activity": rec.get("activity"),
                        "component": rec.get("component"),
                        "adb_cmd": rec.get("adb_cmd"),
                        "source": "launch_profiles_index",
                        "updated_at": rec.get("created_at"),
                        "ts": rec.get("ts"),
                    }
                )
        except Exception:
            pass

    for path in devices_dir.glob("*.json"):
        try:
            prof = read_json(path)
        except Exception:
            continue
        did = prof.get("device_id") or prof.get("device_serial")
        apps = []
        apps_obj = (prof.get("apps") or {}) if isinstance(prof.get("apps"), dict) else {}
        apps = list(apps_obj.get("apps") or [])
        lps = launch_profiles_by_device.get(did, [])
        device_row = _device_row_from_profile(prof)
        if not device_row.get("device_id"):
            continue
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO devices (
                    device_id, device_serial, display_name, market_name, model, brand,
                    manufacturer, device, product, adb, android_release, sdk_int,
                    security_patch, screen_w, screen_h, density_dpi, abs_max_x, abs_max_y,
                    launcher_package, launcher_version_name, launcher_version_code,
                    captured_at, updated_at, ts
                ) VALUES (
                    :device_id, :device_serial, :display_name, :market_name, :model, :brand,
                    :manufacturer, :device, :product, :adb, :android_release, :sdk_int,
                    :security_patch, :screen_w, :screen_h, :density_dpi, :abs_max_x, :abs_max_y,
                    :launcher_package, :launcher_version_name, :launcher_version_code,
                    :captured_at, :updated_at, :ts
                )
                """,
                device_row,
            )
            conn.execute("DELETE FROM apps WHERE device_id = ?", (device_row["device_id"],))
            if apps:
                conn.executemany(
                    """
                    INSERT INTO apps (
                        device_id, package_name, app_name, version_code, version_name,
                        icon_path, icon_dpi, icon_source, is_system, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            device_row["device_id"],
                            _get_app_field(app, "PackageName", "package_name"),
                            _get_app_field(app, "AppName", "app_name"),
                            _get_app_field(app, "VersionCode", "version_code"),
                            _get_app_field(app, "VersionName", "version_name"),
                            _get_app_field(app, "IconPath", "icon_path"),
                            _get_app_field(app, "IconDpi", "icon_dpi"),
                            _get_app_field(app, "IconSource", "icon_source"),
                            _get_app_field(app, "IsSystem", "is_system"),
                            apps_obj.get("captured_at") or prof.get("captured_at"),
                        )
                        for app in apps
                    ],
                )
            conn.execute("DELETE FROM launch_profiles WHERE device_id = ?", (device_row["device_id"],))
            if lps:
                conn.executemany(
                    """
                    INSERT INTO launch_profiles (
                        device_id, package_name, activity, component, adb_cmd, source,
                        updated_at, ts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            device_row["device_id"],
                            lp.get("package"),
                            lp.get("activity"),
                            lp.get("component"),
                            lp.get("adb_cmd"),
                            lp.get("source"),
                            lp.get("updated_at") or iso_now(),
                            lp.get("ts") or now_ts(),
                        )
                        for lp in lps
                    ],
                )

