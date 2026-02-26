from __future__ import annotations

from pathlib import Path

from phone_pilot.extensions.vision.template import match_template, match_template_with_features
from phone_pilot.extensions.vision.markup import extract_red_box_crop


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_DIR = ROOT / "phone_pilot" / "resource"
TEST_DIR = RESOURCE_DIR / "test"


CASES = [
    {
        "name": "gravity_main",
        "screen": TEST_DIR / "test_gravity_main.png",
        "template": RESOURCE_DIR / "gravity_ad_test_step1.png",
        "must_fail": False,
    },
    {
        "name": "gravity_personal",
        "screen": TEST_DIR / "test_gravity_personal.png",
        "template": RESOURCE_DIR / "gravity_ad_test_step2.png",
        "must_fail": False,
    },
    {
        "name": "gravity_task_center",
        "screen": TEST_DIR / "test_gravity_task_center.png",
        "template": RESOURCE_DIR / "gravity_ad_test_step4.png",
        "must_fail": True,
    },
    {
        "name": "gravity_task_center_adshow",
        "screen": TEST_DIR / "test_gravity_task_center_adshow.png",
        "template": RESOURCE_DIR / "gravity_ad_test_step4.png",
        "must_fail": False,
    },
]


def _match(screen_bytes: bytes, template_bytes: bytes, *, roi=None) -> dict:
    return match_template(
        screen_bytes,
        template_bytes,
        threshold=0.8,
        grayscale=True,
        roi=roi,
        scales=[0.9, 1.0, 1.1],
        method="ccoeff_normed",
        max_results=5,
    )


def _match_with_features(screen_bytes: bytes, template_bytes: bytes) -> dict:
    return match_template_with_features(
        screen_bytes,
        template_bytes,
        threshold=0.8,
        grayscale=True,
        scales=[0.9, 1.0, 1.1],
        method="ccoeff_normed",
        max_results=5,
        feature_enabled=True,
    )


def _count(res: dict) -> int:
    if not isinstance(res, dict) or not res.get("ok"):
        return 0
    return int(res.get("count") or 0)


def test_marked_box_dataset():
    for case in CASES:
        screen_bytes = Path(case["screen"]).read_bytes()
        template_bytes = Path(case["template"]).read_bytes()

        crop_res = extract_red_box_crop(template_bytes, padding=4)
        if crop_res.get("ok"):
            bbox = crop_res.get("bbox")
            if bbox:
                x, y, w, h = bbox
                pad = max(12, int(max(w, h) * 0.2))
                roi = (max(0, x - pad), max(0, y - pad), w + pad * 2, h + pad * 2)
            else:
                roi = None
            roi_match = _match(screen_bytes, crop_res["crop_bytes"], roi=roi)
            full_match = _match_with_features(screen_bytes, crop_res["crop_bytes"])
        else:
            roi_match = {"ok": False, "count": 0}
            full_match = {"ok": False, "count": 0}

        roi_count = _count(roi_match)
        full_count = _count(full_match)

        if case["must_fail"]:
            assert roi_count == 0 and full_count == 0, f"{case['name']} should not match"
