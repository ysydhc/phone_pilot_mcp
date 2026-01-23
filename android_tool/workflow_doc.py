#!/usr/bin/env python3
"""
Parse a human-facing "test doc" into a structured workflow.

We intentionally avoid adding extra dependencies (e.g. PyYAML). The most stable
format is embedding a JSON block inside Markdown:

```json
{ "name": "...", "device_serial": "...", "steps": [ ... ] }
```
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Optional


_JSON_FENCE_RE = re.compile(r"```json\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _extract_json_from_markdown(text: str) -> Optional[str]:
    m = _JSON_FENCE_RE.search(text or "")
    if not m:
        return None
    return (m.group(1) or "").strip()


def load_workflow_doc_text(*, doc_text: Optional[str] = None, doc_path: Optional[str] = None) -> str:
    if doc_text and str(doc_text).strip():
        return str(doc_text)
    if doc_path and str(doc_path).strip():
        p = pathlib.Path(str(doc_path)).expanduser().resolve()
        return p.read_text(encoding="utf-8")
    return ""


def parse_workflow_doc(text: str) -> dict[str, Any]:
    """
    Parse workflow spec from:
    - raw JSON string
    - or a Markdown code fence ```json ... ```
    """
    raw = (text or "").strip()
    if not raw:
        return {"ok": False, "error": "empty_doc", "hint": "请提供 doc_text 或 doc_path，或在 Markdown 中嵌入 ```json ... ``` 块。"}

    candidate = raw
    if "```" in raw:
        j = _extract_json_from_markdown(raw)
        if j:
            candidate = j

    try:
        obj = json.loads(candidate)
    except Exception as e:
        return {
            "ok": False,
            "error": "json_parse_failed",
            "detail": str(e),
            "hint": "当前仅支持 JSON（或 Markdown 内的 ```json ... ```）。建议把工作流 spec 放到文档的 json 代码块里。",
        }

    if not isinstance(obj, dict):
        return {"ok": False, "error": "invalid_workflow_type", "hint": "JSON 顶层必须是对象（dict）。"}

    # Normalize schema
    wf = dict(obj)
    steps = wf.get("steps")
    if steps is None and isinstance(wf.get("workflow"), dict):
        steps = wf["workflow"].get("steps")
        # merge workflow fields up
        for k, v in wf["workflow"].items():
            wf.setdefault(k, v)
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        return {"ok": False, "error": "invalid_steps", "hint": "`steps` 必须是数组。"}
    wf["steps"] = steps
    return {"ok": True, "workflow": wf}


