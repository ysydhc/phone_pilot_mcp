#!/usr/bin/env python3
"""
Simple image crop helpers (OpenCV).

We use OpenCV here because the project already depends on opencv-python-headless.
"""

from __future__ import annotations

import pathlib
from typing import Tuple

import cv2


def crop_image_file(
    in_path: pathlib.Path,
    *,
    bounds: Tuple[int, int, int, int],
    out_path: pathlib.Path,
    padding: int = 0,
) -> pathlib.Path:
    """
    Crop an image by bounds (x1,y1,x2,y2) and save to out_path.
    """
    x1, y1, x2, y2 = (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
    pad = max(0, int(padding))
    img = cv2.imread(str(in_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError(f"failed_to_read_image: {in_path}")
    h, w = img.shape[:2]

    x1p = max(0, min(w, x1 - pad))
    y1p = max(0, min(h, y1 - pad))
    x2p = max(0, min(w, x2 + pad))
    y2p = max(0, min(h, y2 + pad))
    if x2p <= x1p or y2p <= y1p:
        raise ValueError(f"invalid_crop_bounds: {(x1p, y1p, x2p, y2p)} for image {w}x{h}")

    crop = img[y1p:y2p, x1p:x2p]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(out_path), crop)
    if not ok:
        raise RuntimeError(f"failed_to_write_image: {out_path}")
    return out_path


