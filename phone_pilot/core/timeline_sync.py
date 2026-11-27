"""Generic monotonic & wall-clock timeline synchronization for PhonePilot scenes.

Eliminates linear timestamp interpolation (`(i+1)*(span/(N+1))`) by fusing:
1. Monotonic offsets (`mono_ts - suite_mono_t0`) from MockServer `RequestEntry`;
2. Device-local `MM-DD HH:MM:SS.mmm` (`adb logcat -v threadtime`) relative to `log_since`;
3. Host/Simulator `YYYY-MM-DD HH:MM:SS.mmm` (`simctl log stream`) or ISO-8601 step timestamps relative to `suite_wall_t0`;
into a causally-ordered, clamped `[0.0, span_s]` timeline for Video <-> Log <-> Network sync.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Iterable, Optional

_ISO_PREFIX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)(?:Z|[+-]\d{2}:?\d{2})?"
)
_THREADTIME_RE = re.compile(r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?\b")
_HMS_BRACKET_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?\]")


def parse_mmdd_hms_seconds(text: str) -> Optional[float]:
    """Parse `MM-DD HH:MM:SS.mmm` into day-of-month * 86400 + seconds-of-day (device-timezone agnostic)."""
    if not text:
        return None
    m = _THREADTIME_RE.match(text.strip())
    if not m:
        return None
    _month, day, hh, mm, ss, frac = m.groups()
    sec = int(day) * 86400.0 + int(hh) * 3600.0 + int(mm) * 60.0 + int(ss)
    if frac:
        sec += float(f"0.{frac}")
    return sec


def parse_log_timestamp_epoch(
    line: str,
    *,
    ref_epoch: Optional[float] = None,
) -> Optional[float]:
    """Extract epoch seconds from ISO-8601, `YYYY-MM-DD HH:MM:SS.mmm`, or `[HH:MM:SS]` log lines."""
    s = (line or "").strip()
    if not s:
        return None

    # 1. Full ISO-8601 or `YYYY-MM-DD HH:MM:SS.mmm` (e.g. iOS simctl log stream / steps.json)
    m_iso = _ISO_PREFIX_RE.match(s)
    if m_iso:
        try:
            # Preserve explicit timezone if present on pure ISO string
            raw_cand = s.split()[0] if "T" in s[:12] else f"{m_iso.group(1)}T{m_iso.group(2)}"
            parsed = dt.datetime.fromisoformat(raw_cand)
            return parsed.timestamp()
        except Exception:
            try:
                parsed = dt.datetime.fromisoformat(f"{m_iso.group(1)}T{m_iso.group(2)}")
                return parsed.timestamp()
            except Exception:
                pass

    # 2. `[HH:MM:SS]` console milestone relative to ref_epoch's local calendar date
    if ref_epoch is not None:
        m_hms = _HMS_BRACKET_RE.match(s)
        if m_hms:
            hh, mm, ss, frac = m_hms.groups()
            base_dt = dt.datetime.fromtimestamp(ref_epoch)
            cand_dt = base_dt.replace(
                hour=int(hh),
                minute=int(mm),
                second=int(ss),
                microsecond=int((frac or "0").ljust(6, "0")[:6]),
            )
            ep = cand_dt.timestamp()
            if ep < ref_epoch - 60.0:
                ep += 86400.0
            return ep

    return None


def compute_threadtime_offset(line: str, log_since: str) -> Optional[float]:
    """Compute relative seconds `t >= 0` between an `adb logcat -v threadtime` line and `log_since`."""
    line_s = parse_mmdd_hms_seconds(line)
    base_s = parse_mmdd_hms_seconds(log_since)
    if line_s is None or base_s is None:
        return None
    delta = line_s - base_s
    # Handle month rollover (e.g. 30th 23:59 -> 01st 00:00)
    if delta < -86400.0 * 10:
        # Approximate rollover by matching day wraparound to midnight
        line_tod = line_s % 86400.0
        base_tod = base_s % 86400.0
        delta = (86400.0 - base_tod) + line_tod
    elif delta < -60.0:
        delta += 86400.0
    return max(0.0, delta)


def build_synced_timeline(
    *,
    span_s: float,
    suite_wall_t0: Optional[float] = None,
    suite_mono_t0: Optional[float] = None,
    log_since: str = "",
    header_line: str = "",
    footer_line: str = "",
    step_items: Optional[Iterable[dict[str, Any]]] = None,
    mock_items: Optional[Iterable[dict[str, Any]]] = None,
    timed_log_items: Optional[Iterable[tuple[Optional[float], str] | str]] = None,
) -> list[dict[str, Any]]:
    """Fuse steps, MockServer HTTP requests, and device/SDK logs onto a single `[0.0, span_s]` timeline."""
    eff_span = max(0.2, float(span_s or 1.0))
    # `(t_sec, seq_priority, insertion_order, line, source)`
    staged: list[tuple[float, int, int, str, str]] = []
    seq = 0

    def _push(t_val: float, prio: int, line_txt: str, src: str) -> None:
        nonlocal seq
        txt = (line_txt or "").strip()
        if not txt:
            return
        clamped = round(max(0.0, min(eff_span, float(t_val))), 2)
        staged.append((clamped, prio, seq, txt, src))
        seq += 1

    if header_line:
        _push(0.0, 0, header_line, "scene")

    # 1. Step snapshots (`steps.json` entries with ISO `timestamp`)
    step_list = list(step_items or [])
    for s_idx, st in enumerate(step_list):
        if not isinstance(st, dict):
            continue
        act_nm = str(st.get("action") or "").strip()
        det_nm = str(st.get("detail") or "").strip()
        if not act_nm or act_nm == "find_text":
            continue
        t_rel: Optional[float] = None
        if st.get("rel_t") is not None:
            try:
                t_rel = float(st["rel_t"])
            except Exception:
                t_rel = None
        if t_rel is None and suite_wall_t0 is not None and st.get("timestamp"):
            ep = parse_log_timestamp_epoch(str(st.get("timestamp")), ref_epoch=suite_wall_t0)
            if ep is not None:
                t_rel = ep - suite_wall_t0
        if t_rel is None:
            t_rel = (s_idx + 1) * (eff_span / max(len(step_list) + 1, 2))
        msg = f"[STEP] {act_nm}" + (f" — {det_nm}" if det_nm else "")
        _push(t_rel, 2, msg, "step")

    # 2. MockServer HTTP requests (prefer monotonic `mono_ts - suite_mono_t0`, then `ts - suite_wall_t0`)
    mock_list = list(mock_items or [])
    for m_idx, rq in enumerate(mock_list):
        if isinstance(rq, dict):
            path = str(rq.get("path") or "")
            method = str(rq.get("method") or "GET")
            mono_val = rq.get("mono_ts")
            wall_val = rq.get("ts") if rq.get("ts") is not None else rq.get("timestamp")
            custom_line = rq.get("line")
        else:
            path = str(getattr(rq, "path", "") or "")
            method = str(getattr(rq, "method", "GET") or "GET")
            mono_val = getattr(rq, "mono_ts", None)
            wall_val = getattr(rq, "ts", None)
            custom_line = None
        if not path and not custom_line:
            continue
        if "code=" in path and len(path) > 80:
            path = path.split("code=", 1)[0] + "code=<encrypted…>"
        t_rel = None
        if suite_mono_t0 is not None and mono_val is not None:
            try:
                mv = float(mono_val)
                if mv > 0 and mv >= suite_mono_t0 - 0.5:
                    t_rel = max(0.0, mv - suite_mono_t0)
            except Exception:
                t_rel = None
        if t_rel is None and suite_wall_t0 is not None and wall_val is not None:
            try:
                wv = float(wall_val)
                if wv > 0 and wv >= suite_wall_t0 - 1.0:
                    t_rel = max(0.0, wv - suite_wall_t0)
            except Exception:
                t_rel = None
        if t_rel is None:
            t_rel = (m_idx + 1) * (eff_span / max(len(mock_list) + 1, 2))
        ln_str = str(custom_line) if custom_line else f"[MOCK] {method} {path}"
        # Give `/bid` requests priority 1 so at identical millisecond `0.12s` request precedes response log
        prio = 1 if "bid" in path.lower() else 2
        _push(t_rel, prio, ln_str, "mock")

    # 3. Device / SDK logs (explicit `(rel_t, display_line)` OR raw line with `threadtime` / ISO / `[HH:MM:SS]`)
    raw_logs = list(timed_log_items or [])
    last_known_t = 0.05
    for l_idx, item in enumerate(raw_logs):
        if isinstance(item, tuple) and len(item) == 2:
            explicit_t, disp_line = item
        else:
            explicit_t, disp_line = None, str(item)
        t_rel = explicit_t
        if t_rel is None and log_since:
            t_rel = compute_threadtime_offset(disp_line, log_since)
        if t_rel is None and suite_wall_t0 is not None:
            ep = parse_log_timestamp_epoch(disp_line, ref_epoch=suite_wall_t0)
            if ep is not None:
                t_rel = max(0.0, ep - suite_wall_t0)
        if t_rel is None:
            # Preserve causal position relative to last resolved log line instead of blind global uniform spacing
            remaining = max(1, len(raw_logs) - l_idx)
            step_inc = max(0.02, min(0.25, (eff_span * 0.96 - last_known_t) / (remaining + 1)))
            t_rel = last_known_t + step_inc
        last_known_t = max(0.0, min(eff_span * 0.98, float(t_rel)))
        _push(t_rel, 3, disp_line, "log")

    if footer_line:
        _push(eff_span, 9, footer_line, "verdict")

    staged.sort(key=lambda x: (x[0], x[1], x[2]))
    seen_pairs: set[tuple[float, str]] = set()
    out: list[dict[str, Any]] = []
    for t_val, _prio, _seq, txt, src in staged:
        dedup_k = (t_val, txt)
        if dedup_k in seen_pairs:
            continue
        seen_pairs.add(dedup_k)
        out.append({"t": t_val, "line": txt, "source": src})
    return out
