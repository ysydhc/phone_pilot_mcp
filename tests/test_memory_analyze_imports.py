import unittest


class TestMemoryAnalyzeImports(unittest.TestCase):
    def test_memory_analyze_module_available(self) -> None:
        import phone_pilot.memory_analyze.dumper as dumper

        self.assertTrue(hasattr(dumper, "dump_hprof"))
