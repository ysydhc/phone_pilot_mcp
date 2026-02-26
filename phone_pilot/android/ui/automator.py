#!/usr/bin/env python3
"""
UIAutomator helpers:
- dump current UI hierarchy XML
- parse nodes (text/content-desc/resource-id/bounds)
- find elements and compute tap points

This provides "screen text recognition" without OCR by using Android's accessibility hierarchy.
It works best when the target element exposes `text` or `content-desc` (accessibility label).
"""

from __future__ import annotations

import hashlib
import re
import time as _time
from dataclasses import dataclass, asdict
from typing import Optional

from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner


_DUMP_PATH_RE = re.compile(r"UI hierarchy dumped to:\s*(\S+)")


# ---------------------------------------------------------------------------
# Screen keep-alive: avoid redundant wake checks every step
# ---------------------------------------------------------------------------

_screen_timeout_ms: dict[str, int] = {}      # serial → timeout from system settings
_last_keepalive_ts: dict[str, float] = {}     # serial → last keepalive epoch


def _get_screen_timeout(device_serial: str) -> int:
    """Read system screen_off_timeout (ms). Cached per serial."""
    if device_serial in _screen_timeout_ms:
        return _screen_timeout_ms[device_serial]
    try:
        proc = CommandRunner.run(
            adb_prefix(device_serial) + ["shell", "settings", "get", "system", "screen_off_timeout"],
            check=False, delay_s=0.0, log_output=False, silent=True, timeout_s=3.0,
        )
        val = int((proc.stdout or "").strip())
    except Exception:
        val = 30_000  # default 30s
    _screen_timeout_ms[device_serial] = val
    return val


