#!/usr/bin/env python3
"""
Resource cache management for image assets.
Supports add/update/delete/get/list and @res:<key> resolution.
"""

from __future__ import annotations

import pathlib
import re
import shutil
from typing import Any, Optional

from phone_pilot.core.storage import ensure_abs, iso_now, read_json, recordings_root, write_json

RES_PREFIX = "@res:"
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _snake_case(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return ""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s.lower()


def resource_cache_dir(out_dir: Optional[str] = None) -> pathlib.Path:
    return recordings_root(out_dir) / "cache" / "pic"


def resource_index_path(out_dir: Optional[str] = None) -> pathlib.Path:
    return resource_cache_dir(out_dir) / "index.json"


def _load_index(index_file: pathlib.Path) -> list[dict]:
    if not index_file.exists():
        return []
    try:
        data = read_json(index_file)
    except Exception:
        return []
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def _write_index(index_file: pathlib.Path, records: list[dict]) -> None:
    write_json(index_file, {"records": records})


def _find_record(records: list[dict], key: str) -> tuple[int, Optional[dict]]:
    for idx, rec in enumerate(records):
        if rec.get("key") == key:
            return idx, rec
    return -1, None


def is_res_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(RES_PREFIX)


def resolve_res_ref(value: str, *, out_dir: Optional[str] = None) -> str:
    if not is_res_ref(value):
        return value
    key = value[len(RES_PREFIX) :].strip()
    if not key:
        raise ValueError("empty_res_key")
    rec = res_get(key, out_dir=out_dir)
    if not rec.get("ok"):
        raise ValueError(rec.get("error") or "resource_not_found")
    record = rec.get("record") if isinstance(rec, dict) else None
    if not isinstance(record, dict):
        raise ValueError("resource_not_found")
    return str(record.get("cached_path") or "")


def _is_image_path(path: pathlib.Path) -> bool:
    return path.suffix.lower() in _IMAGE_EXTS


def resolve_or_cache_path(value: str, *, out_dir: Optional[str] = None) -> str:
    if not value:
        return value
    if is_res_ref(value):
        return resolve_res_ref(value, out_dir=out_dir)
    p = pathlib.Path(value).expanduser()
    if not p.is_absolute():
        try:
            p = p.resolve()
        except Exception:
            pass
    if p.exists() and p.is_file() and _is_image_path(p):
        res = res_add(str(p), out_dir=out_dir)
        if not isinstance(res, dict) or not res.get("ok"):
            raise ValueError((res or {}).get("error") or "resource_cache_failed")
        record = res.get("record") if isinstance(res, dict) else None
        if not isinstance(record, dict):
            raise ValueError("resource_cache_failed")
        return str(record.get("cached_path") or "")
    return value


def res_add(
    path: str,
    *,
    key: Optional[str] = None,
    out_dir: Optional[str] = None,
    meta: Optional[dict] = None,
) -> dict:
    if not path:
        return {"ok": False, "error": "path_required"}
    origin = ensure_abs(path)
    if not origin.exists():
        return {"ok": False, "error": "file_not_found", "path": str(origin)}
    key0 = (key or _snake_case(origin.stem)).strip()
    if not key0:
        return {"ok": False, "error": "invalid_key"}

    cache_dir = resource_cache_dir(out_dir)
    index_file = resource_index_path(out_dir)
    records = _load_index(index_file)
    idx, existing = _find_record(records, key0)
    if existing and existing.get("origin_path") != str(origin):
        return {"ok": False, "error": "key_conflict", "key": key0}

    ext = origin.suffix.lower()
    cached_name = f"{key0}{ext}" if ext else key0
    cached_path = cache_dir / cached_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        same = origin.resolve() == cached_path.resolve()
    except Exception:
        same = False
    if not same:
        shutil.copy2(origin, cached_path)

    created_at = existing.get("created_at") if existing else None
    if not created_at:
        created_at = iso_now()

    record = {
        "key": key0,
        "origin_path": str(origin),
        "cached_path": str(cached_path),
        "ext": ext,
        "created_at": created_at,
        "updated_at": iso_now(),
    }
    if meta is not None:
        record["meta"] = meta

    if existing:
        records[idx] = record
    else:
        records.append(record)
    _write_index(index_file, records)
    return {"ok": True, "record": record}


def res_update(
    key: str,
    *,
    path: Optional[str] = None,
    new_key: Optional[str] = None,
    out_dir: Optional[str] = None,
    meta: Optional[dict] = None,
) -> dict:
    if not key:
        return {"ok": False, "error": "key_required"}
    cache_dir = resource_cache_dir(out_dir)
    index_file = resource_index_path(out_dir)
    records = _load_index(index_file)
    idx, existing = _find_record(records, key)
    if not existing:
        return {"ok": False, "error": "resource_not_found", "key": key}

    target_key = (new_key or key).strip()
    if not target_key:
        return {"ok": False, "error": "invalid_key"}
    if target_key != key:
        _, conflict = _find_record(records, target_key)
        if conflict:
            return {"ok": False, "error": "key_conflict", "key": target_key}

    origin_path = existing.get("origin_path")
    origin = ensure_abs(path) if path else (ensure_abs(origin_path) if origin_path else None)
    if path and (origin is None or not origin.exists()):
        return {"ok": False, "error": "file_not_found", "path": str(origin) if origin else path}

    ext = (origin.suffix.lower() if origin else (existing.get("ext") or ""))
    cached_name = f"{target_key}{ext}" if ext else target_key
    cached_path = cache_dir / cached_name
    cache_dir.mkdir(parents=True, exist_ok=True)

    old_cached_path = existing.get("cached_path")
    if origin and path:
        shutil.copy2(origin, cached_path)
    elif target_key != key and old_cached_path:
        old_path = pathlib.Path(old_cached_path)
        if old_path.exists():
            old_path.replace(cached_path)
    elif origin and old_cached_path and not pathlib.Path(old_cached_path).exists():
        shutil.copy2(origin, cached_path)

    if target_key != key and old_cached_path:
        old_path = pathlib.Path(old_cached_path)
        if old_path.exists() and old_path != cached_path:
            try:
                old_path.unlink()
            except Exception:
                pass

    record = {
        "key": target_key,
        "origin_path": str(origin) if origin else str(origin_path or ""),
        "cached_path": str(cached_path),
        "ext": ext,
        "created_at": existing.get("created_at") or iso_now(),
        "updated_at": iso_now(),
    }
    if meta is not None:
        record["meta"] = meta
    elif "meta" in existing:
        record["meta"] = existing.get("meta")

    records[idx] = record
    _write_index(index_file, records)
    return {"ok": True, "record": record}


def res_delete(
    key: str,
    *,
    out_dir: Optional[str] = None,
    delete_file: bool = True,
) -> dict:
    if not key:
        return {"ok": False, "error": "key_required"}
    index_file = resource_index_path(out_dir)
    records = _load_index(index_file)
    idx, existing = _find_record(records, key)
    if not existing:
        return {"ok": False, "error": "resource_not_found", "key": key}

    records.pop(idx)
    _write_index(index_file, records)

    cached_path = existing.get("cached_path")
    if delete_file and cached_path:
        try:
            pathlib.Path(cached_path).unlink()
        except Exception:
            pass

    return {"ok": True, "deleted": existing}


def res_get(key: str, *, out_dir: Optional[str] = None) -> dict:
    if not key:
        return {"ok": False, "error": "key_required"}
    index_file = resource_index_path(out_dir)
    records = _load_index(index_file)
    _, existing = _find_record(records, key)
    if not existing:
        return {"ok": False, "error": "resource_not_found", "key": key}
    return {"ok": True, "record": existing}


def res_list(*, out_dir: Optional[str] = None) -> dict:
    index_file = resource_index_path(out_dir)
    records = _load_index(index_file)
    return {"ok": True, "records": records, "count": len(records)}


def res_resolve(value: str, *, out_dir: Optional[str] = None) -> dict:
    if not value:
        return {"ok": False, "error": "value_required"}
    if not is_res_ref(value):
        return {"ok": True, "value": value, "resolved": value, "cached": False}
    try:
        resolved = resolve_res_ref(value, out_dir=out_dir)
        return {"ok": True, "value": value, "resolved": resolved, "cached": True}
    except Exception as e:
        return {"ok": False, "error": str(e), "value": value}


__all__ = [
    "RES_PREFIX",
    "is_res_ref",
    "resolve_res_ref",
    "resolve_or_cache_path",
    "resource_cache_dir",
    "resource_index_path",
    "res_add",
    "res_update",
    "res_delete",
    "res_get",
    "res_list",
    "res_resolve",
]
