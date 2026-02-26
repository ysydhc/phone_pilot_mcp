from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phone_pilot.extensions.vision.markup import extract_red_box_crop  # noqa: E402


def _encode_png(img_bgr) -> bytes:
    ok, buf = cv2.imencode(".png", img_bgr)
    assert ok
    return bytes(buf)


def test_extract_red_box_crop_returns_inner_region():
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    cv2.rectangle(img, (40, 60), (160, 140), (0, 0, 255), thickness=4)
    png = _encode_png(img)

    res = extract_red_box_crop(png, padding=4)
    assert res["ok"] is True
    x, y, w, h = res["bbox"]
    assert 35 <= x <= 45
    assert 55 <= y <= 65
    assert w > 80 and h > 60
    assert res["crop_bytes"]
