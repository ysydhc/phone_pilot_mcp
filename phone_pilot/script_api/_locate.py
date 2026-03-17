"""UIA 查找、图像匹配、相对定位（私有）/ UIA lookup, image matching, relative positioning (private).

封装 UIAutomator / Airtest 查找与相对元素定位逻辑。
Wraps UIAutomator / Airtest lookup and relative element positioning logic.
"""
from __future__ import annotations

import difflib
import re
import tempfile
import pathlib
import time
from typing import Optional, Sequence

from phone_pilot.core.ui import ImageElement, TextElement, UIElement

from .context import ScriptContext
from ._helpers import (
    _normalize_text,
    _box_center,
    _box_from_bounds,
    _dump_ui_nodes,
    _take_screenshot,
    _get_screen_size,
    _expand_ocr_langs,
)
from ._ocr import (
    _ocr_find_text_boxes,
    _ocr_find_text_boxes_roi,
)


def _uia_find_text_boxes(
    ctx: ScriptContext,
    *,
    texts: Sequence[str],
    exact: bool = False,
    case_sensitive: bool = False,
    regex: Optional[re.Pattern] = None,
    roi: Optional[tuple[int, int, int, int]] = None,
) -> list[dict]:
    def _norm(s: str) -> str:
        return _normalize_text(s, case_sensitive=case_sensitive)

    queries = [str(t or "") for t in texts if str(t or "").strip()]
    nq = [_norm(q) for q in queries]
    if not queries and regex is None:
        return []
    cache = getattr(ctx, "_ui_cache", None)
    nodes = None
    if isinstance(cache, dict):
        ts = cache.get("ts")
        if ts and (time.time() - ts) < 3.0 and cache.get("nodes"):
            nodes = cache.get("nodes")
    if nodes is None:
        nodes = _dump_ui_nodes(ctx)
        if not nodes:
            return []
        ctx._ui_cache = {"ts": time.time(), "nodes": nodes}
    out: list[dict] = []
    def _candidate_texts(node) -> list[str]:
        vals = [
            node.text,
            node.content_desc,
            node.hint,
            node.resource_id,
        ]
        return [str(v) for v in vals if v]

    def _in_roi(bounds: tuple[int, int, int, int]) -> bool:
        if not roi:
            return True
        x1, y1, x2, y2 = bounds
        rx1, ry1, rx2, ry2 = roi
        cx = int((x1 + x2) / 2)
        cy = int((y1 + y2) / 2)
        return rx1 <= cx <= rx2 and ry1 <= cy <= ry2

    for n in nodes:
        cands = _candidate_texts(n)
        if not cands:
            continue
        bounds = n.bounds_tuple()
        if not bounds:
            continue
        if not _in_roi(bounds):
            continue
        for cand in cands:
            nt = _norm(cand)
            if regex:
                ok = bool(regex.search(cand))
            else:
                ok = any((nt == q) if exact else (q in nt) for q in nq)
            if ok:
                b = _box_from_bounds(tuple(int(v) for v in bounds))
                b["text"] = cand
                b["uia"] = n.to_dict()
                out.append(b)
                break
            else:
                continue
            break
    allow_fuzzy = any(len(q) <= 2 for q in nq)
    if not out and nodes and allow_fuzzy:
        best = None
        best_score = 0.0
        for n in nodes:
            cands = _candidate_texts(n)
            if not cands:
                continue
            bounds = n.bounds_tuple()
            if not bounds:
                continue
            for cand in cands:
                nt = _norm(cand)
                for q in nq:
                    score = difflib.SequenceMatcher(None, nt, q).ratio()
                    if score > best_score:
                        best_score = score
                        best = (bounds, cand, n)
        if best and best_score >= 0.6:
            bounds, cand, node = best
            b = _box_from_bounds(tuple(int(v) for v in bounds))
            b["text"] = cand
            b["uia"] = node.to_dict()
            out = [b]
    return out


