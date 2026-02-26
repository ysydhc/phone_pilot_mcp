"""
Detect and crop red box markup on screenshots.
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from phone_pilot.extensions.vision.template import decode_png_to_bgr


def _detect_red_box_bbox(img_bgr) -> Optional[tuple[int, int, int, int]]:
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    mask1 = cv2.inRange(hsv, (0, 120, 120), (10, 255, 255))
    mask2 = cv2.inRange(hsv, (170, 120, 120), (180, 255, 255))
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    x, y, w, h = cv2.boundingRect(cnt)
    return int(x), int(y), int(w), int(h)


def extract_red_box_crop(image_bytes: bytes, *, padding: int = 4) -> dict:
    """
    Extract inner crop of a red rectangular markup box.

    Returns:
        {"ok": bool, "bbox": (x,y,w,h), "crop_bytes": bytes}
    """
    try:
        bgr = decode_png_to_bgr(image_bytes)
    except Exception as e:
        return {"ok": False, "error": "decode_failed", "detail": str(e)}

    bbox = _detect_red_box_bbox(bgr)
    if not bbox:
        return {"ok": False, "error": "no_red_box_found"}
    x, y, w, h = bbox
    pad = max(0, int(padding))
    x1 = max(0, x + pad)
    y1 = max(0, y + pad)
    x2 = min(bgr.shape[1], x + w - pad)
    y2 = min(bgr.shape[0], y + h - pad)
    if x2 <= x1 or y2 <= y1:
        return {"ok": False, "error": "invalid_crop"}
    crop = bgr[y1:y2, x1:x2]
    ok, buf = cv2.imencode(".png", crop)
    if not ok:
        return {"ok": False, "error": "encode_failed"}
    return {"ok": True, "bbox": (x, y, w, h), "crop_bytes": bytes(buf)}


__all__ = ["extract_red_box_crop"]
