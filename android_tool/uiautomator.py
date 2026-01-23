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
from dataclasses import dataclass, asdict
from typing import Optional

from android_tool.adb_utils import adb_prefix
from android_tool.runner import CommandRunner


_DUMP_PATH_RE = re.compile(r"UI hierarchy dumped to:\s*(\S+)")
_BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")


@dataclass
class UINode:
    package: str | None
    text: str | None
    content_desc: str | None
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
        if not self.bounds:
            return None
        m = _BOUNDS_RE.match(self.bounds.strip())
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

    def center(self) -> Optional[tuple[int, int]]:
        b = self.bounds_tuple()
        if not b:
            return None
        x1, y1, x2, y2 = b
        return int((x1 + x2) / 2), int((y1 + y2) / 2)

    def to_dict(self) -> dict:
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
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    # Best-effort: wake/unlock before dumping to avoid "null root node" when screen is off/locked.
    #
    # IMPORTANT: dumping UI should be as non-destructive as possible.
    # In particular, blindly swiping up (even when already unlocked) can scroll the current app UI
    # and cause extremely flaky "find" results. Therefore we:
    # - wake only before the first dump attempt
    # - only do a swipe-unlock retry when the dump indicates "null root node" / missing hierarchy
    #
    # Also, do NOT change the current screen by default (force_home=False).
    if wake_and_unlock:
        try:
            from android_tool.device_lock import wake_and_unlock as _wake_unlock

            # Wake only (no swipe) to avoid perturbing current UI.
            _wake_unlock(device_serial, wakeup=True, swipe=False, pin=None, settle_s=0.15)
        except Exception:
            # Fallback: wake only
            try:
                CommandRunner.run(
                    adb_prefix(device_serial) + ["shell", "input", "keyevent", "KEYCODE_WAKEUP"],
                    check=False,
                    delay_s=0.0,
                    log_output=False,
                )
            except Exception:
                pass

        if force_home:
            try:
                CommandRunner.run(
                    adb_prefix(device_serial) + ["shell", "input", "keyevent", "KEYCODE_HOME"],
                    check=False,
                    delay_s=0.0,
                    log_output=False,
                )
            except Exception:
                pass

    flag = "--compressed" if compressed else ""

    # 1) Try /dev/tty
    cmd1 = adb_prefix(device_serial) + ["shell", "uiautomator", "dump"]
    if flag:
        cmd1.append(flag)
    cmd1.append("/dev/tty")
    proc1 = CommandRunner.run(cmd1, check=False, delay_s=0.0, log_output=False, timeout_s=6.0)
    out1 = (proc1.stdout or "").strip()
    if "<hierarchy" in out1:
        # Some devices print extra lines; extract from first <hierarchy
        idx = out1.find("<hierarchy")
        return {"ok": True, "xml": out1[idx:], "method": "tty", "returncode": proc1.returncode}

    # 2) Fallback: dump to default file path
    cmd2 = adb_prefix(device_serial) + ["shell", "uiautomator", "dump"]
    if flag:
        cmd2.append(flag)
    proc2 = CommandRunner.run(cmd2, check=False, delay_s=0.0, log_output=False, timeout_s=6.0)
    out2 = (proc2.stdout or "").strip()
    # If dump failed with "null root node", it's often because the screen is locked/off.
    # IMPORTANT: do NOT attempt swipe-unlock here.
    # Unlock actions are handled centrally by `android_run_workflow` at workflow start (if locked).
    if "null root node" in out2.lower():
        # Retry once after a small settle (still non-destructive).
        if force_home:
            CommandRunner.run(
                adb_prefix(device_serial) + ["shell", "input", "keyevent", "KEYCODE_HOME"],
                check=False,
                delay_s=0.2,
                log_output=False,
            )
        proc2 = CommandRunner.run(cmd2, check=False, delay_s=0.0, log_output=False, timeout_s=6.0)
        out2 = (proc2.stdout or "").strip()
    m = _DUMP_PATH_RE.search(out2)
    dump_path = m.group(1) if m else "/sdcard/window_dump.xml"

    # Read back the file contents from device
    proc3 = CommandRunner.run(
        adb_prefix(device_serial) + ["shell", "cat", dump_path],
        check=False,
        delay_s=0.0,
        log_output=False,
        timeout_s=6.0,
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
    # Similar: extract from first <hierarchy for safety.
    idx = xml.find("<hierarchy")
    return {
        "ok": True,
        "xml": xml[idx:],
        "method": "file",
        "dump_path": dump_path,
        "returncode": proc2.returncode,
    }


def _attr(xml: str, key: str) -> Optional[str]:
    # Very small, dependency-free attribute extractor.
    # Matches key="..."; not a full XML parser but sufficient for uiautomator output.
    m = re.search(rf'\b{re.escape(key)}="([^"]*)"', xml)
    if not m:
        return None
    val = m.group(1)
    return val if val != "" else None


def parse_uiautomator_nodes(xml: str) -> list[UINode]:
    """
    Parse <node ...> tags from uiautomator XML output.

    The dump is often a single long line, so we use regex over the whole string.
    This is not a full XML parser; we rely on the stable `node` tag attributes.
    """
    nodes: list[UINode] = []
    text = xml or ""
    for m in re.finditer(r"<node\b[^>]*>", text):
        tag = m.group(0)
        def _bool_attr(k: str) -> bool | None:
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


def ui_signature(nodes: list[UINode]) -> str:
    """
    Build a stable-ish signature for the current UI tree (best-effort).

    Goal: detect "no further scrolling" by comparing signatures before/after swipe.
    We intentionally only include a few stable fields to reduce noise.
    """
    parts: list[str] = []
    for n in nodes:
        # Keep order of appearance from the dump; this is usually stable for the same screen.
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
    field: str = "text_or_desc",
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> list[UINode]:
    """
    Find nodes by text/content-desc/resource-id/class.

    field:
    - text
    - desc
    - text_or_desc
    - resource_id
    - class
    """
    q = (query or "")
    if not q:
        return []
    fld = (field or "text_or_desc").strip().lower()
    if fld not in ("text", "desc", "text_or_desc", "resource_id", "class"):
        fld = "text_or_desc"

    def norm(s: Optional[str]) -> str:
        if s is None:
            return ""
        return s if case_sensitive else s.lower()

    qq = q if case_sensitive else q.lower()

    def match(val: Optional[str]) -> bool:
        v = norm(val)
        if not v:
            return False
        if exact:
            return v == qq
        return qq in v

    hits: list[UINode] = []
    for n in nodes:
        ok = False
        if fld == "text":
            ok = match(n.text)
        elif fld == "desc":
            ok = match(n.content_desc)
        elif fld == "resource_id":
            ok = match(n.resource_id)
        elif fld == "class":
            ok = match(n.class_name)
        else:
            ok = match(n.text) or match(n.content_desc)
        if ok:
            hits.append(n)
            if limit and len(hits) >= int(limit):
                break
    return hits


