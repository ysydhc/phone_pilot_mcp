import os
import tempfile
import unittest

from phone_pilot.extensions.lang import translate


class TestTranslateSynonyms(unittest.TestCase):
    def test_synonym_candidates_from_runtime_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "phone_pilot.db")
            os.environ["PHONE_PILOT_DB_PATH"] = db_path
            os.environ["PHONE_PILOT_DISABLE_TRANSLATION"] = "1"

            base = translate.translate_candidates("个人中心")
            self.assertEqual(base, ["个人中心"])

            translate.record_synonym("个人中心", "个人资料")
            translate.record_synonym("个人中心", "我的")

            cands = translate.translate_candidates("个人中心")
            self.assertIn("个人资料", cands)
            self.assertIn("我的", cands)
