#!/usr/bin/env python3
"""
Workflow-related tool helpers extracted from mcp_server.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import uuid
from typing import Any, Optional

from mcp.server import FastMCP

from android_tool.recordings_store import append_jsonl, ensure_abs, now_ts, read_json, safe_name, write_json
from android_tool.workflow_doc import load_workflow_doc_text, parse_workflow_doc


def _write_text(path: pathlib.Path, s: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s, encoding="utf-8", errors="replace")


async def android_runlog_summarize(
    *,
    runlog_path: Optional[str] = None,
    workflow_dir: Optional[str] = None,
    top_n: int = 10,
) -> dict:
    if runlog_path is None and workflow_dir is None:
        return {"ok": False, "error": "runlog_path or workflow_dir is required"}

    p: Optional[pathlib.Path] = None
    if runlog_path is not None:
        p = pathlib.Path(str(runlog_path)).expanduser()
    else:
        d = pathlib.Path(str(workflow_dir)).expanduser()
        for c in (d / "run.json", d / "run.jsonl"):
            if c.exists():
                p = c
                break
        if p is None:
            return {"ok": False, "error": "runlog_not_found", "workflow_dir": str(d)}

    if p is None or not p.exists():
        return {"ok": False, "error": "runlog_not_found", "runlog_path": str(p) if p else None}

    events: list[dict] = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    events.append(obj)
    except Exception as e:
        return {"ok": False, "error": "read_failed", "runlog_path": str(p), "detail": str(e)}

    def _ts(e: dict) -> Optional[float]:
        try:
            return float(e.get("ts")) if e.get("ts") is not None else None
        except Exception:
            return None

    first_ts: Optional[float] = None
    last_ts: Optional[float] = None
    t_by_event: dict[str, float] = {}
    for e in events:
        ts = _ts(e)
        if ts is None:
            continue
        if first_ts is None or ts < first_ts:
            first_ts = ts
        if last_ts is None or ts > last_ts:
            last_ts = ts
        ev = e.get("event")
        if isinstance(ev, str) and ev and ev not in t_by_event:
            t_by_event[ev] = ts

    step_start: dict[tuple[int, str], float] = {}
    step_meta: dict[tuple[int, str], dict] = {}
    step_rows: list[dict] = []
    by_type_ms: dict[str, float] = {}

    verify_start: dict[tuple[int, str, int], float] = {}
    verify_meta: dict[tuple[int, str, int], dict] = {}
    verify_by_type_ms: dict[str, float] = {}

    for e in events:
        ev = e.get("event")
        ts = _ts(e)
        if not isinstance(ev, str) or ts is None:
            continue
        if ev == "step_start":
            try:
                idx = int(e.get("step_index"))
            except Exception:
                continue
            sp = str(e.get("step_path") or "")
            key = (idx, sp)
            step_start[key] = ts
            step_meta[key] = {"type": e.get("type"), "name": e.get("name"), "step_index": idx, "step_path": sp}
        elif ev in ("step_end", "step_error"):
            try:
                idx = int(e.get("step_index"))
            except Exception:
                continue
            sp = str(e.get("step_path") or "")
            key = (idx, sp)
            t0 = step_start.get(key)
            if t0 is None:
                continue
            ms = max(0.0, (ts - t0) * 1000.0)
            m = step_meta.get(key, {"step_index": idx, "step_path": sp})
            typ = str(m.get("type") or "")
            row = {**m, "event": ev, "elapsed_ms": ms}
            step_rows.append(row)
            if typ:
                by_type_ms[typ] = float(by_type_ms.get(typ, 0.0)) + ms
        elif ev == "verify_start":
            try:
                idx = int(e.get("step_index"))
                vi = int(e.get("verify_index"))
            except Exception:
                continue
            sp = str(e.get("step_path") or "")
            key = (idx, sp, vi)
            verify_start[key] = ts
            verify_meta[key] = {
                "type": e.get("verify_type"),
                "step_index": idx,
                "step_path": sp,
                "verify_index": vi,
            }
        elif ev == "verify_end":
            try:
                idx = int(e.get("step_index"))
                vi = int(e.get("verify_index"))
            except Exception:
                continue
            sp = str(e.get("step_path") or "")
            key = (idx, sp, vi)
            t0 = verify_start.get(key)
            if t0 is None:
                continue
            ms = max(0.0, (ts - t0) * 1000.0)
            m = verify_meta.get(key, {"step_index": idx, "step_path": sp, "verify_index": vi})
            typ = str(m.get("type") or "")
            if typ:
                verify_by_type_ms[typ] = float(verify_by_type_ms.get(typ, 0.0)) + ms

    phases_ms: dict[str, Optional[float]] = {}
    ws = t_by_event.get("workflow_start")
    we = t_by_event.get("workflow_end")
    if ws is not None and we is not None:
        phases_ms["workflow_elapsed_ms"] = max(0.0, (we - ws) * 1000.0)
    for k in ("screenrecord_start", "screenrecord_stop", "logcat_clear", "device_capture", "final_collect_artifacts"):
        ts0 = t_by_event.get(k)
        phases_ms[k + "_since_workflow_start_ms"] = (max(0.0, (ts0 - ws) * 1000.0) if (ts0 is not None and ws is not None) else None)

    elapsed_ms = phases_ms.get("workflow_elapsed_ms")
    slow_steps = sorted(step_rows, key=lambda r: r.get("elapsed_ms", 0.0), reverse=True)[: max(1, int(top_n))]
    return {
        "ok": True,
        "runlog_path": str(p),
        "event_count": len(events),
        "elapsed_ms": elapsed_ms,
        "phases_ms": phases_ms,
        "steps": step_rows,
        "by_type_ms": by_type_ms,
        "verify_by_type_ms": verify_by_type_ms,
        "slow_steps": slow_steps,
    }


def _workflow_docs_dir(out_root: pathlib.Path) -> pathlib.Path:
    return out_root / "workflow_docs"


def _workflow_docs_index_path(out_root: pathlib.Path) -> pathlib.Path:
    return _workflow_docs_dir(out_root) / "index.jsonl"


async def android_workflow_doc_save(
    *,
    name: str,
    out_dir: str = "./recordings",
    device_serial: Optional[str] = None,
    steps: Optional[list[dict]] = None,
    description: Optional[str] = None,
    compact: bool = True,
) -> dict:
    out_root = ensure_abs(out_dir)
    safe = safe_name(name or "workflow_doc")
    docs_dir = _workflow_docs_dir(out_root)
    docs_dir.mkdir(parents=True, exist_ok=True)
    doc_id = uuid.uuid4().hex[:12]
    doc_path = docs_dir / f"{safe}_{doc_id}.md"

    md_parts = [
        f"# {safe}\n\n",
    ]
    if description:
        md_parts.append(f"{description}\n\n")

    wf: dict[str, Any] = {
        "name": name,
        "device_serial": device_serial,
        "out_dir": out_dir,
        "steps": steps or [],
        "clear_logcat_first": True,
        "final_collect_artifacts": True,
        "logcat_lines": 8000,
    }

    def _strip_none(obj: Any) -> Any:
        if isinstance(obj, dict):
            keys = list(obj.keys())
            for k in keys:
                v = obj.get(k)
                if v is None:
                    obj.pop(k, None)
                else:
                    obj[k] = _strip_none(v)
            return obj
        if isinstance(obj, list):
            return [_strip_none(x) for x in obj]
        return obj

    if compact:
        wf = _strip_none(wf)
        if wf.get("out_dir") == "./recordings":
            wf.pop("out_dir", None)
        if wf.get("clear_logcat_first") is True:
            wf.pop("clear_logcat_first", None)
        if wf.get("final_collect_artifacts") is True:
            wf.pop("final_collect_artifacts", None)
        if wf.get("record_screen") is False:
            wf.pop("record_screen", None)

    md_parts.append("```json\n")
    md_parts.append(json.dumps(wf, ensure_ascii=False, indent=2))
    md_parts.append("\n```\n")

    await asyncio.to_thread(_write_text, doc_path, "".join(md_parts))

    append_jsonl(
        _workflow_docs_index_path(out_root),
        {
            "ts": now_ts(),
            "doc_id": doc_id,
            "name": safe,
            "doc_path": str(doc_path),
        },
    )
    return {"ok": True, "doc_id": doc_id, "name": safe, "doc_path": str(doc_path), "workflow": wf}


async def android_workflow_docs_list(
    *,
    out_dir: str = "./recordings",
    query: Optional[str] = None,
    limit: int = 50,
) -> dict:
    out_root = ensure_abs(out_dir)
    idx = _workflow_docs_index_path(out_root)
    if not idx.exists():
        return {"ok": True, "items": []}
    rows = []
    try:
        with open(idx, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except Exception as e:
        return {"ok": False, "error": "read_failed", "detail": str(e)}
    if query:
        q = str(query).lower()
        rows = [r for r in rows if q in str(r.get("name") or "").lower() or q in str(r.get("doc_path") or "").lower()]
    rows = list(reversed(rows))[: max(1, int(limit))]
    return {"ok": True, "items": rows}


async def android_parse_workflow_doc(
    *,
    doc_text: Optional[str] = None,
    doc_path: Optional[str] = None,
) -> dict:
    if doc_text is None and doc_path is None:
        return {"ok": False, "error": "doc_text or doc_path is required"}
    text = load_workflow_doc_text(doc_text=doc_text, doc_path=doc_path)
    return parse_workflow_doc(text)


def register_workflow_tools(mcp: FastMCP) -> None:
    mcp.tool()(android_runlog_summarize)
    mcp.tool()(android_workflow_doc_save)
    mcp.tool()(android_workflow_docs_list)
    mcp.tool()(android_parse_workflow_doc)