def _airtest_find_image_boxes(
    ctx: ScriptContext,
    *,
    path: str,
    threshold: float = 0.8,
    grayscale: bool = True,
) -> list[dict]:
    try:
        from airtest.aircv import imread as air_imread
        from airtest.aircv import find_all_template
    except Exception as e:
        ctx._try_install_and_import("airtest.aircv", "airtest", e)
        from airtest.aircv import imread as air_imread
        from airtest.aircv import find_all_template

    img_bytes = _take_screenshot(ctx)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    try:
        tmp.write(img_bytes)
        tmp.close()
        screen = air_imread(tmp.name)
    finally:
        try:
            pathlib.Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass
    tpl = air_imread(str(path))
    if screen is None or tpl is None:
        return []
    matches = find_all_template(screen, tpl, threshold=float(threshold), rgb=not bool(grayscale)) or []
    th, tw = tpl.shape[:2]
    out: list[dict] = []
    for m in matches:
        result = m.get("result")
        if not result:
            continue
        cx, cy = result
        x1 = int(cx - tw / 2)
        y1 = int(cy - th / 2)
        out.append(
            {
                "x": x1,
                "y": y1,
                "x2": x1 + int(tw),
                "y2": y1 + int(th),
                "w": int(tw),
                "h": int(th),
                "center_x": int(cx),
                "center_y": int(cy),
            }
        )
    return out


def _template_size(path: str) -> Optional[tuple[int, int]]:
    try:
        from airtest.aircv import imread as air_imread
    except Exception:
        return None
    tpl = air_imread(str(path))
    if tpl is None:
        return None
    th, tw = tpl.shape[:2]
    return (int(tw), int(th))


def _select_relative_box(
    *,
    anchor: dict,
    candidates: list[dict],
    direction: str,
    rule: str,
    x_tol: int,
    y_tol: int,
    expand_step: int,
    expand_max: int,
) -> Optional[dict]:
    if not candidates:
        return None
    ax1 = int(anchor.get("x", 0))
    ay1 = int(anchor.get("y", 0))
    aw = int(anchor.get("w", 0))
    ah = int(anchor.get("h", 0))
    ax2 = ax1 + aw
    ay2 = ay1 + ah
    acx, acy = _box_center(anchor)

    def _filter_with_tol(tol: int) -> list[dict]:
        hits = []
        for c in candidates:
            ccx, ccy = _box_center(c)
            if direction in ("right", "left"):
                if abs(ccy - acy) > tol:
                    continue
                dx = ccx - acx
                if direction == "right" and dx <= 0:
                    continue
                if direction == "left" and dx >= 0:
                    continue
                hits.append((abs(dx), c))
            else:
                if abs(ccx - acx) > tol:
                    continue
                dy = ccy - acy
                if direction == "down" and dy <= 0:
                    continue
                if direction == "up" and dy >= 0:
                    continue
                hits.append((abs(dy), c))
        hits.sort(key=lambda x: x[0])
        return [h[1] for h in hits]

    def _filter_expand(tol: int) -> list[dict]:
        hits = []
        for c in candidates:
            ccx, ccy = _box_center(c)
            if direction == "right":
                if ccx <= ax2:
                    continue
                if not (ay1 - tol <= ccy <= ay2 + tol):
                    continue
                dist = ccx - ax2
            elif direction == "left":
                if ccx >= ax1:
                    continue
                if not (ay1 - tol <= ccy <= ay2 + tol):
                    continue
                dist = ax1 - ccx
            elif direction == "down":
                if ccy <= ay2:
                    continue
                if not (ax1 - tol <= ccx <= ax2 + tol):
                    continue
                dist = ccy - ay2
            else:  # up
                if ccy >= ay1:
                    continue
                if not (ax1 - tol <= ccx <= ax2 + tol):
                    continue
                dist = ay1 - ccy
            hits.append((dist, c))
        hits.sort(key=lambda x: x[0])
        return [h[1] for h in hits]

    tol = max(int(x_tol if direction in ("up", "down") else y_tol), 0)
    max_tol = max(int(expand_max), tol)
    step = max(int(expand_step), 1)
    while True:
        if rule == "expand":
            picks = _filter_expand(tol)
        else:
            picks = _filter_with_tol(tol)
        if picks:
            return picks[0]
        if tol >= max_tol:
            break
        tol = min(tol + step, max_tol)
    # fallback: relax band if nothing matched
    best = None
    best_score = None
    for c in candidates:
        ccx, ccy = _box_center(c)
        if direction == "right" and ccx <= ax1:
            continue
        if direction == "left" and ccx >= ax2:
            continue
        if direction == "down" and ccy <= ay1:
            continue
        if direction == "up" and ccy >= ay2:
            continue
        dx = abs(ccx - acx)
        dy = abs(ccy - acy)
        score = dx + dy * 2
        if best_score is None or score < best_score:
            best_score = score
            best = c
    return best
    return None


