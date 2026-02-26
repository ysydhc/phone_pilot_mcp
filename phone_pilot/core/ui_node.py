"""
Universal UI node type shared across all platforms.

Both Android (UIAutomator) and HarmonyOS (hmdriver2) drivers convert their
platform-specific node representations into this universal format so that
the Skills / script_api layer never needs to know about platform specifics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional, Sequence

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


@dataclass
class UINode:
    """Universal UI hierarchy node.

    Attributes are a superset of Android UINode and HarmonyOS HarmonyUINode
    so both platforms can be mapped losslessly.
    """

    text: str = ""
    content_desc: str = ""   # Android content-desc / Harmony description
    hint: str = ""           # Android only (input hint text)
    resource_id: str = ""    # Android resource-id / Harmony id
    class_name: str = ""     # Android class / Harmony type
    key: str = ""            # Harmony key
    package: str = ""
    bounds: str = ""         # "[x1,y1][x2,y2]" string for backward compat
    clickable: bool = False
    enabled: bool = True
    focusable: bool = False
    focused: bool = False
    selected: bool = False
    checkable: bool = False
    checked: bool = False
    long_clickable: bool = False
    scrollable: bool = False
    password: bool = False

    # Pre-computed center (populated by drivers when available)
    _center_x: int = 0
    _center_y: int = 0

    # ---- geometry helpers ----

    def bounds_tuple(self) -> Optional[tuple[int, int, int, int]]:
        """Parse bounds to (x1, y1, x2, y2)."""
        if not self.bounds:
            return None
        m = _BOUNDS_RE.search(self.bounds)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

    def center(self) -> Optional[tuple[int, int]]:
        """Get center point (x, y)."""
        if self._center_x and self._center_y:
            return (self._center_x, self._center_y)
        b = self.bounds_tuple()
        if not b:
            return None
        x1, y1, x2, y2 = b
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def to_dict(self) -> dict:
        """Convert to dict with center_x / center_y."""
        d = asdict(self)
        # Remove private fields
        d.pop("_center_x", None)
        d.pop("_center_y", None)
        c = self.center()
        if c:
            d["center_x"], d["center_y"] = c
        return d


def collect_ui_texts(nodes: Sequence[UINode]) -> list[str]:
    """Collect all visible text from a list of UINodes.

    Gathers .text, .content_desc, and .hint (if present).
    """
    texts: list[str] = []
    for n in nodes:
        if n.text:
            texts.append(n.text)
        if n.content_desc and n.content_desc != n.text:
            texts.append(n.content_desc)
        if n.hint and n.hint != n.text and n.hint != n.content_desc:
            texts.append(n.hint)
    return texts
