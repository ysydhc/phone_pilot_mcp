"""OCR 相关查找逻辑（私有）/ OCR-based text finding logic (private).

通过 Tesseract OCR 在截图中查找文本，支持多语言、ROI 裁剪和正则匹配。
Uses Tesseract OCR to find text in screenshots with multi-language, ROI crop, and regex support.
"""
from __future__ import annotations

import difflib
import pathlib
import re
import time
from typing import Optional, Sequence

from phone_pilot.extensions.ocr.config import normalize_ocr_lang
from phone_pilot.extensions.ocr.tesseract import ocr_image

from .context import ScriptContext
from ._helpers import _normalize_text, _box_center, _take_screenshot


def _ocr_find_text_boxes(
    ctx: ScriptContext,
    *,
    texts: Sequence[str],
    lang: Optional[str] = None,
    exact: bool = False,
    case_sensitive: bool = False,
    psm: int = 6,
) -> list[dict]:
    def _norm(s: str) -> str:
        return _normalize_text(s, case_sensitive=case_sensitive)

    def _scale_png_bytes(data: bytes, scale: float = 2.0) -> Optional[bytes]:
        try:
            import cv2
            import numpy as np
        except Exception:
            return None
        if not data:
            return None
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        h, w = img.shape[:2]
        nh, nw = int(h * scale), int(w * scale)
        if nh <= 0 or nw <= 0:
            return None
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_CUBIC)
        ok, enc = cv2.imencode(".png", resized)
        if not ok:
            return None
        return enc.tobytes()

    def _preprocess_png_bytes(data: bytes, mode: str) -> Optional[bytes]:
        try:
            import cv2
            import numpy as np
        except Exception:
            return None
        if not data:
            return None
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if mode == "gray":
            out = gray
        elif mode == "thresh":
            _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        elif mode == "thresh_inv":
            _, out = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        else:
            return None
        ok, enc = cv2.imencode(".png", out)
        if not ok:
            return None
        return enc.tobytes()

    queries = [str(t or "") for t in texts if str(t or "").strip()]
    if not queries:
        return []
    nq = [_norm(q) for q in queries]
    boxes = None
    img_bytes = None
    cache = getattr(ctx, "_ocr_cache", None)
    if isinstance(cache, dict):
        ts = cache.get("ts")
        if ts and (time.time() - ts) < 1.5 and cache.get("boxes"):
            boxes = cache.get("boxes")
            img_bytes = cache.get("img_bytes")
    if boxes is None:
        for _ in range(3):
            try:
                img_bytes = _take_screenshot(ctx)
                boxes = ocr_image(img_bytes, lang=normalize_ocr_lang(lang), psm=int(psm))
                ctx._ocr_cache = {"ts": time.time(), "boxes": boxes, "img_bytes": img_bytes}
                break
            except Exception as e:
                _ = e  # noqa: F841
                boxes = None
                time.sleep(0.15)
    if boxes is None:
        return []
    def _match_boxes(boxes_in: list[dict]) -> list[dict]:
        out: list[dict] = []
        for b in boxes_in:
            if not isinstance(b, dict):
                continue
            txt = str(b.get("text", ""))
            t = _norm(txt)
            for q in nq:
                ok = (t == q) if exact else (q in t)
                if ok:
                    if "center_x" not in b or "center_y" not in b:
                        cx, cy = _box_center(b)
                        b = dict(b)
                        b["center_x"] = cx
                        b["center_y"] = cy
                    if "x2" not in b or "y2" not in b:
                        x1 = int(b.get("x", 0))
                        y1 = int(b.get("y", 0))
                        w = int(b.get("w", 0))
                        h = int(b.get("h", 0))
                        b["x2"] = x1 + w
                        b["y2"] = y1 + h
                    out.append(b)
                    break
        return out

    def _process_boxes(boxes_in: list[dict]) -> list[dict]:
        out_local = _match_boxes(boxes_in)
        if out_local:
            return out_local
        # try combine adjacent word boxes (e.g. "広" + "告")
        combos: list[dict] = []
        items = [b for b in boxes_in if isinstance(b, dict)]
        for i in range(len(items)):
            b1 = items[i]
            t1 = str(b1.get("text", "")).strip()
            if not t1:
                continue
            cx1, cy1 = _box_center(b1)
            for j in range(len(items)):
                if i == j:
                    continue
                b2 = items[j]
                t2 = str(b2.get("text", "")).strip()
                if not t2:
                    continue
                cx2, cy2 = _box_center(b2)
                if abs(cy1 - cy2) > 30:
                    continue
                gap = int(b2.get("x", 0)) - int(b1.get("x2", b1.get("x", 0) + b1.get("w", 0)))
                if gap < -5 or gap > 30:
                    continue
                combined = dict(b1)
                combined["text"] = t1 + t2
                combined["x"] = min(int(b1.get("x", 0)), int(b2.get("x", 0)))
                combined["y"] = min(int(b1.get("y", 0)), int(b2.get("y", 0)))
                combined["x2"] = max(int(b1.get("x2", 0)), int(b2.get("x2", 0)))
                combined["y2"] = max(int(b1.get("y2", 0)), int(b2.get("y2", 0)))
                combined["w"] = combined["x2"] - combined["x"]
                combined["h"] = combined["y2"] - combined["y"]
                ccx, ccy = _box_center(combined)
                combined["center_x"] = ccx
                combined["center_y"] = ccy
                combos.append(combined)
        if combos:
            out_local = _match_boxes(combos)
            if out_local:
                return out_local
        non_ascii_query = any(any(ord(ch) > 127 for ch in q) for q in nq)
        best = None
        best_score = 0.0
        for b in items:
            txt = str(b.get("text", ""))
            t = _norm(txt)
            if non_ascii_query and not any(ord(ch) > 127 for ch in t):
                continue
            for q in nq:
                score = difflib.SequenceMatcher(None, t, q).ratio()
                if score > best_score:
                    best_score = score
                    best = b
        threshold = 0.6 if non_ascii_query else (0.45 if any(len(q) <= 2 for q in nq) else 0.6)
        if best is not None and best_score >= threshold:
            if "center_x" not in best or "center_y" not in best:
                cx, cy = _box_center(best)
                best = dict(best)
                best["center_x"] = cx
                best["center_y"] = cy
            if "x2" not in best or "y2" not in best:
                x1 = int(best.get("x", 0))
                y1 = int(best.get("y", 0))
                w = int(best.get("w", 0))
                h = int(best.get("h", 0))
                best["x2"] = x1 + w
                best["y2"] = y1 + h
            return [best]
        return []

    out = _process_boxes(boxes)
    if not out and img_bytes:
        scaled = _scale_png_bytes(img_bytes, scale=2.0)
        if scaled:
            try:
                boxes2 = ocr_image(scaled, lang=normalize_ocr_lang(lang), psm=int(psm))
                out = _process_boxes(boxes2)
                boxes = boxes2
            except Exception:
                pass
    if not out and img_bytes and any(len(q) <= 2 for q in nq):
        for alt_psm in (7, 11, 6):
            if int(alt_psm) == int(psm):
                continue
            try:
                boxes3 = ocr_image(img_bytes, lang=normalize_ocr_lang(lang), psm=int(alt_psm))
                out = _process_boxes(boxes3)
                if out:
                    break
            except Exception:
                pass
            for scale in (3.0, 4.0):
                scaled3 = _scale_png_bytes(img_bytes, scale=scale)
                if scaled3:
                    try:
                        boxes4 = ocr_image(scaled3, lang=normalize_ocr_lang(lang), psm=int(alt_psm))
                        out = _process_boxes(boxes4)
                        if out:
                            break
                    except Exception:
                        pass
                if out:
                    break
            for mode in ("gray", "thresh", "thresh_inv"):
                pre = _preprocess_png_bytes(img_bytes, mode)
                if not pre:
                    continue
                try:
                    boxes5 = ocr_image(pre, lang=normalize_ocr_lang(lang), psm=int(alt_psm))
                    out = _process_boxes(boxes5)
                    if out:
                        break
                except Exception:
                    pass
            if out:
                break
    return out