def _ui_ops(ctx: ScriptContext) -> dict:
    # Lazy imports to avoid circular dependency with actions.py
    from .actions import tap_xy, screenshot

    def _tap_xy(x: int, y: int) -> dict:
        return tap_xy(ctx, int(x), int(y))

    def _swipe(x1: int, y1: int, x2: int, y2: int) -> dict:
        import time as _time
        from ._helpers import _input_swipe
        res = _input_swipe(ctx, int(x1), int(y1), int(x2), int(y2), duration_ms=320)
        _time.sleep(0.15)
        return res

    def _screen_size():
        return _get_screen_size(ctx)

    def _screenshot(name: str) -> dict:
        return screenshot(ctx, str(name or "uielement"))

    def _air_pinch(cx: int, cy: int, zoom_size: float) -> dict:
        try:
            air = ctx.ensure_air()
        except Exception as exc:
            return {"ok": False, "error": "airtest_unavailable", "detail": str(exc)}
        if not hasattr(air, "pinch"):
            return {"ok": False, "error": "airtest_pinch_unavailable"}
        in_or_out = "out" if zoom_size > 0 else "in"
        percent = min(1.0, max(0.1, abs(float(zoom_size))))
        try:
            air.pinch(in_or_out=in_or_out, center=(int(cx), int(cy)), percent=percent)
            return {"ok": True, "center_x": int(cx), "center_y": int(cy), "percent": percent, "in_or_out": in_or_out}
        except Exception as exc:
            return {"ok": False, "error": "airtest_pinch_failed", "detail": str(exc)}

    def _observe_after_input(action: str, detail: str = "") -> None:
        """主动模式：用户输入操作（点击/滑动）后等待 1s 再截图，保证页面 UI 稳定。"""
        if not getattr(ctx, "auto_screenshot", False):
            return
        time.sleep(1)
        try:
            ctx._observe(action, detail, element=None)
        except Exception:
            pass

    return {
        "tap_xy": _tap_xy,
        "swipe": _swipe,
        "get_screen_size": _screen_size,
        "screenshot": _screenshot,
        "observe_after_input": _observe_after_input,
        "relative": lambda element, **kwargs: _ui_relative_find(ctx, element, **kwargs),
        "locate": lambda locator: _ui_locate(ctx, locator),
        "air_pinch": _air_pinch,
        "uia_info": lambda element: _uia_info_from_element(ctx, element),
    }


def _anchor_from_element(elem: UIElement) -> Optional[dict]:
    bounds = elem.bounds()
    if not bounds:
        return None
    x1, y1, x2, y2 = bounds
    return {"x": x1, "y": y1, "x2": x2, "y2": y2}


