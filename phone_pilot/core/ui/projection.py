"""UI Semantic Projection & Delta Observer.

Provides semantic pruning, focus-based element projection, page state delta computation,
and log contract event verification for LLM/Subagent context compression.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Sequence

from phone_pilot.core.log import _log

_PLACEHOLDER_TYPES = frozenset({"view", "layout", "container"})
_SEARCH_FIELDS = ("label", "text", "resource_id", "content_desc", "type", "hint")
_PRESET_FOCUSES = frozenset({"all", "*", "actionable", "interactive", "navigation", "nav", "inputs", "text"})
_NAV_TYPES = frozenset({"tab", "toolbar", "actionbar", "navigation", "menubar", "bottomnavigation"})
_NAV_KEYWORDS = (
    "back",
    "return",
    "home",
    "menu",
    "tab",
    "nav",
    "close",
    "more",
    "title",
    "返回",
    "首页",
    "主页",
    "菜单",
    "导航",
    "关闭",
    "更多",
)


def _match_preset_focus(elem: dict[str, Any], preset: str) -> float | None:
    """Return a match score if preset is a recognized semantic focus mode, else None."""
    p = preset.strip().lower()
    if p not in _PRESET_FOCUSES:
        return None
    if p in {"all", "*"}:
        return 1.0

    is_interactive = bool(
        elem.get("clickable")
        or elem.get("scrollable")
        or elem.get("long_clickable")
        or elem.get("checkable")
        or elem.get("editable")
    )
    elem_type = str(elem.get("type") or "").lower()
    label_text = " ".join(
        str(elem.get(k) or "") for k in ("label", "text", "content_desc", "resource_id", "hint")
    ).lower()

    if p in {"actionable", "interactive"}:
        if is_interactive or elem_type in {"button", "input", "edittext", "checkbox", "switch", "tab"}:
            has_label = bool((elem.get("label") or "").strip() or (elem.get("text") or "").strip())
            return 1.0 if has_label else 0.75
        return 0.0

    if p in {"navigation", "nav"}:
        if elem_type in _NAV_TYPES:
            return 1.0
        if any(kw in label_text for kw in _NAV_KEYWORDS):
            return 1.0
        if is_interactive and elem_type in {"button", "text", "image"}:
            return 0.8
        return 0.0

    if p == "inputs":
        if elem_type in {"input", "edittext", "search", "textfield"} or elem.get("editable") or elem.get("hint"):
            return 1.0
        return 0.0

    if p == "text":
        has_text = bool((elem.get("label") or "").strip() or (elem.get("text") or "").strip())
        return 1.0 if has_text else 0.0

    return None


def _is_placeholder_element(elem: dict[str, Any]) -> bool:
    """Return True if the element is a non-interactive, textless placeholder container."""
    elem_type = str(elem.get("type") or "view").lower()
    if elem_type not in _PLACEHOLDER_TYPES:
        return False
    has_label = bool(
        (elem.get("label") or "").strip()
        or (elem.get("text") or "").strip()
        or (elem.get("content_desc") or "").strip()
        or (elem.get("hint") or "").strip()
    )
    if has_label:
        return False
    is_interactive = bool(
        elem.get("clickable")
        or elem.get("scrollable")
        or elem.get("long_clickable")
        or elem.get("checkable")
        or elem.get("focusable")
    )
    return not is_interactive


def _normalize_focus_patterns(focus: str | Sequence[str]) -> tuple[str, list[re.Pattern[str]], list[str]]:
    """Normalize focus input into a query string, compiled regexes, and lowercase tokens."""
    if isinstance(focus, str):
        raw_parts = [p.strip() for p in focus.split("|") if p.strip()] if focus.strip() else []
        focus_query = focus.strip()
    else:
        raw_parts = []
        for item in focus:
            if isinstance(item, str) and item.strip():
                raw_parts.extend(p.strip() for p in item.split("|") if p.strip())
        focus_query = "|".join(raw_parts)

    regexes: list[re.Pattern[str]] = []
    tokens: list[str] = []
    for part in raw_parts:
        try:
            regexes.append(re.compile(part, re.IGNORECASE))
        except re.error:
            regexes.append(re.compile(re.escape(part), re.IGNORECASE))
        for tok in re.split(r"[\s_\-/.,:;]+", part.lower()):
            if tok and len(tok) >= 2:
                tokens.append(tok)
            elif tok:
                tokens.append(tok)
    return focus_query, regexes, tokens


def _score_element(
    elem: dict[str, Any],
    regexes: Sequence[re.Pattern[str]],
    tokens: Sequence[str],
) -> float:
    """Score an element against focus regexes (1.0) and token/substring similarity (0.6)."""
    if not regexes and not tokens:
        return 1.0

    field_values: list[str] = []
    for field in _SEARCH_FIELDS:
        val = elem.get(field)
        if val is not None:
            s = str(val).strip()
            if s:
                field_values.append(s)

    if not field_values:
        return 0.0

    for val in field_values:
        for rgx in regexes:
            if rgx.search(val):
                return 1.0

    combined_lower = " ".join(field_values).lower()
    for tok in tokens:
        if tok and (tok in combined_lower or any(tok in fv.lower() or fv.lower() in tok for fv in field_values if len(fv) >= 2)):
            return 0.6

    return 0.0


def _is_spatially_contained(inner: dict[str, Any], outer: dict[str, Any], margin: int = 8) -> bool:
    """Return True if inner's bounds lie inside outer's bounds (within margin)."""
    b_in = inner.get("bounds")
    b_out = outer.get("bounds")
    if (
        isinstance(b_in, (list, tuple))
        and len(b_in) == 4
        and isinstance(b_out, (list, tuple))
        and len(b_out) == 4
    ):
        ix1, iy1, ix2, iy2 = b_in
        ox1, oy1, ox2, oy2 = b_out
        return (
            ix1 >= ox1 - margin
            and iy1 >= oy1 - margin
            and ix2 <= ox2 + margin
            and iy2 <= oy2 + margin
        )
    return False


def _are_spatially_related(elem_a: dict[str, Any], elem_b: dict[str, Any], margin: int = 48) -> bool:
    """Check if two elements spatially contain or are immediately adjacent to each other."""
    bounds_a = elem_a.get("bounds")
    bounds_b = elem_b.get("bounds")
    if (
        isinstance(bounds_a, (list, tuple))
        and len(bounds_a) == 4
        and isinstance(bounds_b, (list, tuple))
        and len(bounds_b) == 4
    ):
        ax1, ay1, ax2, ay2 = bounds_a
        bx1, by1, bx2, by2 = bounds_b
        # Overlap or within margin
        horiz_ok = (ax1 - margin) <= bx2 and (bx1 - margin) <= ax2
        vert_ok = (ay1 - margin) <= by2 and (by1 - margin) <= ay2
        return bool(horiz_ok and vert_ok)
    return False


def _compact_element(elem: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    """Strip empty/default fields from an element while strictly preserving `index`."""
    out: dict[str, Any] = {}
    if "index" in elem:
        out["index"] = elem["index"]
    for key, val in elem.items():
        if key == "index":
            continue
        if val is None or val == "" or val is False or val == [] or val == {}:
            continue
        out[key] = val
    if score is not None and score > 0:
        out["match_score"] = round(score, 2)
    return out


def project_page_state(
    page_state: dict[str, Any],
    focus: str | Sequence[str] = "",
    *,
    max_elements: int = 25,
    include_neighbors: int = 1,
    compact: bool = True,
) -> dict[str, Any]:
    """Project and prune a raw page_state dict for high-density LLM/Subagent consumption.

    Preserves original element `index` values so `phone_tap_element(index)` remains valid.
    """
    raw_elements: list[dict[str, Any]] = list(page_state.get("elements") or [])
    total_before = len(raw_elements)

    # 1. Prune pure placeholder/background views
    pruned_elements = [e for e in raw_elements if not _is_placeholder_element(e)]

    focus_query, regexes, tokens = _normalize_focus_patterns(focus)
    preset_mode = focus_query.lower() if focus_query.lower() in _PRESET_FOCUSES else ""
    is_focused = bool(focus_query) and preset_mode not in {"all", "*"}

    scores: dict[int, float] = {}
    if is_focused:
        matched_positions: list[int] = []
        for pos, elem in enumerate(pruned_elements):
            if preset_mode:
                sc = _match_preset_focus(elem, preset_mode) or 0.0
            else:
                sc = _score_element(elem, regexes, tokens)
            if sc > 0.0:
                scores[pos] = sc
                matched_positions.append(pos)

        # For actionable/navigation presets, promote labeled child elements inside clickable row containers
        if preset_mode in {"actionable", "interactive", "navigation", "nav"} and matched_positions:
            clickable_containers = [
                pruned_elements[m_pos]
                for m_pos in matched_positions
                if pruned_elements[m_pos].get("clickable")
            ]
            if clickable_containers:
                for n_pos, n_elem in enumerate(pruned_elements):
                    if n_pos not in scores:
                        has_lbl = bool((n_elem.get("label") or "").strip() or (n_elem.get("text") or "").strip())
                        if has_lbl and any(_is_spatially_contained(n_elem, c) for c in clickable_containers):
                            scores[n_pos] = 0.95

        # Retain immediate interactive neighbors (±include_neighbors or spatially adjacent)
        if not preset_mode and include_neighbors > 0 and matched_positions:
            for m_pos in matched_positions:
                m_elem = pruned_elements[m_pos]
                start_idx = max(0, m_pos - include_neighbors)
                end_idx = min(len(pruned_elements) - 1, m_pos + include_neighbors)
                for n_pos in range(start_idx, end_idx + 1):
                    if n_pos not in scores:
                        n_elem = pruned_elements[n_pos]
                        if n_elem.get("clickable") or n_elem.get("scrollable") or n_elem.get("label"):
                            scores[n_pos] = 0.35

                # Also check spatial containment/adjacency for interactive nodes
                for n_pos, n_elem in enumerate(pruned_elements):
                    if n_pos not in scores and (n_elem.get("clickable") or n_elem.get("scrollable")):
                        if _are_spatially_related(m_elem, n_elem):
                            scores[n_pos] = 0.35

        # Rank by (score desc, original position asc), cap at max_elements, then restore visual order
        ranked_positions = sorted(scores.keys(), key=lambda p: (-scores[p], p))[: max(0, max_elements)]
        selected_positions = sorted(ranked_positions)
        retained_pairs = [(pruned_elements[p], scores[p]) for p in selected_positions]
    else:
        selected_slice = pruned_elements[: max(0, max_elements)] if max_elements > 0 else []
        retained_pairs = [(elem, None) for elem in selected_slice]

    if compact:
        projected_elements = [_compact_element(elem, sc) for elem, sc in retained_pairs]
    else:
        projected_elements = []
        for elem, sc in retained_pairs:
            copied = dict(elem)
            if sc is not None:
                copied["match_score"] = round(sc, 2)
            projected_elements.append(copied)

    # Filter all_texts when focused
    raw_texts: list[str] = [str(t) for t in (page_state.get("all_texts") or []) if t is not None]
    if is_focused:
        retained_labels = {
            str(elem.get("label") or "").strip()
            for elem, _ in retained_pairs
            if elem.get("label")
        }
        filtered_texts: list[str] = []
        for txt in raw_texts:
            if txt in retained_labels:
                filtered_texts.append(txt)
                continue
            if any(rgx.search(txt) for rgx in regexes):
                filtered_texts.append(txt)
                continue
            txt_lower = txt.lower()
            if any(tok in txt_lower for tok in tokens if tok):
                filtered_texts.append(txt)
        all_texts_out = filtered_texts
    else:
        all_texts_out = raw_texts[:max_elements] if compact else raw_texts

    scrollable_areas = list(page_state.get("scrollable_areas") or [])
    if compact:
        scrollable_areas = [
            {k: v for k, v in area.items() if v not in (None, "", False, [], {})}
            for area in scrollable_areas
        ]

    result: dict[str, Any] = {
        "ok": bool(page_state.get("ok", True)),
        "activity": page_state.get("activity") or {},
        "screen": page_state.get("screen") or {},
        "elements": projected_elements,
        "element_count": len(projected_elements),
        "scrollable_areas": scrollable_areas,
        "all_texts": all_texts_out,
        "focused": is_focused,
        "focus_query": focus_query,
        "total_elements_before": total_before,
        "compression_ratio": 0.0,
    }

    if not compact:
        if "screenshot_base64" in page_state:
            result["screenshot_base64"] = page_state["screenshot_base64"]
        if "screenshot_annotated_base64" in page_state:
            result["screenshot_annotated_base64"] = page_state["screenshot_annotated_base64"]

    try:
        orig_json_len = len(json.dumps(page_state, ensure_ascii=False))
        proj_json_len = len(json.dumps(result, ensure_ascii=False))
        if orig_json_len > 0:
            result["compression_ratio"] = round(max(0.0, 1.0 - (proj_json_len / orig_json_len)), 4)
    except Exception as exc:
        _log(f"[projection] failed to compute compression_ratio: {exc}")

    return result


def _element_signature(elem: dict[str, Any]) -> tuple[Any, ...]:
    """Build a stable semantic identity tuple for an element across page states."""
    return (
        str(elem.get("type") or ""),
        str(elem.get("label") or elem.get("text") or ""),
        str(elem.get("resource_id") or ""),
        bool(elem.get("clickable", False)),
    )


def compute_state_delta(
    before_state: dict[str, Any],
    after_state: dict[str, Any],
) -> dict[str, Any]:
    """Compute structural and text delta between two page_state snapshots."""
    before_act = before_state.get("activity") or {}
    after_act = after_state.get("activity") or {}
    activity_changed = before_act != after_act

    before_texts = [str(t) for t in (before_state.get("all_texts") or []) if t is not None]
    after_texts = [str(t) for t in (after_state.get("all_texts") or []) if t is not None]
    before_text_set = set(before_texts)
    after_text_set = set(after_texts)

    added_texts = [t for t in after_texts if t not in before_text_set]
    removed_texts = [t for t in before_texts if t not in after_text_set]

    before_elems: list[dict[str, Any]] = list(before_state.get("elements") or [])
    after_elems: list[dict[str, Any]] = list(after_state.get("elements") or [])

    before_sigs = {_element_signature(e) for e in before_elems}
    after_sigs = {_element_signature(e) for e in after_elems}

    added_elements = [e for e in after_elems if _element_signature(e) not in before_sigs]
    removed_elements = [e for e in before_elems if _element_signature(e) not in after_sigs]

    changed = bool(activity_changed or added_texts or removed_texts or added_elements or removed_elements)

    return {
        "changed": changed,
        "activity_changed": activity_changed,
        "added_texts": added_texts,
        "removed_texts": removed_texts,
        "added_elements": added_elements,
        "removed_elements": removed_elements,
    }


def _extract_lines(source: str | Sequence[str]) -> list[str]:
    """Normalize string or sequence of strings into a list of log lines."""
    if isinstance(source, str):
        return source.splitlines()
    lines: list[str] = []
    for item in source:
        if isinstance(item, str):
            if "\n" in item:
                lines.extend(item.splitlines())
            else:
                lines.append(item)
    return lines


def _extract_json_objects(line: str) -> list[dict[str, Any]]:
    """Extract embedded JSON dict objects `{...}` from a log line."""
    objs: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    idx = 0
    length = len(line)
    while idx < length:
        brace_pos = line.find("{", idx)
        if brace_pos == -1:
            break
        try:
            obj, end_pos = decoder.raw_decode(line, brace_pos)
            if isinstance(obj, dict):
                objs.append(obj)
            idx = end_pos
        except json.JSONDecodeError:
            idx = brace_pos + 1
    return objs


def _evaluate_contract_events_once(
    lines: Sequence[str],
    pattern_list: Sequence[str | dict[str, Any]],
    default_required_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Evaluate pattern matches, ordering, and required JSON fields on a snapshot of lines."""
    matched_events = 0
    out_of_order = 0
    missing_fields: list[str] = []
    unmatched_patterns: list[str] = []
    matched_details: list[dict[str, Any]] = []

    last_matched_line_idx = -1

    for pat_item in pattern_list:
        if isinstance(pat_item, dict):
            pat_str = str(pat_item.get("pattern") or "")
            item_req_fields = pat_item.get("required_fields")
            req_fields = list(item_req_fields) if item_req_fields is not None else list(default_required_fields or [])
        else:
            pat_str = str(pat_item)
            req_fields = list(default_required_fields or [])

        try:
            rgx = re.compile(pat_str)
        except re.error:
            rgx = re.compile(re.escape(pat_str))

        # Find all line indices matching this pattern
        candidate_indices = [
            idx for idx, line in enumerate(lines) if rgx.search(line) or pat_str in line
        ]

        if not candidate_indices:
            unmatched_patterns.append(pat_str)
            continue

        matched_events += 1
        # Prefer a match at or after last_matched_line_idx to respect monotonic order when possible
        in_order_indices = [idx for idx in candidate_indices if idx >= last_matched_line_idx]
        if in_order_indices:
            chosen_idx = in_order_indices[0]
            is_in_order = True
        else:
            chosen_idx = candidate_indices[0]
            is_in_order = False
            out_of_order += 1

        last_matched_line_idx = max(last_matched_line_idx, chosen_idx)
        matched_line = lines[chosen_idx]

        line_missing: list[str] = []
        if req_fields:
            json_objs = _extract_json_objects(matched_line)
            merged_payload: dict[str, Any] = {}
            for obj in json_objs:
                merged_payload.update(obj)
            for field in req_fields:
                if field not in merged_payload or merged_payload[field] is None:
                    line_missing.append(field)
                    if field not in missing_fields:
                        missing_fields.append(field)

        matched_details.append(
            {
                "pattern": pat_str,
                "line_index": chosen_idx,
                "line": matched_line,
                "in_order": is_in_order,
                "missing_fields": line_missing,
            }
        )

    ok = (
        matched_events == len(pattern_list)
        and out_of_order == 0
        and len(missing_fields) == 0
    )
    return {
        "ok": ok,
        "matched_events": matched_events,
        "expected_events": len(pattern_list),
        "out_of_order": out_of_order,
        "missing_fields": missing_fields,
        "unmatched_patterns": unmatched_patterns,
        "matched_details": matched_details,
    }


