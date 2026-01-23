#!/usr/bin/env python3
"""
Persistence helpers for recordings:
- stable folder naming
- meta.json read/write
- index.jsonl append + fold into latest state
- lookup recordings by key (id/name/substring)
- infer replay start context
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import re
import time
from typing import Any, Optional


def now_dirname() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_abs(path: str) -> pathlib.Path:
    return pathlib.Path(path).expanduser().resolve()


def iso_now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    # Keep it filesystem-friendly for folder naming and searching.
    cleaned = re.sub(r"[^\w\-\.]+", "_", name.strip(), flags=re.UNICODE).strip("_")
    return cleaned[:80] if cleaned else "recording"


def index_path(out_root: pathlib.Path) -> pathlib.Path:
    return out_root / "index.jsonl"


def write_json(path: pathlib.Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def fold_index_records(index_file: pathlib.Path) -> dict[str, dict]:
    """
    Read append-only index.jsonl and fold into latest state per recording_id.
    """
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
            rid = rec.get("recording_id")
            if not rid:
                continue
            prev = latest.get(rid, {})
            merged = {**prev, **rec}
            latest[rid] = merged
    return latest


def load_recording_by_key(out_root: pathlib.Path, key: str) -> Optional[dict]:
    """
    Load a recording entry from index.jsonl by:
    - recording_id
    - exact name match
    - name/tags substring match (fallback, newest)
    """
    latest = fold_index_records(index_path(out_root))
    if key in latest:
        return latest[key]
    for it in latest.values():
        if it.get("name") == key:
            return it
    k = key.strip().lower()
    hits: list[dict[str, Any]] = []
    for it in latest.values():
        hay = " ".join([str(it.get("name", "")), " ".join(it.get("tags") or [])]).lower()
        if k and k in hay:
            hits.append(it)
    hits.sort(key=lambda x: float(x.get("ts") or 0.0), reverse=True)
    return hits[0] if hits else None


def infer_start_context(item: dict) -> dict:
    """
    Infer start context from a recording index item.

    Returns:
      {
        "type": "home" | "app" | "unknown",
        "package": Optional[str],
        "component": Optional[str],
        "deeplink": Optional[str],
        "force_stop": bool,
        "wait_s": float,
      }
    """
    start_type = item.get("start_context_type")
    # If missing, infer from flags/fields we already persisted historically.
    if not start_type:
        if item.get("reset_home") is True:
            start_type = "home"
        elif item.get("restart_app") is True or item.get("app_package") or item.get("start_focused_package"):
            start_type = "app"
        else:
            start_type = "unknown"

    pkg = item.get("app_package") or item.get("start_focused_package")
    component = item.get("app_component")
    deeplink = item.get("app_deeplink")
    force_stop = bool(item.get("app_force_stop", True))
    try:
        wait_s = float(item.get("app_wait_s", 1.0))
    except Exception:
        wait_s = 1.0

    # If inferred type is home, ignore app launch info.
    if start_type == "home":
        pkg = component = deeplink = None

    return {
        "type": start_type,
        "package": pkg,
        "component": component,
        "deeplink": deeplink,
        "force_stop": force_stop,
        "wait_s": wait_s,
    }


def now_ts() -> float:
    """Unix epoch seconds. Used for index sorting."""
    return time.time()


