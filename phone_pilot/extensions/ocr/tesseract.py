"""
Tesseract OCR integration.

Platform-agnostic: operates on bytes, no device dependencies.
Uses tesseract CLI binary (must be installed on host).
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from typing import Optional

from phone_pilot.extensions.ocr.config import normalize_ocr_lang


@dataclass
class OCRBox:
    """OCR text box result."""
    text: str
    x: int
    y: int
    w: int
    h: int
    conf: float

    @property
    def center(self) -> tuple[int, int]:
        return int(self.x + self.w / 2), int(self.y + self.h / 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        cx, cy = self.center
        d["center_x"] = cx
        d["center_y"] = cy
        return d


def _ensure_tesseract() -> str:
    """Check that tesseract is installed."""
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError(
            "tesseract not found on PATH. Install it (e.g. `brew install tesseract`)."
        )
    return exe


def _run_tesseract_tsv(
    image_path: pathlib.Path,
    *,
    lang: str = "eng",
    psm: int = 6,
) -> str:
    """Run tesseract and return TSV text (includes bounding boxes)."""
    _ensure_tesseract()
    img = pathlib.Path(image_path).expanduser().resolve()
    if not img.exists():
        raise RuntimeError(f"image not found: {img}")
    
    cmd = [
        "tesseract",
        str(img),
        "stdout",
        "-l",
        str(lang),
        "--psm",
        str(int(psm)),
        "tsv",
    ]
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed (rc={proc.returncode}): {(proc.stderr or '').strip()}")
    return proc.stdout or ""


def _parse_tesseract_tsv(tsv: str) -> list[OCRBox]:
    """Parse tesseract TSV output into OCRBox list (word-level rows)."""
    boxes: list[OCRBox] = []
    lines = (tsv or "").splitlines()
    if not lines:
        return boxes
    header = lines[0].split("\t")
    idx = {k: i for i, k in enumerate(header)}
    required = ["text", "left", "top", "width", "height", "conf", "level"]
    if any(k not in idx for k in required):
        return boxes

    for row in lines[1:]:
        cols = row.split("\t")
        try:
            level = int(cols[idx["level"]])
        except Exception:
            continue
        # level 5 is word-level in tesseract TSV
        if level != 5:
            continue
        txt = (cols[idx["text"]] or "").strip()
        if not txt:
            continue
        try:
            x = int(float(cols[idx["left"]]))
            y = int(float(cols[idx["top"]]))
            w = int(float(cols[idx["width"]]))
            h = int(float(cols[idx["height"]]))
        except Exception:
            continue
        try:
            conf = float(cols[idx["conf"]])
        except Exception:
            conf = -1.0
        boxes.append(OCRBox(text=txt, x=x, y=y, w=w, h=h, conf=conf))
    return boxes


def run_tesseract_tsv(
    image_path: pathlib.Path | str,
    *,
    lang: str | None = None,
    psm: int = 6,
) -> str:
    return _run_tesseract_tsv(pathlib.Path(image_path), lang=normalize_ocr_lang(lang), psm=psm)


def parse_tesseract_tsv(tsv: str) -> list[OCRBox]:
    return _parse_tesseract_tsv(tsv)


def find_text_boxes(
    boxes: list[OCRBox],
    *,
    query: str,
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
    fuzzy_threshold: float = 0.5,
) -> list[OCRBox]:
    q = (query or "").strip()
    if not q:
        return []
    qq = q if case_sensitive else q.lower()

    def norm(s: str) -> str:
        return s if case_sensitive else s.lower()

    hits: list[OCRBox] = []
    for b in boxes:
        text = b.text or ""
        t = norm(text)
        ok = (t == qq) if exact else (qq in t)
        if ok:
            hits.append(b)
            if limit and len(hits) >= int(limit):
                break

    if not hits and not exact:
        import difflib

        fuzzy_hits: list[tuple[float, float, OCRBox]] = []
        for b in boxes:
            text = b.text or ""
            t = norm(text)
            if not t:
                continue
            score = difflib.SequenceMatcher(None, t, qq).ratio()
            if score >= float(fuzzy_threshold):
                conf = float(b.conf)
                fuzzy_hits.append((score, conf, b))
        fuzzy_hits.sort(key=lambda x: (-x[0], -x[1]))
        hits = [b for _, _, b in fuzzy_hits]

    hits.sort(key=lambda x: float(x.conf), reverse=True)
    return hits[: max(1, int(limit))]


def ocr_image(
    image_bytes: bytes,
    *,
    lang: str | None = None,
    psm: int = 6,
) -> list[dict]:
    """
    Run OCR on image bytes.
    
    Args:
        image_bytes: Image PNG bytes
        lang: Tesseract language code (e.g., "eng", "chi_sim", "eng+chi_sim")
        psm: Page segmentation mode
            - 6: Assume a single uniform block of text (default)
            - 3: Fully automatic page segmentation
            - 11: Sparse text
        
    Returns:
        List of OCR boxes: [{"text", "x", "y", "w", "h", "conf", "center_x", "center_y"}]
    """
    lang = normalize_ocr_lang(lang)
    # Write bytes to temp file for tesseract CLI
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(image_bytes)
        temp_path = pathlib.Path(f.name)
    
    try:
        tsv = _run_tesseract_tsv(temp_path, lang=lang, psm=psm)
        boxes = _parse_tesseract_tsv(tsv)
        return [b.to_dict() for b in boxes]
    finally:
        # Clean up temp file
        temp_path.unlink(missing_ok=True)


def find_text(
    boxes: list[dict],
    query: str,
    *,
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
    fuzzy_threshold: float = 0.5,
) -> list[dict]:
    """
    Find text in OCR results.
    
    Args:
        boxes: OCR boxes from ocr_image()
        query: Text to search for
        exact: Require exact match
        case_sensitive: Case-sensitive matching
        limit: Maximum results
        
    Returns:
        List of matching boxes sorted by confidence
    """
    q = (query or "").strip()
    if not q:
        return []
    qq = q if case_sensitive else q.lower()

    def norm(s: str) -> str:
        return s if case_sensitive else s.lower()

    hits: list[dict] = []
    for b in boxes:
        text = b.get("text", "")
        t = norm(text)
        ok = (t == qq) if exact else (qq in t)
        if ok:
            hits.append(b)
            if limit and len(hits) >= int(limit):
                break

    if not hits and not exact:
        import difflib

        fuzzy_hits: list[tuple[float, float, dict]] = []
        for b in boxes:
            text = b.get("text", "")
            t = norm(text)
            if not t:
                continue
            score = difflib.SequenceMatcher(None, t, qq).ratio()
            if score >= float(fuzzy_threshold):
                conf = float(b.get("conf", -1))
                b2 = dict(b)
                b2["fuzzy_score"] = score
                fuzzy_hits.append((score, conf, b2))

        fuzzy_hits.sort(key=lambda x: (-x[0], -x[1]))
        hits = [b for _, _, b in fuzzy_hits]

    # Sort by confidence (higher first)
    hits.sort(key=lambda x: float(x.get("conf", -1)), reverse=True)
    return hits[: max(1, int(limit))]


def ocr_and_find(
    image_bytes: bytes,
    query: str,
    *,
    lang: str | None = None,
    psm: int = 6,
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> dict:
    """
    OCR image and find matching text in one call.
    
    Args:
        image_bytes: Image PNG bytes
        query: Text to search for
        lang: Tesseract language code
        psm: Page segmentation mode
        exact: Require exact match
        case_sensitive: Case-sensitive matching
        limit: Maximum results
        
    Returns:
        {
            "ok": bool,
            "boxes_count": int,
            "matches_count": int,
            "matches": [{"text", "x", "y", "w", "h", "conf", "center_x", "center_y"}],
        }
    """
    try:
        lang = normalize_ocr_lang(lang)
        boxes = ocr_image(image_bytes, lang=lang, psm=psm)
        matches = find_text(
            boxes,
            query,
            exact=exact,
            case_sensitive=case_sensitive,
            limit=limit,
        )
        return {
            "ok": True,
            "boxes_count": len(boxes),
            "matches_count": len(matches),
            "matches": matches,
            "lang": lang,
            "psm": psm,
            "query": query,
            "exact": exact,
            "case_sensitive": case_sensitive,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": "ocr_failed",
            "detail": str(e),
        }


def ocr_screenshot_and_find(
    device_serial: Optional[str],
    *,
    out_dir: str,
    name: str,
    query: str,
    lang: str | None = None,
    psm: int = 6,
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> dict:
    """
    Take a screenshot to out_dir, OCR it, and find matching text boxes.
    
    Args:
        device_serial: Android device serial
        out_dir: Directory to save screenshot
        name: Screenshot filename (without extension)
        query: Text to search for
        lang: Tesseract language code
        psm: Page segmentation mode
        exact: Require exact match
        case_sensitive: Case-sensitive matching
        limit: Maximum results
        
    Returns:
        {
            "ok": bool,
            "screenshot_path": str,
            "boxes_count": int,
            "matches_count": int,
            "matches": [...],
        }
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    
    lang = normalize_ocr_lang(lang)
    # Import here to avoid circular deps
    from phone_pilot.android.adb.screenshot import save_screenshot_png
    
    out_root = pathlib.Path(out_dir).expanduser().resolve()
    out_path = out_root / f"{name}.png"
    out_root.mkdir(parents=True, exist_ok=True)
    save_screenshot_png(device_serial, out_path)

    tsv = _run_tesseract_tsv(out_path, lang=lang, psm=psm)
    boxes = _parse_tesseract_tsv(tsv)
    
    # Convert to dict for find_text
    box_dicts = [b.to_dict() for b in boxes]
    hits = find_text(
        box_dicts,
        query=query,
        exact=exact,
        case_sensitive=case_sensitive,
        limit=limit,
    )
    return {
        "ok": True,
        "device_serial": device_serial,
        "screenshot_path": str(out_path),
        "lang": lang,
        "psm": int(psm),
        "query": query,
        "exact": bool(exact),
        "case_sensitive": bool(case_sensitive),
        "boxes_count": len(boxes),
        "matches_count": len(hits),
        "matches": hits,
    }
