"""
HarmonyOS element query operations (powered by hmdriver2).

Provides element_query and element_exists that mirror the Android
UI query interface.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Sequence

from phone_pilot.harmony.hmdriver_bridge import get_hmdriver
from phone_pilot.harmony.ui.automator import (
    HarmonyUINode,
    dump_hierarchy_dict,
    parse_hierarchy_nodes,
)

logger = logging.getLogger(__name__)


def element_query(
    device_serial: str,
    selector: dict,
    *,
    attributes: Optional[list[str]] = None,
    case_sensitive: bool = False,
    limit: int = 10,
) -> list[dict]:
    """
    Query UI elements with a selector dict.

    Args:
        device_serial: Device serial.
        selector: Query criteria. Supported keys:
            - text: Exact text match
            - text_contains: Partial text match
            - description: Content description
            - id: Element ID
            - type: Element type/class
            - clickable, enabled, focused, scrollable: Boolean filters
        attributes: If provided, only return these attributes.
        case_sensitive: Case-sensitive matching.
        limit: Max results.

    Returns:
        List of element dicts.
    """
    hm = get_hmdriver(device_serial)

    # Build hmdriver2 kwargs from selector
    hm_kwargs = _selector_to_kwargs(selector)

    results = []

    if hm_kwargs:
        # Try hmdriver2 native query
        try:
            ui_obj = hm(**hm_kwargs)
            count = ui_obj.count()
            for i in range(min(count, limit)):
                comp = hm(**hm_kwargs, index=i).find_component()
                if comp:
                    d = _component_to_result(comp, attributes)
                    results.append(d)
            if results:
                return results
        except Exception as exc:
            logger.debug("Native element_query failed: %s", exc)

    # Fallback: hierarchy dump + search
    try:
        hierarchy = dump_hierarchy_dict(device_serial)
        nodes = parse_hierarchy_nodes(hierarchy)

        # Apply filters
        filtered = _filter_nodes(nodes, selector, case_sensitive=case_sensitive)

        for node in filtered[:limit]:
            d = node.to_dict()
            if attributes:
                d = {k: v for k, v in d.items() if k in attributes}
            results.append(d)
    except Exception as exc:
        logger.warning("element_query hierarchy fallback failed: %s", exc)

    return results


def element_exists(
    device_serial: str,
    query: Optional[str] = None,
    *,
    selector: Optional[dict] = None,
    exact: bool = False,
    timeout_s: float = 5.0,
    interval_s: float = 0.5,
) -> dict:
    """
    Check if a UI element exists on screen.

    Args:
        device_serial: Device serial.
        query: Text to search for (shortcut for selector={"text": query}).
        selector: Full selector dict (overrides query).
        exact: Exact match or substring.
        timeout_s: Max search time.
        interval_s: Interval between retries.

    Returns:
        {"ok": True, "exists": bool, ...}
    """
    hm = get_hmdriver(device_serial)

    if selector is None:
        if query:
            selector = {"text": query} if exact else {"text_contains": query}
        else:
            return {"ok": False, "error": "query or selector required"}

    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        # Try hmdriver2 native check
        hm_kwargs = _selector_to_kwargs(selector)
        if hm_kwargs:
            try:
                ui_obj = hm(**hm_kwargs)
                if ui_obj.exists():
                    comp = ui_obj.find_component()
                    info = {}
                    if comp:
                        try:
                            info = comp.to_dict()
                        except Exception:
                            pass
                    return {
                        "ok": True,
                        "exists": True,
                        "element": info,
                        "engine": "hmdriver2",
                    }
            except Exception:
                pass

        # Fallback: hierarchy
        try:
            hierarchy = dump_hierarchy_dict(device_serial)
            nodes = parse_hierarchy_nodes(hierarchy)
            filtered = _filter_nodes(
                nodes, selector,
                case_sensitive=False,
            )
            if filtered:
                node = filtered[0]
                return {
                    "ok": True,
                    "exists": True,
                    "element": node.to_dict(),
                    "engine": "hierarchy_search",
                }
        except Exception:
            pass

        if time.monotonic() + interval_s > deadline:
            break
        time.sleep(interval_s)

    return {"ok": True, "exists": False}


async def element_query_async(
    device_serial: str,
    selector: dict,
    **kwargs,
) -> list[dict]:
    """Async wrapper for element_query."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: element_query(device_serial, selector, **kwargs)
    )


async def element_exists_async(
    device_serial: str,
    query: Optional[str] = None,
    **kwargs,
) -> dict:
    """Async wrapper for element_exists."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: element_exists(device_serial, query, **kwargs)
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _selector_to_kwargs(selector: dict) -> dict:
    """Convert our selector dict to hmdriver2 kwargs."""
    kwargs = {}
    mapping = {
        "text": "text",
        "type": "type",
        "id": "id",
        "key": "key",
        "description": "description",
        "clickable": "clickable",
        "enabled": "enabled",
        "focused": "focused",
        "scrollable": "scrollable",
        "selected": "selected",
        "checked": "checked",
        "resource_id": "id",
        "class_name": "type",
        "content_desc": "description",
        "desc": "description",
    }
    for k, v in selector.items():
        if k in mapping:
            kwargs[mapping[k]] = v
        elif k == "text_contains":
            # hmdriver2 doesn't have textContains; use text and hope for partial
            kwargs["text"] = v
    return kwargs


def _filter_nodes(
    nodes: Sequence[HarmonyUINode],
    selector: dict,
    *,
    case_sensitive: bool = False,
) -> list[HarmonyUINode]:
    """Filter nodes by selector criteria."""
    results = list(nodes)

    for key, value in selector.items():
        if not results:
            break

        if key == "text":
            results = [n for n in results if _match(n.text, value, exact=True, cs=case_sensitive)]
        elif key == "text_contains":
            results = [n for n in results if _match(n.text, value, exact=False, cs=case_sensitive)]
        elif key == "description" or key == "desc" or key == "content_desc":
            results = [n for n in results if _match(n.description, value, exact=False, cs=case_sensitive)]
        elif key == "id" or key == "resource_id":
            results = [n for n in results if _match(n.id, value, exact=True, cs=case_sensitive)]
        elif key == "type" or key == "class_name":
            results = [n for n in results if _match(n.type, value, exact=False, cs=case_sensitive)]
        elif key == "clickable":
            results = [n for n in results if n.clickable == bool(value)]
        elif key == "scrollable":
            results = [n for n in results if n.scrollable == bool(value)]
        elif key == "enabled":
            results = [n for n in results if n.enabled == bool(value)]
        elif key == "focused":
            results = [n for n in results if n.focused == bool(value)]
        elif key == "selected":
            results = [n for n in results if n.selected == bool(value)]

    return results


def _match(text: str, query: str, *, exact: bool, cs: bool) -> bool:
    """Check if text matches query."""
    t = str(text or "")
    q = str(query or "")
    if not cs:
        t = t.lower()
        q = q.lower()
    return (t == q) if exact else (q in t)


def _component_to_result(comp, attributes: Optional[list[str]] = None) -> dict:
    """Convert hmdriver2 component to result dict."""
    try:
        d = comp.to_dict()
    except Exception:
        d = {"text": getattr(comp, "text", ""), "type": getattr(comp, "type", "")}

    if attributes:
        d = {k: v for k, v in d.items() if k in attributes}
    return d


__all__ = [
    "element_query",
    "element_exists",
    "element_query_async",
    "element_exists_async",
]