def _ocr_texts_from_image(path: str, *, lang: Optional[str] = None, psm: int = 6) -> list[str]:
    try:
        data = pathlib.Path(path).read_bytes()
    except Exception:
        return []
    boxes = ocr_image(data, lang=normalize_ocr_lang(lang), psm=int(psm))
    texts: list[str] = []
    for b in boxes:
        if not isinstance(b, dict):
            continue
        t = str(b.get("text", "")).strip()
        if t:
            texts.append(t)
    return texts


def _ocr_find_text_boxes_roi(
    ctx: ScriptContext,
    *,
    texts: Sequence[str],
    roi: tuple[int, int, int, int],
    lang: Optional[str] = None,
    exact: bool = False,
    case_sensitive: bool = False,
    psm: int = 6,
) -> list[dict]:
    try:
        import cv2
        import numpy as np
    except Exception:
        return []

    x1, y1, x2, y2 = [int(v) for v in roi]
    if x2 <= x1 or y2 <= y1:
        return []
    img_bytes = _take_screenshot(ctx)
    arr = np.frombuffer(img_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    h, w = img.shape[:2]
    x1 = max(0, min(x1, w - 1))
    x2 = max(0, min(x2, w))
    y1 = max(0, min(y1, h - 1))
    y2 = max(0, min(y2, h))
    if x2 <= x1 or y2 <= y1:
        return []
    crop = img[y1:y2, x1:x2]
    ok, enc = cv2.imencode(".png", crop)
    if not ok:
        return []
    boxes = ocr_image(enc.tobytes(), lang=normalize_ocr_lang(lang), psm=int(psm))
    out = []
    for b in boxes:
        if not isinstance(b, dict):
            continue
        txt = str(b.get("text", ""))
        t = _normalize_text(txt, case_sensitive=case_sensitive)
        for q in [_normalize_text(s, case_sensitive=case_sensitive) for s in texts if str(s or "").strip()]:
            ok_match = (t == q) if exact else (q in t)
            if ok_match:
                if "center_x" not in b or "center_y" not in b:
                    cx, cy = _box_center(b)
                    b = dict(b)
                    b["center_x"] = cx
                    b["center_y"] = cy
                if "x2" not in b or "y2" not in b:
                    bx1 = int(b.get("x", 0))
                    by1 = int(b.get("y", 0))
                    bw = int(b.get("w", 0))
                    bh = int(b.get("h", 0))
                    b["x2"] = bx1 + bw
                    b["y2"] = by1 + bh
                # shift to full-screen coords
                b["x"] = int(b.get("x", 0)) + x1
                b["y"] = int(b.get("y", 0)) + y1
                b["x2"] = int(b.get("x2", 0)) + x1
                b["y2"] = int(b.get("y2", 0)) + y1
                b["center_x"] = int(b.get("center_x", 0)) + x1
                b["center_y"] = int(b.get("center_y", 0)) + y1
                out.append(b)
                break
    return out


def _ocr_find_text_boxes_regex(
    ctx: ScriptContext,
    *,
    pattern: re.Pattern,
    lang: Optional[str] = None,
    psm: int = 6,
    roi: Optional[tuple[int, int, int, int]] = None,
) -> list[dict]:
    boxes = None
    img_bytes = None
    last_err: Exception | None = None
    for _ in range(3):
        try:
            img_bytes = _take_screenshot(ctx)
            if roi:
                try:
                    import cv2
                    import numpy as np
                except Exception:
                    break
                arr = np.frombuffer(img_bytes, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is None:
                    break
                x1, y1, x2, y2 = roi
                x1 = max(0, min(x1, img.shape[1] - 1))
                y1 = max(0, min(y1, img.shape[0] - 1))
                x2 = max(x1 + 1, min(x2, img.shape[1]))
                y2 = max(y1 + 1, min(y2, img.shape[0]))
                crop = img[y1:y2, x1:x2]
                ok, enc = cv2.imencode(".png", crop)
                if not ok:
                    break
                img_bytes = enc.tobytes()
            boxes = ocr_image(img_bytes, lang=normalize_ocr_lang(lang), psm=int(psm))
            break
        except Exception as e:
            last_err = e
            boxes = None
            time.sleep(0.15)
    if boxes is None:
        if last_err:
            pass
        return []
    out: list[dict] = []
    for b in boxes:
        if not isinstance(b, dict):
            continue
        txt = str(b.get("text", ""))
        if pattern.search(txt):
            if roi:
                b = dict(b)
                b["x"] = int(b.get("x", 0)) + roi[0]
                b["y"] = int(b.get("y", 0)) + roi[1]
                b["x2"] = int(b.get("x2", 0)) + roi[0]
                b["y2"] = int(b.get("y2", 0)) + roi[1]
                b["center_x"] = int(b.get("center_x", 0)) + roi[0]
                b["center_y"] = int(b.get("center_y", 0)) + roi[1]
            out.append(b)
    return out
