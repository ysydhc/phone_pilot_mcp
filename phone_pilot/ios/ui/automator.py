"""
WDA source → universal representation.

Parses WebDriverAgent's `/source?format=json` tree into a flat list of element
dicts (each carrying center_x/center_y, the cross-platform contract from
protocols.py:99) and maps them to the universal UINode.

WDA term → universal term:
  name        → resource_id   (accessibility id)
  label/value → text
  type        → class_name    (e.g. XCUIElementTypeButton)
"""

from __future__ import annotations

import json
from typing import Union

from phone_pilot.core.ui_node import UINode


def _as_dict(source: Union[str, dict]) -> dict:
    """Accept a JSON string or an already-parsed dict."""
    if isinstance(source, str):
        try:
            return json.loads(source)
        except (ValueError, TypeError):
            return {}
    return source if isinstance(source, dict) else {}


def _rect_center(rect: dict) -> tuple[int, int]:
    """Compute (center_x, center_y) from a WDA rect dict."""
    x = int(rect.get("x", 0))
    y = int(rect.get("y", 0))
    w = int(rect.get("width", 0))
    h = int(rect.get("height", 0))
    return (x + w // 2, y + h // 2)


def _scale_rect(rect: dict, scale: float) -> dict:
    """Scale a WDA rect dict (in points) to physical pixels."""
    if scale == 1.0 or not rect:
        return rect
    return {
        "x": int(round(rect.get("x", 0) * scale)),
        "y": int(round(rect.get("y", 0) * scale)),
        "width": int(round(rect.get("width", 0) * scale)),
        "height": int(round(rect.get("height", 0) * scale)),
    }


def _bounds_str(rect: dict) -> str:
    """Build an Android-style "[x1,y1][x2,y2]" bounds string from a WDA rect."""
    x = int(rect.get("x", 0))
    y = int(rect.get("y", 0))
    w = int(rect.get("width", 0))
    h = int(rect.get("height", 0))
    return f"[{x},{y}][{x + w},{y + h}]"


def parse_wda_source(source: Union[str, dict], scale: float = 1.0) -> list[dict]:
    """
    Flatten a WDA source tree into a list of element dicts.

    Each dict carries the raw WDA fields plus top-level center_x / center_y
    and a bounds string (cross-platform contract). When scale != 1.0, rect and
    coordinates are converted from WDA points to physical pixels.
    """
    root = _as_dict(source)
    out: list[dict] = []

    def _walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        raw_rect = node.get("rect", {}) or {}
        rect = _scale_rect(raw_rect, scale) if scale != 1.0 else raw_rect
        cx, cy = _rect_center(rect)
        elem = {
            "type": node.get("type", ""),
            "name": node.get("name") or "",
            "label": node.get("label") or "",
            "value": node.get("value") or "",
            "isEnabled": bool(node.get("isEnabled", True)),
            "rect": rect,
            "bounds": _bounds_str(rect),
            "center_x": cx,
            "center_y": cy,
        }
        out.append(elem)
        for child in node.get("children", []) or []:
            _walk(child)

    # Skip the synthetic Application root from the flattened element list,
    # but still descend into its children.
    if root.get("type") == "Application":
        for child in root.get("children", []) or []:
            _walk(child)
    else:
        _walk(root)
    return out


def wda_node_to_universal(elem: dict) -> UINode:
    """Map a parsed WDA element dict to the universal UINode."""
    text = elem.get("label") or elem.get("value") or ""
    return UINode(
        text=text,
        content_desc=elem.get("label", "") or "",
        resource_id=elem.get("name", "") or "",
        class_name=elem.get("type", "") or "",
        bounds=elem.get("bounds", "") or "",
        enabled=bool(elem.get("isEnabled", True)),
        _center_x=int(elem.get("center_x", 0)),
        _center_y=int(elem.get("center_y", 0)),
    )


def wda_source_to_ui_nodes(
    source: Union[str, dict], scale: float = 1.0
) -> list[UINode]:
    """Convert a full WDA source tree to a universal UINode list."""
    return [wda_node_to_universal(e) for e in parse_wda_source(source, scale=scale)]


def collect_texts(source: Union[str, dict], scale: float = 1.0) -> list[str]:
    """Collect visible text (label / value / name) from a WDA source tree."""
    texts: list[str] = []
    for elem in parse_wda_source(source, scale=scale):
        for key in ("label", "value", "name"):
            v = elem.get(key)
            if v and v not in texts:
                texts.append(v)
    return texts


def match_selector(elem: dict, selector: dict) -> bool:
    """
    Test whether a parsed WDA element matches a platform-agnostic selector.

    Supported keys: text, text_contains, desc, desc_contains, resource_id,
    class_name, enabled.
    """
    label = elem.get("label", "") or ""
    value = elem.get("value", "") or ""
    name = elem.get("name", "") or ""
    cls = elem.get("type", "") or ""
    text_blob = label or value

    for key, want in selector.items():
        if key == "text":
            if text_blob != want and label != want and value != want:
                return False
        elif key == "text_contains":
            if want not in label and want not in value:
                return False
        elif key in ("desc", "content_desc"):
            if label != want:
                return False
        elif key in ("desc_contains", "content_desc_contains"):
            if want not in label:
                return False
        elif key == "resource_id":
            if name != want:
                return False
        elif key == "class_name":
            if cls != want:
                return False
        elif key == "enabled":
            if bool(elem.get("isEnabled", True)) != bool(want):
                return False
        # unknown selector keys are ignored (best-effort)
    return True


def find_in_source(
    source: Union[str, dict], selector: dict, scale: float = 1.0
) -> list[dict]:
    """Return all parsed elements matching the selector (each has center_x/y)."""
    if not selector:
        return []
    return [
        e for e in parse_wda_source(source, scale=scale) if match_selector(e, selector)
    ]


def selector_to_wda_predicate(selector: dict) -> str:
    """
    Translate a platform-agnostic selector to a WDA NSPredicate string.

    Best-effort; host-side filtering via match_selector remains the source of
    truth. Provided for callers that want server-side WDA querying.
    """
    clauses: list[str] = []
    for key, want in selector.items():
        if key == "text":
            clauses.append(f'label == "{want}"')
        elif key == "text_contains":
            clauses.append(f'label CONTAINS "{want}"')
        elif key == "resource_id":
            clauses.append(f'name == "{want}"')
        elif key == "class_name":
            clauses.append(f'type == "{want}"')
    return " AND ".join(clauses)


__all__ = [
    "parse_wda_source",
    "wda_node_to_universal",
    "wda_source_to_ui_nodes",
    "collect_texts",
    "match_selector",
    "find_in_source",
    "selector_to_wda_predicate",
]
