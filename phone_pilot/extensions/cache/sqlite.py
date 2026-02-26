"""
SQLite-backed cache for synonyms and crop templates.
"""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import threading
import time
from typing import Optional

from phone_pilot.core.storage import recordings_root


_DB_ENV = "PHONE_PILOT_DB_PATH"
_LOCK = threading.RLock()


def _db_path() -> pathlib.Path:
    env = os.getenv(_DB_ENV)
    if env:
        return pathlib.Path(env).expanduser().resolve()
    return (recordings_root(None) / "cache" / "phone_pilot.db").expanduser().resolve()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS synonyms (
            query TEXT NOT NULL,
            synonym TEXT NOT NULL,
            source TEXT,
            count INTEGER NOT NULL DEFAULT 1,
            last_seen REAL NOT NULL,
            PRIMARY KEY (query, synonym)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS crop_cache (
            app_package TEXT NOT NULL,
            activity TEXT NOT NULL,
            query TEXT NOT NULL,
            ui_sig TEXT NOT NULL,
            bbox TEXT,
            image_path TEXT NOT NULL,
            screen_path TEXT,
            count INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            last_seen REAL NOT NULL,
            PRIMARY KEY (app_package, activity, query, ui_sig)
        )
        """
    )
    conn.commit()


def get_synonyms(query: str, *, limit: int = 20) -> list[str]:
    q = (query or "").strip().lower()
    if not q:
        return []
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT synonym FROM synonyms
                WHERE query = ?
                ORDER BY count DESC, last_seen DESC
                LIMIT ?
                """,
                (q, int(limit)),
            ).fetchall()
        finally:
            conn.close()
    return [r["synonym"] for r in rows if r and r["synonym"]]


def record_synonym(query: str, synonym: str, *, source: str = "auto") -> None:
    q = (query or "").strip().lower()
    s = (synonym or "").strip()
    if not q or not s or q == s.lower():
        return
    now = time.time()
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO synonyms(query, synonym, source, count, last_seen)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(query, synonym) DO UPDATE SET
                    count = count + 1,
                    last_seen = excluded.last_seen,
                    source = excluded.source
                """,
                (q, s, source, now),
            )
            conn.commit()
        finally:
            conn.close()


def record_crop_cache(
    *,
    app_package: str,
    activity: str,
    query: str,
    ui_sig: str,
    image_path: str,
    screen_path: Optional[str] = None,
    bbox: Optional[tuple[int, int, int, int]] = None,
) -> None:
    ap = (app_package or "").strip()
    act = (activity or "").strip()
    q = (query or "").strip()
    sig = (ui_sig or "").strip()
    if not (ap and act and q and sig and image_path):
        return
    now = time.time()
    bbox_json = json.dumps(bbox) if bbox else None
    with _LOCK:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO crop_cache(
                    app_package, activity, query, ui_sig, bbox, image_path, screen_path,
                    count, created_at, last_seen
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(app_package, activity, query, ui_sig) DO UPDATE SET
                    count = count + 1,
                    last_seen = excluded.last_seen,
                    image_path = excluded.image_path,
                    screen_path = excluded.screen_path,
                    bbox = excluded.bbox
                """,
                (ap, act, q, sig, bbox_json, image_path, screen_path, now, now),
            )
            conn.commit()
        finally:
            conn.close()


def get_crop_cache(
    *,
    app_package: str,
    activity: str,
    query: str,
    ui_sig: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    ap = (app_package or "").strip()
    act = (activity or "").strip()
    q = (query or "").strip()
    sig = (ui_sig or "").strip() if ui_sig else None
    if not (ap and act and q):
        return []
    with _LOCK:
        conn = _connect()
        try:
            if sig:
                rows = conn.execute(
                    """
                    SELECT * FROM crop_cache
                    WHERE app_package = ? AND activity = ? AND query = ? AND ui_sig = ?
                    ORDER BY count DESC, last_seen DESC
                    LIMIT ?
                    """,
                    (ap, act, q, sig, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM crop_cache
                    WHERE app_package = ? AND activity = ? AND query = ?
                    ORDER BY count DESC, last_seen DESC
                    LIMIT ?
                    """,
                    (ap, act, q, int(limit)),
                ).fetchall()
        finally:
            conn.close()
    out: list[dict] = []
    for r in rows:
        if not r:
            continue
        item = dict(r)
        if item.get("bbox"):
            try:
                item["bbox"] = json.loads(item["bbox"])
            except Exception:
                pass
        out.append(item)
    return out


__all__ = [
    "get_synonyms",
    "record_synonym",
    "record_crop_cache",
    "get_crop_cache",
]