def _apply_uia_fields(elem: UIElement, uia: dict) -> None:
    if not isinstance(uia, dict):
        return
    if elem.desc is None:
        elem.desc = uia.get("content_desc")
    if elem.resource_id is None:
        elem.resource_id = uia.get("resource_id")
    if elem.clickable is None:
        elem.clickable = uia.get("clickable")
    if elem.enabled is None:
        elem.enabled = uia.get("enabled")
    if elem.focusable is None:
        elem.focusable = uia.get("focusable")
    if elem.selectable is None:
        elem.selectable = uia.get("checkable")
    if elem.selected is None:
        elem.selected = uia.get("checked") if uia.get("checked") is not None else uia.get("selected")
    if isinstance(elem, TextElement):
        if elem.text is None:
            elem.text = uia.get("text")
        if elem.hint is None:
            elem.hint = uia.get("hint")


def _ui_element_from_box(
    ctx: ScriptContext,
    box: dict,
    *,
    kind: str,
    query: Optional[str] = None,
    method: Optional[str] = None,
    text: Optional[str] = None,
    image_src: Optional[str] = None,
    match_src: Optional[str] = None,
    meta: Optional[dict] = None,
    locator: Optional[dict] = None,
) -> UIElement:
    meta_dict = dict(meta or {})
    if method:
        meta_dict.setdefault("method", method)
    if query is not None:
        meta_dict.setdefault("query", query)
    meta_dict.setdefault("out_dir", ctx.out_dir)
    uia = box.get("uia")
    if isinstance(uia, dict):
        meta_dict.setdefault("uia", uia)
    if kind == "text":
        elem: UIElement = TextElement.from_box(box, kind=kind, meta=meta_dict, text=text, hint=box.get("hint"))
    elif kind == "image":
        elem = ImageElement.from_box(box, kind=kind, meta=meta_dict, src=image_src, match_src=match_src)
    else:
        elem = UIElement.from_box(box, kind=kind, meta=meta_dict)
    _apply_uia_fields(elem, uia if isinstance(uia, dict) else {})
    elem._ops = _ui_ops(ctx)
    elem._locator = locator
    return elem


