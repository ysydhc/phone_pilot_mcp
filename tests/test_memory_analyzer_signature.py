import unittest

from phone_pilot.memory_analyze import analyzer


class TestMemoryAnalyzerSignature(unittest.TestCase):
    def test_analyze_hprof_accepts_threshold(self) -> None:
        res = analyzer.analyze_hprof("/tmp/not_exists.hprof", large_object_threshold=1234)
        self.assertFalse(res.get("ok", True))

    def test_diff_hprof_accepts_threshold(self) -> None:
        res = analyzer.diff_hprof("/tmp/a.hprof", "/tmp/b.hprof", large_object_threshold=1234)
        self.assertFalse(res.get("ok", True))