def assert_contract_events(
    log_source: str | Sequence[str] | Callable[[], str | Sequence[str]],
    pattern_list: Sequence[str | dict[str, Any]],
    *,
    timeout: float = 0.0,
    poll_interval: float = 0.1,
    required_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Verify ordered contract events and embedded JSON fields from a log source."""
    deadline = time.monotonic() + max(0.0, timeout)
    interval = max(0.01, poll_interval)

    while True:
        raw_logs = log_source() if callable(log_source) else log_source
        lines = _extract_lines(raw_logs)
        result = _evaluate_contract_events_once(
            lines,
            pattern_list,
            default_required_fields=required_fields,
        )
        if result["ok"] or not callable(log_source) or time.monotonic() >= deadline:
            return result
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


class SemanticProjector:
    """Object-oriented wrapper for UI semantic projection, delta observation, and log contract assertions."""

    def __init__(
        self,
        *,
        default_max_elements: int = 25,
        default_include_neighbors: int = 1,
        default_compact: bool = True,
    ) -> None:
        self.default_max_elements = default_max_elements
        self.default_include_neighbors = default_include_neighbors
        self.default_compact = default_compact

    def project(
        self,
        page_state: dict[str, Any],
        focus: str | Sequence[str] = "",
        *,
        max_elements: int | None = None,
        include_neighbors: int | None = None,
        compact: bool | None = None,
    ) -> dict[str, Any]:
        """Project a page_state dict with optional focus and compaction."""
        return project_page_state(
            page_state,
            focus=focus,
            max_elements=self.default_max_elements if max_elements is None else max_elements,
            include_neighbors=(
                self.default_include_neighbors if include_neighbors is None else include_neighbors
            ),
            compact=self.default_compact if compact is None else compact,
        )

    def diff(
        self,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Compute structural delta between before_state and after_state."""
        return compute_state_delta(before_state, after_state)

    def assert_events(
        self,
        log_source: str | Sequence[str] | Callable[[], str | Sequence[str]],
        pattern_list: Sequence[str | dict[str, Any]],
        *,
        timeout: float = 0.0,
        poll_interval: float = 0.1,
        required_fields: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Assert ordered contract events and required JSON payload fields."""
        return assert_contract_events(
            log_source,
            pattern_list,
            timeout=timeout,
            poll_interval=poll_interval,
            required_fields=required_fields,
        )


__all__ = [
    "project_page_state",
    "compute_state_delta",
    "assert_contract_events",
    "SemanticProjector",
]