def keep_screen_alive(device_serial: str) -> None:
    """Lightweight keep-alive: only send WAKEUP when approaching screen timeout."""
    now = _time.time()
    last = _last_keepalive_ts.get(device_serial, 0.0)
    timeout_s = _get_screen_timeout(device_serial) / 1000.0
    # Send keepalive if more than (timeout - 10s) have passed, minimum every 20s
    threshold = max(timeout_s - 10.0, 20.0)
    if now - last < threshold:
        return  # still within safe window
    try:
        CommandRunner.run(
            adb_prefix(device_serial) + ["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
            check=False, delay_s=0.0, log_output=False, silent=True, timeout_s=3.0,
        )
    except Exception:
        pass
    _last_keepalive_ts[device_serial] = now
_BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")


@dataclass
class UINode:
    """Represents a node in the UI hierarchy."""
    package: str | None
    text: str | None
    content_desc: str | None
    hint: str | None
    resource_id: str | None
    class_name: str | None
    bounds: str | None
    clickable: bool | None = None
    enabled: bool | None = None
    focusable: bool | None = None
    focused: bool | None = None
    selected: bool | None = None
    checkable: bool | None = None
    checked: bool | None = None
    long_clickable: bool | None = None
    password: bool | None = None
    scrollable: bool | None = None

    def bounds_tuple(self) -> Optional[tuple[int, int, int, int]]:
        """Parse bounds string to (x1, y1, x2, y2) tuple."""
        if not self.bounds:
            return None
        m = _BOUNDS_RE.match(self.bounds.strip())
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

    def center(self) -> Optional[tuple[int, int]]:
        """Get center point (x, y) of the node."""
        b = self.bounds_tuple()
        if not b:
            return None
        x1, y1, x2, y2 = b
        return int((x1 + x2) / 2), int((y1 + y2) / 2)

    def to_dict(self) -> dict:
        """Convert to dict, including computed center coordinates."""
        d = asdict(self)
        c = self.center()
        if c:
            d["center_x"], d["center_y"] = c
        return d


def dump_ui_xml(
    device_serial: Optional[str],
    *,
    compressed: bool = True,
    wake_and_unlock: bool = True,
    force_home: bool = False,
) -> dict:
    """
    Dump UI hierarchy to XML text (best-effort).

    Strategy:
    1) Try dumping directly to stdout with /dev/tty (some Androids support it).
    2) Fallback to default dump path (usually /sdcard/window_dump.xml) and read it back.

    Args:
        device_serial: Device serial number
        compressed: Use --compressed flag
        wake_and_unlock: Attempt to wake device before dumping
        force_home: Press home before dumping

    Returns:
        Dict with ok, xml, method, and error info
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    # Lightweight keep-alive instead of full wake_and_unlock every step
    if wake_and_unlock:
        keep_screen_alive(device_serial)

    flag = "--compressed" if compressed else ""

    # 1) Try /dev/tty (all internal dump commands are silent)
    cmd1 = adb_prefix(device_serial) + ["shell", "uiautomator", "dump"]
    if flag:
        cmd1.append(flag)
    cmd1.append("/dev/tty")
    proc1 = CommandRunner.run(cmd1, check=False, delay_s=0.0, log_output=False, silent=True, timeout_s=6.0)
    out1 = (proc1.stdout or "").strip()
    if "<hierarchy" in out1:
        idx = out1.find("<hierarchy")
        return {"ok": True, "xml": out1[idx:], "method": "tty", "returncode": proc1.returncode}

    # 2) Fallback: dump to default file path
    cmd2 = adb_prefix(device_serial) + ["shell", "uiautomator", "dump"]
    if flag:
        cmd2.append(flag)
    proc2 = CommandRunner.run(cmd2, check=False, delay_s=0.0, log_output=False, silent=True, timeout_s=6.0)
    out2 = (proc2.stdout or "").strip()
    
    if "null root node" in out2.lower():
        if force_home:
            CommandRunner.run(
                adb_prefix(device_serial) + ["shell", "input", "keyevent", "KEYCODE_HOME"],
                check=False, delay_s=0.2, log_output=False, silent=True,
            )
        proc2 = CommandRunner.run(cmd2, check=False, delay_s=0.0, log_output=False, silent=True, timeout_s=6.0)
        out2 = (proc2.stdout or "").strip()
    m = _DUMP_PATH_RE.search(out2)
    dump_path = m.group(1) if m else "/sdcard/window_dump.xml"

    # Read back the file contents from device
    proc3 = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "cat", dump_path],
        check=False, delay_s=0.0, log_output=False, silent=True, timeout_s=6.0,
    )
    xml = proc3.stdout or ""
    if "<hierarchy" not in xml:
        return {
            "ok": False,
            "error": "uiautomator_dump_failed",
            "method": "file",
            "dump_path": dump_path,
            "dump_stdout": out2[:5000],
            "cat_returncode": proc3.returncode,
            "cat_stderr": (proc3.stderr or "").strip(),
        }
    idx = xml.find("<hierarchy")
    return {
        "ok": True,
        "xml": xml[idx:],
        "method": "file",
        "dump_path": dump_path,
        "returncode": proc2.returncode,
    }


def _attr(xml: str, key: str) -> Optional[str]:
    """Extract attribute value from XML tag."""
    m = re.search(rf'\b{re.escape(key)}="([^"]*)"', xml)
    if not m:
        return None
    val = m.group(1)
    return val if val != "" else None


def parse_uiautomator_nodes(xml: str) -> list[UINode]:
    """
    Parse <node ...> tags from uiautomator XML output.

    The dump is often a single long line, so we use regex over the whole string.
    """
    nodes: list[UINode] = []
    text = xml or ""
    for m in re.finditer(r"<node\b[^>]*>", text):
        tag = m.group(0)
        def _bool_attr(k: str) -> bool | None:
            """Parse boolean attribute value from XML tag."""
            v = _attr(tag, k)
            if v is None:
                return None
            vv = v.strip().lower()
            if vv == "true":
                return True
            if vv == "false":
                return False
            return None
        sc = _attr(tag, "scrollable")
        if sc is None:
            scrollable = None
        else:
            scrollable = sc.strip().lower() == "true"
        nodes.append(
            UINode(
                package=_attr(tag, "package"),
                text=_attr(tag, "text"),
                content_desc=_attr(tag, "content-desc"),
                hint=_attr(tag, "hint-text") or _attr(tag, "hint"),
                resource_id=_attr(tag, "resource-id"),
                class_name=_attr(tag, "class"),
                bounds=_attr(tag, "bounds"),
                clickable=_bool_attr("clickable"),
                enabled=_bool_attr("enabled"),
                focusable=_bool_attr("focusable"),
                focused=_bool_attr("focused"),
                selected=_bool_attr("selected"),
                checkable=_bool_attr("checkable"),
                checked=_bool_attr("checked"),
                long_clickable=_bool_attr("long-clickable"),
                password=_bool_attr("password"),
                scrollable=scrollable,
            )
        )
    return nodes


def pick_scrollable_bounds(nodes: list[UINode]) -> Optional[tuple[int, int, int, int]]:
    """
    Pick a "best" scrollable container bounds (best-effort).
    Heuristic: choose the scrollable node with the largest area.
    """
    best = None
    best_area = -1
    for n in nodes:
        if n.scrollable is not True:
            continue
        b = n.bounds_tuple()
        if not b:
            continue
        x1, y1, x2, y2 = b
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if area > best_area:
            best_area = area
            best = b
    return best


def infer_scroll_directions(nodes: list[UINode], default_dirs: Optional[list[str]] = None) -> list[str]:
    """
    Infer likely scroll directions based on container classes and bounds.
    Best-effort for home screen paging (horizontal) vs list scrolling (vertical).
    """
    dirs = list(default_dirs or [])
    # Prefer explicit horizontal containers.
    for n in nodes:
        cn = (n.class_name or "").lower()
        if "viewpager" in cn or "horizontalscrollview" in cn:
            return ["left", "right"]

    # Consider scrollable containers by size/ratio.
    best = None
    best_area = -1
    for n in nodes:
        if n.scrollable is not True:
            continue
        b = n.bounds_tuple()
        if not b:
            continue
        x1, y1, x2, y2 = b
        area = max(0, x2 - x1) * max(0, y2 - y1)
        if area > best_area:
            best_area = area
            best = (n, b)

    if best:
        node, (x1, y1, x2, y2) = best
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        cn = (node.class_name or "").lower()
        if "recyclerview" in cn or "listview" in cn or "scrollview" in cn:
            return ["up", "down"] if h >= w else ["left", "right"]
        if w >= int(h * 1.2):
            return ["left", "right"]
        if h >= int(w * 1.2):
            return ["up", "down"]

    return dirs or ["left", "right"]


def ui_signature(nodes: list[UINode]) -> str:
    """
    Build a stable-ish signature for the current UI tree (best-effort).

    Goal: detect "no further scrolling" by comparing signatures before/after swipe.
    """
    parts: list[str] = []
    for n in nodes:
        parts.append(
            "|".join(
                [
                    n.class_name or "",
                    n.resource_id or "",
                    n.text or "",
                    n.content_desc or "",
                    n.bounds or "",
                    "1" if n.scrollable is True else ("0" if n.scrollable is False else ""),
                ]
            )
        )
    raw = "\n".join(parts).encode("utf-8", errors="replace")
    return hashlib.sha1(raw).hexdigest()


def find_nodes(
    nodes: list[UINode],
    *,
    query: str,
    field: str = "auto",
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> list[UINode]:
    """
    Find nodes by text/hint/desc/resource-id/class.

    Matching priority (high -> low):
    1) text / hint
    2) content-desc
    3) resource-id
    4) class

    Args:
        nodes: List of UINode to search
        query: Search query string
        field: Deprecated (kept for compatibility). Matching is automatic.
        exact: Require exact match
        case_sensitive: Case-sensitive matching
        limit: Maximum results to return
    """
    q = (query or "")
    if not q:
        return []
    def norm(s: Optional[str]) -> str:
        """Normalize string for matching (case-fold if needed)."""
        if s is None:
            return ""
        return s if case_sensitive else s.lower()

    qq = q if case_sensitive else q.lower()

    def match(val: Optional[str]) -> bool:
        """Check whether value matches query with current settings."""
        v = norm(val)
        if not v:
            return False
        if exact:
            return v == qq
        return qq in v

    ranked: list[tuple[int, int, UINode]] = []
    for idx, n in enumerate(nodes):
        if match(n.text) or match(n.hint):
            ranked.append((0, idx, n))
        elif match(n.content_desc):
            ranked.append((1, idx, n))
        elif match(n.resource_id):
            ranked.append((2, idx, n))
        elif match(n.class_name):
            ranked.append((3, idx, n))

    ranked.sort(key=lambda x: (x[0], x[1]))
    hits = [n for _, _, n in ranked]
    if hits:
        if limit:
            return hits[: int(limit)]
        return hits

    # Fuzzy fallback when no direct match.
    qq_len = len(qq)
    if qq_len < 2:
        return []

    import difflib

    def sim(a: Optional[str]) -> float:
        """Compute fuzzy similarity between value and query."""
        v = norm(a)
        if not v:
            return 0.0
        return difflib.SequenceMatcher(None, v, qq).ratio()

    FUZZY_THRESHOLD = 0.5 if qq_len <= 4 else 0.6
    fuzzy_ranked: list[tuple[int, float, int, UINode]] = []
    for idx, n in enumerate(nodes):
        score = max(sim(n.text), sim(n.hint))
        if score >= FUZZY_THRESHOLD:
            fuzzy_ranked.append((0, score, idx, n))
            continue
        score = sim(n.content_desc)
        if score >= FUZZY_THRESHOLD:
            fuzzy_ranked.append((1, score, idx, n))
            continue
        score = sim(n.resource_id)
        if score >= FUZZY_THRESHOLD:
            fuzzy_ranked.append((2, score, idx, n))
            continue
        score = sim(n.class_name)
        if score >= FUZZY_THRESHOLD:
            fuzzy_ranked.append((3, score, idx, n))

    if not fuzzy_ranked:
        return []
    fuzzy_ranked.sort(key=lambda x: (x[0], -x[1], x[2]))
    hits = [n for _, _, _, n in fuzzy_ranked]
    if limit:
        return hits[: int(limit)]
    return hits


__all__ = [
    "UINode",
    "dump_ui_xml",
    "parse_uiautomator_nodes",
    "pick_scrollable_bounds",
    "infer_scroll_directions",
    "ui_signature",
    "find_nodes",
]
