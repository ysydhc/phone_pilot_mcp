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
    Crop an image by bounds (x1, y1, x2, y2) and save to out_path.

    Args:
        in_path: Path to input image
        bounds: Tuple of (x1, y1, x2, y2) defining crop region
        out_path: Path for output cropped image
        padding: Extra padding around the crop region

    Returns:
        Path to the saved cropped image
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


def crop_image_bytes(
    image_bytes: bytes,
    *,
    bounds: Tuple[int, int, int, int],
    padding: int = 0,
) -> bytes:
    """
    Crop an image from bytes and return cropped image as PNG bytes.

    Args:
        image_bytes: Input image as bytes (PNG/JPEG)
        bounds: Tuple of (x1, y1, x2, y2) defining crop region
        padding: Extra padding around the crop region

    Returns:
        Cropped image as PNG bytes
    """
    import numpy as np

    x1, y1, x2, y2 = (int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3]))
    pad = max(0, int(padding))

    # Decode image bytes
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise RuntimeError("failed_to_decode_image_bytes")
    h, w = img.shape[:2]

    x1p = max(0, min(w, x1 - pad))
    y1p = max(0, min(h, y1 - pad))
    x2p = max(0, min(w, x2 + pad))
    y2p = max(0, min(h, y2 + pad))
    if x2p <= x1p or y2p <= y1p:
        raise ValueError(f"invalid_crop_bounds: {(x1p, y1p, x2p, y2p)} for image {w}x{h}")

    crop = img[y1p:y2p, x1p:x2p]

    # Encode back to PNG
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError("failed_to_encode_cropped_image")
    return encoded.tobytes()


__all__ = [
    "crop_image_file",
    "crop_image_bytes",
]