def _ui_relative_find(
    ctx: ScriptContext,
    anchor_elem: UIElement,
    *,
    direction: str,
    text: Optional[str] = None,
    texts: Optional[Sequence[str]] = None,
    image: Optional[str] = None,
    rule: str = "band",
    x_tol: int = 80,
    y_tol: int = 40,
    expand_step: int = 20,
    expand_max: int = 200,
    lang: Optional[str] = None,
    exact: bool = False,
    case_sensitive: bool = False,
    psm: int = 6,
    threshold: float = 0.8,
    grayscale: bool = True,
) -> Optional[UIElement]:
    anchor = _anchor_from_element(anchor_elem)
    if not anchor:
        return None
    screen = _get_screen_size(ctx) or (0, 0)
    sw, sh = screen
    rule = "band"
    expand_step = 100
    if direction in ("right", "left"):
        y_tol = 0
        expand_max = int(sh) if sh else 1000
    else:
        x_tol = 0
        expand_max = int(sw) if sw else 1000
    queries = list(texts or [])
    if text:
        queries.insert(0, text)
    ocr_langs = _expand_ocr_langs(ctx, lang)
    if image:
        candidates = _airtest_find_image_boxes(ctx, path=image, threshold=threshold, grayscale=grayscale)
        method = "airtest"
        kind = "image"
    else:
        candidates = _uia_find_text_boxes(
            ctx,
            texts=queries,
            exact=exact,
            case_sensitive=case_sensitive,
        )
        method = "poco"
        kind = "text"
        if not candidates:
            screen = _get_screen_size(ctx)
            if screen:
                sw, sh = screen
                ax1 = int(anchor.get("x", 0))
                ay1 = int(anchor.get("y", 0))
                ax2 = int(anchor.get("x2", ax1))
                ay2 = int(anchor.get("y2", ay1))
                if direction == "right":
                    roi = (ax2, max(0, ay1 - y_tol), sw, min(sh, ay2 + y_tol))
                elif direction == "left":
                    roi = (0, max(0, ay1 - y_tol), ax1, min(sh, ay2 + y_tol))
                elif direction == "down":
                    roi = (max(0, ax1 - x_tol), ay2, min(sw, ax2 + x_tol), sh)
                else:
                    roi = (max(0, ax1 - x_tol), 0, min(sw, ax2 + x_tol), ay1)
                for ocr_lang in ocr_langs:
                    roi_candidates = _ocr_find_text_boxes_roi(
                        ctx,
                        texts=queries,
                        roi=roi,
                        lang=ocr_lang,
                        exact=exact,
                        case_sensitive=case_sensitive,
                        psm=psm,
                    )
                    if roi_candidates:
                        candidates = roi_candidates
                        method = f"ocr_roi:{ocr_lang}"
                        break
            if not candidates:
                for ocr_lang in ocr_langs:
                    candidates = _ocr_find_text_boxes(
                        ctx,
                        texts=queries,
                        lang=ocr_lang,
                        exact=exact,
                        case_sensitive=case_sensitive,
                        psm=psm,
                    )
                    if candidates:
                        method = f"ocr:{ocr_lang}"
                        break
    if not candidates:
        return None
    best = _select_relative_box(
        anchor=anchor,
        candidates=candidates,
        direction=direction,
        rule=str(rule or "band"),
        x_tol=int(x_tol),
        y_tol=int(y_tol),
        expand_step=int(expand_step),
        expand_max=int(expand_max),
    )
    if not best:
        return None
    locator = {"type": kind, "query": queries[0] if queries else None, "image": image}
    return _ui_element_from_box(
        ctx,
        best,
        kind=kind,
        query=queries[0] if queries else None,
        method=method,
        text=queries[0] if kind == "text" and queries else None,
        image_src=image if kind == "image" else None,
        match_src=image if kind == "image" else None,
        locator=locator,
    )


def _uia_info_from_element(ctx: ScriptContext, elem: UIElement) -> dict:
    bounds = elem.bounds()
    if not bounds:
        return {}
    nodes = _dump_ui_nodes(ctx)
    if not nodes:
        return {}
    cx, cy = elem.center() or (None, None)
    if cx is None or cy is None:
        return {}
    best = None
    best_area = None
    for n in nodes:
        b = n.bounds_tuple()
        if not b:
            continue
        x1, y1, x2, y2 = b
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            area = max(1, (x2 - x1) * (y2 - y1))
            if best_area is None or area < best_area:
                best_area = area
                best = n
    return best.to_dict() if best else {}


def _ui_locate(ctx: ScriptContext, locator: dict) -> Optional[UIElement]:
    # Lazy imports to avoid circular dependency with find.py
    from .find import find_text, find_image

    if not isinstance(locator, dict):
        return None
    loc_type = locator.get("type")
    if loc_type == "text":
        query = locator.get("query") or ""
        res = find_text(
            ctx,
            str(query),
            ocr_lang=locator.get("ocr_lang"),
            ocr_exact=bool(locator.get("ocr_exact", False)),
            ocr_case_sensitive=bool(locator.get("ocr_case_sensitive", False)),
            ocr_psm=int(locator.get("ocr_psm", 6)),
            use_ocr=bool(locator.get("use_ocr", True)),
        )
        return res
    if loc_type == "image":
        path = locator.get("image") or locator.get("query") or ""
        return find_image(
            ctx,
            str(path),
            threshold=float(locator.get("threshold", 0.8)),
            ocr_lang=locator.get("ocr_lang"),
            ocr_exact=bool(locator.get("ocr_exact", False)),
            ocr_case_sensitive=bool(locator.get("ocr_case_sensitive", False)),
            ocr_psm=int(locator.get("ocr_psm", 6)),
        )
    return None
