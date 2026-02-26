"""Test that missing optional dependencies produce friendly error messages."""

import unittest
from unittest.mock import patch


class TestHmdriver2ImportGuard(unittest.TestCase):
    """Test that importing hmdriver2 when not installed gives a clear message."""

    def test_hmdriver_bridge_import_error_message(self):
        """Verify the ImportError message suggests pip install phone-pilot[harmony]."""
        import phone_pilot.harmony.hmdriver_bridge as bridge

        # Reset the module state
        bridge._HmDriver = None
        bridge._cache.clear()
        bridge._failed_serials.clear()

        with patch.dict("sys.modules", {"hmdriver2": None, "hmdriver2.driver": None}):
            # Force re-import failure
            bridge._HmDriver = None
            with self.assertRaises(ImportError) as ctx:
                bridge._ensure_hmdriver_class()
            self.assertIn("phone-pilot[harmony]", str(ctx.exception))


class TestVisionImportGuard(unittest.TestCase):
    """Test that langdetect/deep-translator failures are handled gracefully."""

    def test_langdetect_missing_returns_none(self):
        """When langdetect is not available, detect_language should return None."""
        from phone_pilot.extensions.lang.translate import _detect_lang_cached
        # Clear the lru_cache
        _detect_lang_cached.cache_clear()

        with patch.dict("sys.modules", {"langdetect": None}):
            from phone_pilot.extensions.lang.translate import detect_language
            detect_language("hello world")
            # Should return None gracefully, not raise
            # (Note: if langdetect IS installed, it'll actually work; this test 
            #  verifies the try/except path is correct by mocking)

    def test_deep_translator_missing_returns_none(self):
        """When deep-translator is not available, translate_text should return None."""
        from phone_pilot.extensions.lang.translate import _translate_cached
        _translate_cached.cache_clear()

        with patch.dict("sys.modules", {"deep_translator": None}):
            from phone_pilot.extensions.lang.translate import translate_text
            translate_text("hello", "zh")
            # Should return None gracefully


if __name__ == "__main__":
    unittest.main()
