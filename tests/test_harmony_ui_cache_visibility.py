"""Tests for Harmony UI snapshot cache and visibility enrichment.

100% mock-based tests covering:
1. 500ms UI hierarchy cache hit and invalidation (both string and dict).
2. _element_info_to_dict producing standardized bounds_rect and center coordinates.
3. _enrich_with_visibility calculations (full 1.0, partial 0.5, off-screen 0.0, None).
4. Graceful degradation when screen size query fails (visible_reason = "screen_size_unavailable").
5. find_elements and find_all_elements containing enrichment fields.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from phone_pilot.core import ui_snapshot_cache
from phone_pilot.harmony.driver import (
    HarmonyUIDriver,
    _element_info_to_dict,
    _parse_bounds_rect,
)


class TestHarmonyUICache(unittest.TestCase):
    """Test 500ms caching and invalidation for dump_ui_hierarchy and dump_ui_hierarchy_dict."""

    def setUp(self):
        ui_snapshot_cache.invalidate()
        self.serial = "mock_harmony_serial_01"
        self.driver = HarmonyUIDriver(self.serial)

    def tearDown(self):
        ui_snapshot_cache.invalidate()

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_dump_ui_hierarchy_str_cache_hit_and_invalidate(self, mock_get_hm):
        mock_hm = MagicMock()
        mock_hm.dump_hierarchy.return_value = {
            "attributes": {"text": "Root", "type": "View"},
            "children": [],
        }
        mock_get_hm.return_value = mock_hm

        # First call: computes and caches
        res1 = self.driver.dump_ui_hierarchy()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 1)
        parsed1 = json.loads(res1)
        self.assertEqual(parsed1["attributes"]["text"], "Root")

        # Second call within TTL: should hit cache
        res2 = self.driver.dump_ui_hierarchy()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 1)
        self.assertEqual(res1, res2)

        # Invalidate device cache
        ui_snapshot_cache.invalidate_device_ui(self.serial, platform="harmony")

        # Third call: cache was invalidated, must compute again
        res3 = self.driver.dump_ui_hierarchy()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 2)
        self.assertEqual(res1, res3)

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_dump_ui_hierarchy_dict_cache_hit_and_invalidate(self, mock_get_hm):
        mock_hm = MagicMock()
        hierarchy_dict = {
            "attributes": {"text": "AppRoot", "type": "Component"},
            "children": [{"attributes": {"text": "Btn", "type": "Button"}}],
        }
        mock_hm.dump_hierarchy.return_value = hierarchy_dict
        mock_get_hm.return_value = mock_hm

        # First call: computes and caches
        d1 = self.driver.dump_ui_hierarchy_dict()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 1)
        self.assertEqual(d1["attributes"]["text"], "AppRoot")

        # Second call: hits cache
        d2 = self.driver.dump_ui_hierarchy_dict()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 1)
        self.assertIs(d1, d2)

        # Invalidate via platform and device
        ui_snapshot_cache.invalidate(platform="harmony", device_serial=self.serial)

        # Third call: computes again
        d3 = self.driver.dump_ui_hierarchy_dict()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 2)
        self.assertEqual(d3["attributes"]["text"], "AppRoot")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_cache_ttl_expiration(self, mock_get_hm):
        mock_hm = MagicMock()
        mock_hm.dump_hierarchy.return_value = {"text": "Snapshot"}
        mock_get_hm.return_value = mock_hm

        # Call with normal cache
        res1 = self.driver.dump_ui_hierarchy()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 1)

        # Fast-forward / simulate time passing beyond TTL by manually updating entry created_at
        key = ("harmony", self.serial, "ui_str")
        with ui_snapshot_cache._lock:
            if key in ui_snapshot_cache._cache:
                ui_snapshot_cache._cache[key].created_at -= 1.0  # 1s ago > 0.5s TTL

        res2 = self.driver.dump_ui_hierarchy()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 2)

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_dump_ui_nodes_and_collect_all_texts_share_cache(self, mock_get_hm):
        mock_hm = MagicMock()
        mock_hm.dump_hierarchy.return_value = {
            "attributes": {"text": "Title", "type": "Text"},
            "children": [
                {"attributes": {"text": "ClickMe", "type": "Button"}, "children": []}
            ],
        }
        mock_get_hm.return_value = mock_hm

        # Call dump_ui_nodes
        nodes = self.driver.dump_ui_nodes()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 1)
        self.assertTrue(len(nodes) > 0)

        # Call collect_all_texts within cache TTL
        texts = self.driver.collect_all_texts()
        self.assertEqual(mock_hm.dump_hierarchy.call_count, 1)  # Cache hit, count still 1
        self.assertIn("Title", texts)
        self.assertIn("ClickMe", texts)


class TestBoundsRectParsing(unittest.TestCase):
    """Test _parse_bounds_rect and _element_info_to_dict bounds_rect standardization."""

    def test_parse_bounds_rect_formats(self):
        # Dict format
        self.assertEqual(
            _parse_bounds_rect({"left": 10, "top": 20, "right": 110, "bottom": 120}),
            [10, 20, 110, 120],
        )
        # List/tuple format
        self.assertEqual(_parse_bounds_rect([10, 20, 110, 120]), [10, 20, 110, 120])
        self.assertEqual(_parse_bounds_rect((10, 20, 110, 120)), [10, 20, 110, 120])
        # String UIAutomator format
        self.assertEqual(_parse_bounds_rect("[10,20][110,120]"), [10, 20, 110, 120])
        # String with negative coordinates
        self.assertEqual(_parse_bounds_rect("[-10,-20][90,80]"), [-10, -20, 90, 80])
        # Object with attributes
        obj = SimpleNamespace(left=10, top=20, right=110, bottom=120)
        self.assertEqual(_parse_bounds_rect(obj), [10, 20, 110, 120])
        # Invalid / missing
        self.assertIsNone(_parse_bounds_rect(None))
        self.assertIsNone(_parse_bounds_rect([]))
        self.assertIsNone(_parse_bounds_rect("invalid"))
        self.assertIsNone(_parse_bounds_rect({"left": 10}))

    def test_element_info_to_dict_with_dict_bounds(self):
        info = MagicMock()
        info.to_dict.return_value = {
            "id": "btn_login",
            "type": "Button",
            "text": "Login",
            "bounds": {"left": 100, "top": 200, "right": 300, "bottom": 260},
        }
        d = _element_info_to_dict(info)
        self.assertEqual(d["bounds_rect"], [100, 200, 300, 260])
        self.assertEqual(d["center_x"], 200)
        self.assertEqual(d["center_y"], 230)
        self.assertEqual(d["text"], "Login")

    def test_element_info_to_dict_with_attribute_bounds(self):
        info = SimpleNamespace(
            id="text_title",
            type="Text",
            text="Welcome",
            bounds=SimpleNamespace(left=0, top=50, right=1080, bottom=150),
        )
        d = _element_info_to_dict(info)
        self.assertEqual(d["bounds_rect"], [0, 50, 1080, 150])
        self.assertEqual(d["center_x"], 540)
        self.assertEqual(d["center_y"], 100)
        self.assertEqual(
            d["bounds"], {"left": 0, "top": 50, "right": 1080, "bottom": 150}
        )

    def test_element_info_to_dict_with_string_bounds(self):
        info = {
            "type": "Image",
            "bounds": "[50,100][250,300]",
        }
        d = _element_info_to_dict(info)
        self.assertEqual(d["bounds_rect"], [50, 100, 250, 300])
        self.assertEqual(d["center_x"], 150)
        self.assertEqual(d["center_y"], 200)

    def test_element_info_to_dict_without_bounds(self):
        info = {"type": "Container", "id": "box"}
        d = _element_info_to_dict(info)
        self.assertIsNone(d["bounds_rect"])
        self.assertNotIn("center_x", d)
        self.assertNotIn("center_y", d)


class TestVisibilityEnrichment(unittest.TestCase):
    """Test _enrich_with_visibility and get_screen_size behavior."""

    def setUp(self):
        self.driver = HarmonyUIDriver("mock_serial")

    @patch("phone_pilot.harmony.device.utils.get_screen_size", return_value=(1080, 2400))
    def test_get_screen_size_delegation(self, mock_utils_screen_size):
        size = self.driver.get_screen_size()
        self.assertEqual(size, (1080, 2400))
        mock_utils_screen_size.assert_called_once_with("mock_serial")

    @patch("phone_pilot.harmony.device.utils.get_screen_size", return_value=None)
    def test_get_screen_size_failure_raises(self, mock_utils_screen_size):
        with self.assertRaises(RuntimeError):
            self.driver.get_screen_size()

    @patch.object(HarmonyUIDriver, "get_screen_size", return_value=(1080, 2400))
    def test_enrich_with_visibility_full(self, _mock_screen):
        results = [{"id": "elem1", "bounds_rect": [100, 200, 300, 400]}]
        enriched = self.driver._enrich_with_visibility(results)
        self.assertEqual(len(enriched), 1)
        self.assertTrue(enriched[0]["visible"])
        self.assertEqual(enriched[0]["visible_ratio"], 1.0)
        self.assertNotIn("visible_reason", enriched[0])

    @patch.object(HarmonyUIDriver, "get_screen_size", return_value=(1080, 2400))
    def test_enrich_with_visibility_partial(self, _mock_screen):
        # Element bounds: [0, 2000, 1080, 2800] -> height 800, visible height 400 (ratio = 0.5)
        results = [{"id": "elem_half", "bounds_rect": [0, 2000, 1080, 2800]}]
        enriched = self.driver._enrich_with_visibility(results)
        self.assertEqual(len(enriched), 1)
        self.assertTrue(enriched[0]["visible"])
        self.assertAlmostEqual(enriched[0]["visible_ratio"], 0.5)

    @patch.object(HarmonyUIDriver, "get_screen_size", return_value=(1080, 2400))
    def test_enrich_with_visibility_offscreen(self, _mock_screen):
        # Element bounds completely beyond bottom of screen (y > 2400)
        results = [{"id": "elem_offscreen", "bounds_rect": [0, 2500, 1080, 2800]}]
        enriched = self.driver._enrich_with_visibility(results)
        self.assertEqual(len(enriched), 1)
        self.assertFalse(enriched[0]["visible"])
        self.assertEqual(enriched[0]["visible_ratio"], 0.0)

    @patch.object(HarmonyUIDriver, "get_screen_size", return_value=(1080, 2400))
    def test_enrich_with_visibility_missing_bounds(self, _mock_screen):
        results = [{"id": "elem_nobounds", "bounds_rect": None}]
        enriched = self.driver._enrich_with_visibility(results)
        self.assertIsNone(enriched[0]["visible"])
        self.assertIsNone(enriched[0]["visible_ratio"])

    @patch.object(
        HarmonyUIDriver,
        "get_screen_size",
        side_effect=RuntimeError("Screen size query failed"),
    )
    def test_enrich_with_visibility_screen_size_unavailable(self, _mock_screen):
        results = [{"id": "elem1", "bounds_rect": [100, 200, 300, 400]}]
        enriched = self.driver._enrich_with_visibility(results)
        self.assertEqual(len(enriched), 1)
        self.assertIsNone(enriched[0]["visible"])
        self.assertIsNone(enriched[0]["visible_ratio"])
        self.assertEqual(enriched[0]["visible_reason"], "screen_size_unavailable")

    def test_enrich_with_visibility_empty_results(self):
        enriched = self.driver._enrich_with_visibility([])
        self.assertEqual(enriched, [])


class TestFindElementsEnrichment(unittest.TestCase):
    """Test find_elements and find_all_elements return enriched elements."""

    def setUp(self):
        self.serial = "mock_serial_find"
        self.driver = HarmonyUIDriver(self.serial)

    @patch("phone_pilot.harmony.device.utils.get_screen_size", return_value=(1080, 2400))
    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_elements_enriched(self, mock_get_hm, _mock_screen):
        mock_comp = MagicMock()
        mock_comp.to_dict.return_value = {
            "text": "Submit",
            "type": "Button",
            "bounds": {"left": 200, "top": 500, "right": 400, "bottom": 600},
        }

        mock_ui_obj = MagicMock()
        mock_ui_obj.find_component.return_value = mock_comp

        mock_hm = MagicMock()
        mock_hm.return_value = mock_ui_obj
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"text": "Submit"})
        self.assertEqual(len(results), 1)
        elem = results[0]
        self.assertEqual(elem["text"], "Submit")
        self.assertEqual(elem["bounds_rect"], [200, 500, 400, 600])
        self.assertEqual(elem["center_x"], 300)
        self.assertEqual(elem["center_y"], 550)
        self.assertTrue(elem["visible"])
        self.assertEqual(elem["visible_ratio"], 1.0)

    @patch("phone_pilot.harmony.device.utils.get_screen_size", return_value=(1080, 2400))
    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_elements_empty_not_found(self, mock_get_hm, _mock_screen):
        mock_ui_obj = MagicMock()
        mock_ui_obj.find_component.return_value = None

        mock_hm = MagicMock()
        mock_hm.return_value = mock_ui_obj
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"text": "NonExistent"})
        self.assertEqual(results, [])

    @patch("phone_pilot.harmony.device.utils.get_screen_size", return_value=(1080, 2400))
    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_all_elements_enriched(self, mock_get_hm, _mock_screen):
        comp1 = MagicMock()
        comp1.to_dict.return_value = {
            "text": "Item1",
            "type": "Text",
            "bounds": {"left": 0, "top": 100, "right": 500, "bottom": 200},
        }
        comp2 = MagicMock()
        comp2.to_dict.return_value = {
            "text": "Item2",
            "type": "Text",
            "bounds": {
                "left": 0,
                "top": 2500,
                "right": 500,
                "bottom": 2600,
            },  # offscreen
        }

        mock_ui_obj = MagicMock()
        mock_ui_obj.count.return_value = 2

        mock_hm = MagicMock()

        # When called without index, returns count mock; when called with index, returns component
        def _hm_call(**kwargs):
            if "index" in kwargs:
                idx = kwargs["index"]
                obj = MagicMock()
                obj.find_component.return_value = comp1 if idx == 0 else comp2
                return obj
            return mock_ui_obj

        mock_hm.side_effect = _hm_call
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_all_elements({"class_name": "Text"})
        self.assertEqual(len(results), 2)

        self.assertEqual(results[0]["text"], "Item1")
        self.assertEqual(results[0]["bounds_rect"], [0, 100, 500, 200])
        self.assertTrue(results[0]["visible"])
        self.assertEqual(results[0]["visible_ratio"], 1.0)

        self.assertEqual(results[1]["text"], "Item2")
        self.assertEqual(results[1]["bounds_rect"], [0, 2500, 500, 2600])
        self.assertFalse(results[1]["visible"])
        self.assertEqual(results[1]["visible_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
