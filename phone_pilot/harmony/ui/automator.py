"""
HarmonyOS UI hierarchy operations (powered by hmdriver2).

Provides dump_hierarchy, node parsing, and element finding functions
that mirror the Android UIAutomator interface.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from phone_pilot.harmony.hmdriver_bridge import get_hmdriver

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures (aligned with Android UINode)
# ---------------------------------------------------------------------------


@dataclass
class HarmonyUINode:
    """Represents a node in the HarmonyOS UI hierarchy."""

    text: str = ""
    description: str = ""  # content-desc equivalent
    type: str = ""  # class name equivalent
    id: str = ""  # resource-id equivalent
    synthetic_id: str = ""  # synthesized fallback identifier for ArkUI components
    key: str = ""
    bounds: str = ""  # "[left,top][right,bottom]" format for compat
    bounds_raw: dict = field(default_factory=dict)
    clickable: bool = False
    scrollable: bool = False
    enabled: bool = True
    focused: bool = False
    selected: bool = False
    checked: bool = False
    checkable: bool = False
    long_clickable: bool = False
    center_x: int = 0
    center_y: int = 0

    def bounds_tuple(self) -> tuple[int, int, int, int]:
        """Parse bounds to (left, top, right, bottom)."""
        br = self.bounds_raw
        if br:
            return (
                br.get("left", 0),
                br.get("top", 0),
                br.get("right", 0),
                br.get("bottom", 0),
            )
        # Try parse string format [x1,y1][x2,y2]
        m = re.findall(r"\d+", self.bounds)
        if len(m) == 4:
            return tuple(int(x) for x in m)
        return (0, 0, 0, 0)

    def center(self) -> tuple[int, int]:
        """Get center point."""
        if self.center_x and self.center_y:
            return (self.center_x, self.center_y)
        left, t, r, b = self.bounds_tuple()
        return ((left + r) // 2, (t + b) // 2)

    def to_dict(self) -> dict:
        """Convert to dict with center coordinates."""
        cx, cy = self.center()
        return {
            "text": self.text,
            "description": self.description,
            "type": self.type,
            "id": self.id,
            "synthetic_id": self.synthetic_id or self.id,
            "key": self.key,
            "bounds": self.bounds,
            "clickable": self.clickable,
            "scrollable": self.scrollable,
            "enabled": self.enabled,
            "focused": self.focused,
            "selected": self.selected,
            "checked": self.checked,
            "center_x": cx,
            "center_y": cy,
        }


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def dump_hierarchy_dict(device_serial: str) -> dict:
    """Dump UI hierarchy as a Python dict (native hmdriver2 format)."""
    hm = get_hmdriver(device_serial)
    return hm.dump_hierarchy()


def dump_hierarchy_json(device_serial: str) -> str:
    """Dump UI hierarchy as a JSON string."""
    d = dump_hierarchy_dict(device_serial)
    return json.dumps(d, ensure_ascii=False, indent=2)


def parse_hierarchy_nodes(hierarchy: dict) -> list[HarmonyUINode]:
    """
    Recursively parse hmdriver2 hierarchy dict into a flat list of HarmonyUINode.

    The hmdriver2 hierarchy format is a nested dict with 'children' arrays.
    Each node has attributes like 'text', 'type', 'id', 'bounds', etc.
    """
    nodes = []
    _walk_node(hierarchy, nodes)
    return nodes


def _walk_node(node: Any, result: list[HarmonyUINode]):
    """Recursively walk hierarchy nodes."""
    if not isinstance(node, dict):
        return

    # Extract node attributes
    attributes = node.get("attributes", node)
    if isinstance(attributes, dict):
        left = 0
        top = 0
        right = 0
        bottom = 0
        bounds_raw = {}

        bounds_info = attributes.get("bounds", {})
        if isinstance(bounds_info, dict):
            left = bounds_info.get("left", 0) or 0
            top = bounds_info.get("top", 0) or 0
            right = bounds_info.get("right", 0) or 0
            bottom = bounds_info.get("bottom", 0) or 0
            bounds_str = f"[{left},{top}][{right},{bottom}]"
            bounds_raw = bounds_info
        elif isinstance(bounds_info, (list, tuple)) and len(bounds_info) >= 4:
            left, top, right, bottom = [int(v) for v in bounds_info[:4]]
            bounds_str = f"[{left},{top}][{right},{bottom}]"
            bounds_raw = {"left": left, "top": top, "right": right, "bottom": bottom}
        else:
            bounds_str = str(bounds_info or "")
            bounds_raw = {}

        center = attributes.get("boundsCenter", {})
        cx = center.get("x", 0) if isinstance(center, dict) else 0
        cy = center.get("y", 0) if isinstance(center, dict) else 0

        node_text = str(attributes.get("text", "") or "")
        node_desc = str(attributes.get("description", "") or "")
        node_type = str(attributes.get("type", "") or "")
        node_id = str(attributes.get("id", "") or "")
        node_key = str(attributes.get("key", "") or "")

        synth_id = node_id
        if not synth_id:
            if node_key:
                synth_id = f"key:{node_key}"
            elif node_text and node_type:
                synth_id = f"{node_type}[text={node_text[:20]}]"
            elif node_desc and node_type:
                synth_id = f"{node_type}[desc={node_desc[:20]}]"
            elif node_type and (left or top):
                synth_id = f"{node_type}@{left}_{top}"

        ui_node = HarmonyUINode(
            text=node_text,
            description=node_desc,
            type=node_type,
            id=node_id,
            synthetic_id=synth_id,
            key=node_key,
            bounds=bounds_str,
            bounds_raw=bounds_raw,
            clickable=bool(attributes.get("clickable", False)),
            scrollable=bool(attributes.get("scrollable", False)),
            enabled=bool(attributes.get("enabled", True)),
            focused=bool(attributes.get("focused", False)),
            selected=bool(attributes.get("selected", False)),
            checked=bool(attributes.get("checked", False)),
            checkable=bool(attributes.get("checkable", False)),
            long_clickable=bool(attributes.get("longClickable", False)),
            center_x=int(cx) if cx else 0,
            center_y=int(cy) if cy else 0,
        )

        # Only add nodes that have some identifiable content
        if ui_node.text or ui_node.description or ui_node.type or ui_node.id or ui_node.synthetic_id:
            result.append(ui_node)

    # Recurse into children
    children = node.get("children", [])
    if isinstance(children, list):
        for child in children:
            _walk_node(child, result)


def find_nodes(
    nodes: Sequence[HarmonyUINode],
    query: str,
    *,
    field: str = "text",
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 0,
) -> list[HarmonyUINode]:
    """
    Find nodes matching a query string.

    Args:
        nodes: List of HarmonyUINode to search.
        query: Search string.
        field: Field to search in: "text", "description", "id", "type", "key",
               or "any" to search all fields.
        exact: If True, require exact match; otherwise substring match.
        case_sensitive: Whether to be case-sensitive.
        limit: Max results (0 = unlimited).

    Returns:
        List of matching HarmonyUINode.
    """
    results = []
    q = query if case_sensitive else query.lower()

    for node in nodes:
        if field == "any":
            candidates = [node.text, node.description, node.id, node.synthetic_id, node.type, node.key]
        elif field == "id":
            candidates = [node.id, node.synthetic_id]
        else:
            candidates = [getattr(node, field, "")]

        for val in candidates:
            v = str(val or "")
            if not case_sensitive:
                v = v.lower()
            matched = (v == q) if exact else (q in v)
            if matched:
                results.append(node)
                break

        if limit and len(results) >= limit:
            break

    return results


def pick_scrollable_bounds(
    nodes: Sequence[HarmonyUINode],
) -> Optional[tuple[int, int, int, int]]:
    """Pick the best scrollable container bounds."""
    scrollables = [n for n in nodes if n.scrollable]
    if not scrollables:
        return None
    # Pick the largest scrollable area
    best = None
    best_area = 0
    for n in scrollables:
        left, t, r, b = n.bounds_tuple()
        area = (r - left) * (b - t)
        if area > best_area:
            best = (left, t, r, b)
            best_area = area
    return best


def ui_signature(nodes: Sequence[HarmonyUINode]) -> str:
    """Build a stable UI tree signature for change detection."""
    parts = []
    for n in nodes[:50]:  # Limit to first 50 nodes for performance
        parts.append(f"{n.type}:{n.text[:20]}:{n.bounds}")
    return "|".join(parts)


def collect_node_texts(nodes: Sequence[HarmonyUINode]) -> list[str]:
    """Collect visible text from nodes."""
    texts = []
    for n in nodes:
        if n.text:
            texts.append(n.text)
        if n.description and n.description != n.text:
            texts.append(n.description)
    return texts


__all__ = [
    "HarmonyUINode",
    "dump_hierarchy_dict",
    "dump_hierarchy_json",
    "parse_hierarchy_nodes",
    "find_nodes",
    "pick_scrollable_bounds",
    "ui_signature",
    "collect_node_texts",
]
