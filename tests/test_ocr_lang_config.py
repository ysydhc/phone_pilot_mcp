import os
import unittest


class TestOcrLangConfig(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = dict(os.environ)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_default_env_override(self) -> None:
        os.environ["PHONE_PILOT_OCR_LANG"] = "eng+chi_sim+chi_tra"
        from phone_pilot.extensions.ocr.config import get_default_ocr_lang

        self.assertEqual(get_default_ocr_lang(), "eng+chi_sim+chi_tra")

    def test_label_mapping(self) -> None:
        from phone_pilot.extensions.ocr.config import (
            MULTI_OCR_LANG,
            resolve_ocr_lang_label,
        )

        self.assertEqual(resolve_ocr_lang_label("日文"), "jpn")
        self.assertEqual(resolve_ocr_lang_label("简体中文"), "chi_sim")
        self.assertEqual(resolve_ocr_lang_label("繁体中文"), "chi_tra")
        self.assertEqual(resolve_ocr_lang_label("多语种"), MULTI_OCR_LANG)


if __name__ == "__main__":
    unittest.main()
