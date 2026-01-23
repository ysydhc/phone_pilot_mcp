#!/usr/bin/env python3
"""
OCR helpers (Tesseract CLI) for Android automation.

We intentionally use the `tesseract` binary directly (via subprocess) to avoid
heavy Python OCR dependencies. This requires tesseract to be installed on the host.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional

from android_tool.screenshot import save_screenshot_png


@dataclass
class OCRBox:
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
    exe = shutil.which("tesseract")
    if not exe:
        raise RuntimeError("tesseract not found on PATH. Install it (e.g. `brew install tesseract`).")
    return exe


def run_tesseract_tsv(
    image_path: pathlib.Path,
    *,
    lang: str = "eng",
    psm: int = 6,
) -> str:
    """
    Run tesseract and return TSV text (includes bounding boxes).
    """
    _ensure_tesseract()
    img = pathlib.Path(image_path).expanduser().resolve()
    if not img.exists():
        raise RuntimeError(f"image not found: {img}")
    # Output to stdout with TSV format.
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
    proc = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed (rc={proc.returncode}): {(proc.stderr or '').strip()}")
    return proc.stdout or ""


def parse_tesseract_tsv(tsv: str) -> list[OCRBox]:
    """
    Parse tesseract TSV output into OCRBox list (word-level rows).
    """
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
        # level 5 is word-level in tesseract TSV.
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


def find_text_boxes(
    boxes: list[OCRBox],
    *,
    query: str,
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> list[OCRBox]:
    q = (query or "").strip()
    if not q:
        return []
    qq = q if case_sensitive else q.lower()

    def norm(s: str) -> str:
        return s if case_sensitive else s.lower()

    hits: list[OCRBox] = []
    for b in boxes:
        t = norm(b.text)
        ok = (t == qq) if exact else (qq in t)
        if ok:
            hits.append(b)
            if limit and len(hits) >= int(limit):
                break
    # Prefer higher confidence first
    hits.sort(key=lambda x: float(x.conf), reverse=True)
    return hits[: max(1, int(limit))]


def ocr_screenshot_and_find(
    device_serial: Optional[str],
    *,
    out_dir: str,
    name: str,
    query: str,
    lang: str = "eng",
    psm: int = 6,
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> dict:
    """
    Take a screenshot to out_dir, OCR it, and find matching text boxes.
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}
    out_root = pathlib.Path(out_dir).expanduser().resolve()
    out_path = out_root / f"{name}.png"
    out_root.mkdir(parents=True, exist_ok=True)
    save_screenshot_png(device_serial, out_path)

    tsv = run_tesseract_tsv(out_path, lang=lang, psm=psm)
    boxes = parse_tesseract_tsv(tsv)
    hits = find_text_boxes(
        boxes,
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
        "matches": [h.to_dict() for h in hits],
    }


