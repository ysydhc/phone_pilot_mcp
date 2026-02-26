#!/usr/bin/env python3
"""
Persistence helpers for recordings and artifacts:
- stable folder naming
- meta.json read/write
- index.json append + fold into latest state
- lookup recordings by key (id/name/substring)
- infer replay start context
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
import time
from typing import Any, Optional


def now_dirname() -> str:
    """Generate a timestamp-based directory name (YYYYMMDD_HHMMSS)."""
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def ensure_abs(path: str) -> pathlib.Path:
    """Expand and resolve a path to absolute."""
    return pathlib.Path(path).expanduser().resolve()


def recordings_root(out_dir: Optional[str] = None) -> pathlib.Path:
    """
    Resolve the recordings root directory.

    Rules:
    - If PHONE_PILOT_CACHE_DIR is set, use <CACHE_DIR>/.recordings
    - If out_dir is provided and not a default .recordings path, use it as-is
    - If running inside this repo, default to <repo_root>/.recordings
    - If running from an external project, default to <cwd>/.recordings
    """
    if out_dir:
        od = str(out_dir).strip()
        if od and od not in ("./.recordings", ".recordings"):
            return ensure_abs(od)

    env_root = os.getenv("PHONE_PILOT_CACHE_DIR")
    if env_root:
        return ensure_abs(str(pathlib.Path(env_root) / ".recordings"))

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    cwd = pathlib.Path(os.getcwd()).resolve()
    if repo_root in cwd.parents or cwd == repo_root:
        return (repo_root / ".recordings").resolve()

    return (cwd / ".recordings").resolve()


def iso_now() -> str:
    """Current time in ISO format (seconds precision)."""
    return _dt.datetime.now().isoformat(timespec="seconds")


def safe_name(name: str) -> str:
    """
    Sanitize a name for filesystem use.

    Replaces non-word characters with underscores, truncates to 80 chars.
    """
    cleaned = re.sub(r"[^\w\-\.]+", "_", name.strip(), flags=re.UNICODE).strip("_")
    return cleaned[:80] if cleaned else "recording"


def index_path(out_root: pathlib.Path) -> pathlib.Path:
    """Get the index.json path for a recordings directory."""
    return out_root / "index.json"


def index_jsonl_path(out_root: pathlib.Path) -> pathlib.Path:
    """Legacy index.jsonl path (deprecated)."""
    return out_root / "index.jsonl"


def write_json(path: pathlib.Path, obj: dict) -> None:
    """Write a dict to a JSON file with pretty printing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: pathlib.Path) -> dict:
    """Read and parse a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: pathlib.Path, obj: dict) -> None:
    """Append a JSON object as a single line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read_jsonl_records(path: pathlib.Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return records
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except Exception:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    return records


def migrate_index_jsonl(out_root: pathlib.Path) -> None:
    """
    Migrate recordings/index.jsonl -> recordings/index.json (array) if needed.
    """
    idx = index_path(out_root)
    legacy = index_jsonl_path(out_root)
    migrate_jsonl_to_json(idx, legacy)


def migrate_jsonl_to_json(index_file: pathlib.Path, legacy_jsonl: pathlib.Path) -> None:
    """
    Migrate legacy jsonl index file to json array format.
    """
    if index_file.exists() or not legacy_jsonl.exists():
        return
    records = _read_jsonl_records(legacy_jsonl)
    write_json(index_file, {"records": records})
    try:
        legacy_jsonl.unlink()
    except Exception:
        pass


def append_index_record(index_file: pathlib.Path, obj: dict) -> None:
    """
    Append an index record to a JSON index file (array payload).
    """
    index_file.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if index_file.exists():
        try:
            data = read_json(index_file)
        except Exception:
            data = {}
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        records = []
    records.append(obj)
    write_json(index_file, {"records": records})


def fold_index_records(index_file: pathlib.Path) -> dict[str, dict]:
    """
    Read index.json (or legacy index.jsonl) and fold into latest state per recording_id.

    Args:
        index_file: Path to index.json

    Returns:
        Dict mapping recording_id to merged record dict
    """
    latest: dict[str, dict] = {}
    if not index_file.exists():
        return latest
    records: list[dict] = []
    if index_file.name.endswith(".jsonl"):
        records = _read_jsonl_records(index_file)
    else:
        try:
            data = read_json(index_file)
            if isinstance(data, dict) and isinstance(data.get("records"), list):
                records = [r for r in data.get("records") if isinstance(r, dict)]
            elif isinstance(data, list):
                records = [r for r in data if isinstance(r, dict)]
        except Exception:
            records = []
    for rec in records:
        rid = rec.get("recording_id")
        if not rid:
            continue
        prev = latest.get(rid, {})
        merged = {**prev, **rec}
        latest[rid] = merged
    return latest


def load_recording_by_key(out_root: pathlib.Path, key: str) -> Optional[dict]:
    """
    Load a recording entry from index.json by:
    - recording_id (exact match)
    - exact name match
    - name/tags substring match (fallback, newest)

    Args:
        out_root: Root directory containing index.json
        key: Search key (id, name, or substring)

    Returns:
        Recording dict or None if not found
    """
    migrate_index_jsonl(out_root)
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


# Export all public functions for convenience
__all__ = [
    "now_dirname",
    "ensure_abs",
    "recordings_root",
    "iso_now",
    "safe_name",
    "index_path",
    "index_jsonl_path",
    "write_json",
    "read_json",
    "append_jsonl",
    "append_index_record",
    "migrate_index_jsonl",
    "migrate_jsonl_to_json",
    "fold_index_records",
    "load_recording_by_key",
    "infer_start_context",
    "now_ts",
]
