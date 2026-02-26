import unittest

from phone_pilot.extensions.ocr.tesseract import find_text


class TestOcrFuzzy(unittest.TestCase):
    def test_fuzzy_match_when_no_contains(self) -> None:
        boxes = [
            {"text": "个人资料", "x": 10, "y": 10, "w": 20, "h": 10, "conf": 90.0},
            {"text": "设置", "x": 40, "y": 10, "w": 20, "h": 10, "conf": 90.0},
        ]

        hits = find_text(boxes, "个人中心", exact=False, case_sensitive=False, limit=1)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["text"], "个人资料")
