"""Unit tests for HarmonyOS XPath selector support in HarmonyUIDriver.

100% Mock-based tests covering:
a) hmdriver2.xpath priority dispatch when available (find_component, all, find_components, list);
b) Fallback to ElementTree when hmdriver2.xpath is unavailable, returns None, or raises an exception;
c) Diverse XPath syntax matching (by tag, attribute id/resource_id, text, description/content_desc,
   boolean clickable/scrollable, hierarchy path, and and-predicates);
d) Complete node enrichment (bounds_rect, bounds, boundsCenter, center_x, center_y, visible,
   visible_ratio, and graceful handling of screen_size_unavailable);
e) Behavior distinction between find_elements (single match) and find_all_elements (all matches).
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from phone_pilot.core import ui_snapshot_cache
from phone_pilot.harmony.driver import HarmonyUIDriver


SAMPLE_RICH_HIERARCHY = {
    "attributes": {
        "text": "",
        "type": "RootView",
        "id": "root_view",
        "bounds": {"left": 0, "top": 0, "right": 1080, "bottom": 2340},
    },
    "children": [
        {
            "attributes": {
                "text": "Welcome Back",
                "type": "Text",
                "id": "title_txt",
                "description": "Greeting Header",
                "bounds": {"left": 100, "top": 100, "right": 900, "bottom": 180},
                "boundsCenter": {"x": 500, "y": 140},
                "clickable": False,
            },
            "children": [],
        },
        {
            "attributes": {
                "text": "Login",
                "type": "Button",
                "id": "submit_btn",
                "description": "Login Button",
                "key": "btn_login_key",
                "bounds": {"left": 100, "top": 200, "right": 400, "bottom": 280},
                "boundsCenter": {"x": 250, "y": 240},
                "clickable": True,
                "enabled": True,
            },
            "children": [],
        },
        {
            "attributes": {
                "text": "Cancel",
                "type": "Button",
                "id": "cancel_btn",
                "description": "Cancel Button",
                "key": "btn_cancel_key",
                "bounds": {"left": 500, "top": 200, "right": 800, "bottom": 280},
                "boundsCenter": {"x": 650, "y": 240},
                "clickable": True,
                "enabled": True,
            },
            "children": [],
        },
        {
            "attributes": {
                "text": "Login",
                "type": "Text",
                "id": "label_txt",
                "bounds": {"left": 100, "top": 300, "right": 300, "bottom": 350},
                "boundsCenter": {"x": 200, "y": 325},
                "clickable": False,
            },
            "children": [],
        },
        {
            "attributes": {
                "text": "",
                "type": "List",
                "id": "item_list",
                "scrollable": True,
                "bounds": {"left": 0, "top": 400, "right": 1080, "bottom": 1800},
            },
            "children": [
                {
                    "attributes": {
                        "text": "Item 1",
                        "type": "ListItem",
                        "id": "item_1",
                        "bounds": {"left": 0, "top": 400, "right": 1080, "bottom": 600},
                        "clickable": True,
                    },
                    "children": [],
                },
                {
                    "attributes": {
                        "text": "Item 2",
                        "type": "ListItem",
                        "id": "item_2",
                        "bounds": {"left": 0, "top": 600, "right": 1080, "bottom": 800},
                        "clickable": True,
                    },
                    "children": [],
                },
            ],
        },
        {
            "attributes": {
                "text": "",
                "type": "Image",
                "id": "offscreen_img",
                "bounds": {"left": 0, "top": 3000, "right": 100, "bottom": 3100},
            },
            "children": [],
        },
    ],
}


class TestHarmonyXPathPriorityDispatch(unittest.TestCase):
    """Test priority dispatch to hmdriver2.xpath when available."""

    def setUp(self):
        ui_snapshot_cache.invalidate()
        self.serial = "mock_serial_harmony"
        self.driver = HarmonyUIDriver(self.serial)
        self.driver.get_screen_size = MagicMock(return_value=(1080, 2340))

    def tearDown(self):
        ui_snapshot_cache.invalidate()

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_elements_hm_xpath_priority_find_component(self, mock_get_hm):
        """When hmdriver2.xpath is present, find_elements should call find_component()."""
        mock_hm = MagicMock()
        mock_xpath_obj = MagicMock()
        mock_comp = SimpleNamespace(
            id="comp_btn",
            type="Button",
            text="Native Click",
            description="",
            key="",
            bounds=SimpleNamespace(left=50, top=60, right=150, bottom=160),
            boundsCenter=SimpleNamespace(x=100, y=110),
            clickable=True,
        )
        mock_xpath_obj.find_component.return_value = mock_comp
        mock_hm.xpath.return_value = mock_xpath_obj
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"xpath": "//Button[@text='Native Click']"})

        mock_hm.xpath.assert_called_once_with("//Button[@text='Native Click']")
        mock_xpath_obj.find_component.assert_called_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "comp_btn")
        self.assertEqual(results[0]["bounds_rect"], [50, 60, 150, 160])
        self.assertEqual(results[0]["center_x"], 100)
        self.assertEqual(results[0]["center_y"], 110)
        self.assertTrue(results[0]["visible"])
        self.assertEqual(results[0]["visible_ratio"], 1.0)
        # dump_hierarchy should not be called since xpath succeeded
        mock_hm.dump_hierarchy.assert_not_called()

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_elements_hm_xpath_priority_with_all(self, mock_get_hm):
        """When xpath_obj has all(), find_elements should return first item only."""
        mock_hm = MagicMock()
        mock_xpath_obj = MagicMock(spec=["all"])
        comp1 = SimpleNamespace(
            id="b1",
            type="Button",
            text="First",
            bounds={"left": 10, "top": 20, "right": 30, "bottom": 40},
        )
        comp2 = SimpleNamespace(
            id="b2",
            type="Button",
            text="Second",
            bounds={"left": 50, "top": 60, "right": 70, "bottom": 80},
        )
        mock_xpath_obj.all.return_value = [comp1, comp2]
        mock_hm.xpath.return_value = mock_xpath_obj
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"xpath": "//Button"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "b1")
        self.assertEqual(results[0]["bounds_rect"], [10, 20, 30, 40])

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_all_elements_hm_xpath_priority_all(self, mock_get_hm):
        """find_all_elements calls all() on xpath_obj and returns all results."""
        mock_hm = MagicMock()
        mock_xpath_obj = MagicMock()
        comp1 = {"id": "c1", "type": "Button", "bounds": [0, 0, 100, 100]}
        comp2 = {"id": "c2", "type": "Button", "bounds": [100, 100, 200, 200]}
        mock_xpath_obj.all.return_value = [comp1, comp2]
        mock_hm.xpath.return_value = mock_xpath_obj
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_all_elements({"xpath": "//Button"})

        mock_hm.xpath.assert_called_once_with("//Button")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], "c1")
        self.assertEqual(results[1]["id"], "c2")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_all_elements_hm_xpath_priority_find_components(self, mock_get_hm):
        """find_all_elements supports find_components() method on xpath_obj."""
        mock_hm = MagicMock()
        mock_xpath_obj = MagicMock(spec=["find_components"])
        comp = {"id": "c_only", "type": "Text", "bounds": [10, 10, 50, 50]}
        mock_xpath_obj.find_components.return_value = [comp]
        mock_hm.xpath.return_value = mock_xpath_obj
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_all_elements({"xpath": "//Text"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "c_only")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_all_elements_hm_xpath_priority_list_result(self, mock_get_hm):
        """find_all_elements supports xpath returning a list directly."""
        mock_hm = MagicMock()
        comp = {"id": "direct_list_item", "type": "Image", "bounds": [0, 0, 10, 10]}
        mock_hm.xpath.return_value = [comp]
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_all_elements({"xpath": "//Image"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "direct_list_item")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_elements_hm_xpath_find_component_returns_none(self, mock_get_hm):
        """When find_component returns None, find_elements returns empty list."""
        mock_hm = MagicMock()
        mock_xpath_obj = MagicMock()
        mock_xpath_obj.find_component.return_value = None
        mock_hm.xpath.return_value = mock_xpath_obj
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"xpath": "//Button[@text='NotExisting']"})

        self.assertEqual(results, [])


class TestHarmonyXPathFallback(unittest.TestCase):
    """Test fallback to ElementTree when hmdriver2.xpath is unavailable or fails."""

    def setUp(self):
        ui_snapshot_cache.invalidate()
        self.serial = "mock_serial_harmony"
        self.driver = HarmonyUIDriver(self.serial)
        self.driver.get_screen_size = MagicMock(return_value=(1080, 2340))

    def tearDown(self):
        ui_snapshot_cache.invalidate()

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_fallback_when_hm_has_no_xpath_method(self, mock_get_hm):
        """When hmdriver2 has no xpath method, fallback to ElementTree."""
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        self.assertFalse(hasattr(mock_hm, "xpath"))

        results = self.driver.find_elements({"xpath": "//*[@id='submit_btn']"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "submit_btn")
        self.assertEqual(results[0]["text"], "Login")
        self.assertEqual(results[0]["bounds_rect"], [100, 200, 400, 280])
        self.assertTrue(results[0]["visible"])

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_fallback_when_hm_xpath_raises_exception(self, mock_get_hm):
        """When hmdriver2.xpath() raises an exception, fallback to ElementTree."""
        mock_hm = MagicMock()
        mock_hm.xpath.side_effect = RuntimeError("uitest socket connection lost")
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"xpath": "//Button[@id='cancel_btn']"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "cancel_btn")
        self.assertEqual(results[0]["text"], "Cancel")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_fallback_when_xpath_obj_method_raises_exception(self, mock_get_hm):
        """When xpath_obj.find_component() raises, fallback to ElementTree."""
        mock_hm = MagicMock()
        mock_xpath_obj = MagicMock()
        mock_xpath_obj.find_component.side_effect = Exception("Malformed xpath in uitest daemon")
        mock_hm.xpath.return_value = mock_xpath_obj
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"xpath": "//*[@id='submit_btn']"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "submit_btn")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_fallback_when_hm_xpath_returns_none(self, mock_get_hm):
        """When hmdriver2.xpath() returns None, fallback to ElementTree."""
        mock_hm = MagicMock()
        mock_hm.xpath.return_value = None
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_elements({"xpath": "//*[@id='submit_btn']"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "submit_btn")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_fallback_find_all_elements_when_hm_xpath_unavailable(self, mock_get_hm):
        """find_all_elements falls back to ElementTree when hm has no xpath."""
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_all_elements({"xpath": "//Button"})

        self.assertEqual(len(results), 2)
        ids = [r["id"] for r in results]
        self.assertIn("submit_btn", ids)
        self.assertIn("cancel_btn", ids)

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_fallback_find_all_elements_when_hm_xpath_raises(self, mock_get_hm):
        """find_all_elements falls back to ElementTree when hm.xpath raises."""
        mock_hm = MagicMock()
        mock_hm.xpath.side_effect = RuntimeError("hmdriver socket error")
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        results = self.driver.find_all_elements({"xpath": "//ListItem"})

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["text"], "Item 1")
        self.assertEqual(results[1]["text"], "Item 2")


class TestHarmonyXPathSyntaxMatching(unittest.TestCase):
    """Test various XPath syntax matching scenarios against XML hierarchy fallback."""

    def setUp(self):
        ui_snapshot_cache.invalidate()
        self.serial = "mock_serial_harmony"
        self.driver = HarmonyUIDriver(self.serial)
        self.driver.get_screen_size = MagicMock(return_value=(1080, 2340))

    def tearDown(self):
        ui_snapshot_cache.invalidate()

    def _setup_mock_hierarchy(self, mock_get_hm):
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_match_by_tag(self, mock_get_hm):
        """Test matching by component type/tag: //Button, //Text, //RootView."""
        self._setup_mock_hierarchy(mock_get_hm)

        buttons = self.driver.find_all_elements({"xpath": "//Button"})
        self.assertEqual(len(buttons), 2)

        texts = self.driver.find_all_elements({"xpath": "//Text"})
        self.assertEqual(len(texts), 2)

        root = self.driver.find_elements({"xpath": "//RootView"})
        self.assertEqual(len(root), 1)
        self.assertEqual(root[0]["id"], "root_view")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_match_by_attribute_id_and_resource_id(self, mock_get_hm):
        """Test matching by @id, @resource_id, and @resource-id."""
        self._setup_mock_hierarchy(mock_get_hm)

        res1 = self.driver.find_elements({"xpath": "//*[@id='submit_btn']"})
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0]["text"], "Login")

        res2 = self.driver.find_elements({"xpath": "//*[@resource_id='cancel_btn']"})
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["text"], "Cancel")

        res3 = self.driver.find_elements({"xpath": "//*[@resource-id='title_txt']"})
        self.assertEqual(len(res3), 1)
        self.assertEqual(res3[0]["text"], "Welcome Back")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_match_by_text(self, mock_get_hm):
        """Test matching by text content: //Text[@text='Login'] vs //Button[@text='Login']."""
        self._setup_mock_hierarchy(mock_get_hm)

        # There are two nodes with text="Login": one Button and one Text
        btn = self.driver.find_elements({"xpath": "//Button[@text='Login']"})
        self.assertEqual(len(btn), 1)
        self.assertEqual(btn[0]["id"], "submit_btn")
        self.assertEqual(btn[0]["type"], "Button")

        txt = self.driver.find_elements({"xpath": "//Text[@text='Login']"})
        self.assertEqual(len(txt), 1)
        self.assertEqual(txt[0]["id"], "label_txt")
        self.assertEqual(txt[0]["type"], "Text")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_match_by_description_and_content_desc(self, mock_get_hm):
        """Test matching by @description and @content_desc / @content-desc."""
        self._setup_mock_hierarchy(mock_get_hm)

        r1 = self.driver.find_elements({"xpath": "//*[@description='Greeting Header']"})
        self.assertEqual(len(r1), 1)
        self.assertEqual(r1[0]["id"], "title_txt")

        r2 = self.driver.find_elements({"xpath": "//*[@content_desc='Greeting Header']"})
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0]["id"], "title_txt")

        r3 = self.driver.find_elements({"xpath": "//*[@content-desc='Login Button']"})
        self.assertEqual(len(r3), 1)
        self.assertEqual(r3[0]["id"], "submit_btn")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_match_by_boolean_attributes(self, mock_get_hm):
        """Test matching by clickable='true' and scrollable='true'."""
        self._setup_mock_hierarchy(mock_get_hm)

        clickables = self.driver.find_all_elements({"xpath": "//*[@clickable='true']"})
        # 2 Buttons + 2 ListItems = 4
        self.assertEqual(len(clickables), 4)

        scrollable_list = self.driver.find_elements({"xpath": "//List[@scrollable='true']"})
        self.assertEqual(len(scrollable_list), 1)
        self.assertEqual(scrollable_list[0]["id"], "item_list")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_match_hierarchical_path_and_indices(self, mock_get_hm):
        """Test parent/child paths and positional indices."""
        self._setup_mock_hierarchy(mock_get_hm)

        # Path under List
        items = self.driver.find_all_elements({"xpath": "//List/ListItem"})
        self.assertEqual(len(items), 2)

        # Indexing in ElementTree
        first_item = self.driver.find_elements({"xpath": "//List/ListItem[1]"})
        self.assertEqual(len(first_item), 1)
        self.assertEqual(first_item[0]["text"], "Item 1")

        second_item = self.driver.find_elements({"xpath": "//List/ListItem[2]"})
        self.assertEqual(len(second_item), 1)
        self.assertEqual(second_item[0]["text"], "Item 2")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_match_and_predicate(self, mock_get_hm):
        """Test predicate with 'and': //Button[@id='submit_btn' and @text='Login']."""
        self._setup_mock_hierarchy(mock_get_hm)

        res = self.driver.find_elements(
            {"xpath": "//Button[@id='submit_btn' and @text='Login']"}
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "submit_btn")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_non_matching_xpath_returns_empty(self, mock_get_hm):
        """Non-matching XPath returns empty list without error."""
        self._setup_mock_hierarchy(mock_get_hm)

        self.assertEqual(self.driver.find_elements({"xpath": "//NoSuchElement"}), [])
        self.assertEqual(self.driver.find_all_elements({"xpath": "//*[@id='not_found']"}), [])

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_invalid_xpath_syntax_handled_gracefully(self, mock_get_hm):
        """Malformed XPath expression safely returns empty list without crashing."""
        self._setup_mock_hierarchy(mock_get_hm)

        self.assertEqual(self.driver.find_elements({"xpath": "//["}), [])
        self.assertEqual(self.driver.find_all_elements({"xpath": "//Button["}), [])


class TestHarmonyXPathEnrichment(unittest.TestCase):
    """Test geometry and visibility enrichment on XPath results."""

    def setUp(self):
        ui_snapshot_cache.invalidate()
        self.serial = "mock_serial_harmony"
        self.driver = HarmonyUIDriver(self.serial)

    def tearDown(self):
        ui_snapshot_cache.invalidate()

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_matched_nodes_contain_complete_enrichment_fields(self, mock_get_hm):
        """Verify matched node contains all standard coordinates and visibility fields."""
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm
        self.driver.get_screen_size = MagicMock(return_value=(1080, 2340))

        results = self.driver.find_elements({"xpath": "//*[@id='submit_btn']"})
        self.assertEqual(len(results), 1)
        elem = results[0]

        # Geometry fields
        self.assertEqual(elem["bounds_rect"], [100, 200, 400, 280])
        self.assertEqual(elem["bounds"], {"left": 100, "top": 200, "right": 400, "bottom": 280})
        self.assertEqual(elem["boundsCenter"], {"x": 250, "y": 240})
        self.assertEqual(elem["center_x"], 250)
        self.assertEqual(elem["center_y"], 240)

        # Visibility fields
        self.assertIn("visible", elem)
        self.assertIn("visible_ratio", elem)
        self.assertTrue(elem["visible"])
        self.assertEqual(elem["visible_ratio"], 1.0)

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_offscreen_node_visibility_calculation(self, mock_get_hm):
        """Verify off-screen elements calculate visible=False and visible_ratio=0.0."""
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm
        self.driver.get_screen_size = MagicMock(return_value=(1080, 2340))

        results = self.driver.find_elements({"xpath": "//Image[@id='offscreen_img']"})
        self.assertEqual(len(results), 1)
        elem = results[0]

        self.assertEqual(elem["bounds_rect"], [0, 3000, 100, 3100])
        self.assertFalse(elem["visible"])
        self.assertEqual(elem["visible_ratio"], 0.0)

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_screen_size_unavailable_enrichment(self, mock_get_hm):
        """When get_screen_size fails, nodes still return with visible_reason='screen_size_unavailable'."""
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm
        self.driver.get_screen_size = MagicMock(side_effect=RuntimeError("Screen size unavailable"))

        results = self.driver.find_elements({"xpath": "//*[@id='submit_btn']"})
        self.assertEqual(len(results), 1)
        elem = results[0]

        self.assertEqual(elem.get("visible_reason"), "screen_size_unavailable")
        self.assertFalse(elem["visible"])
        self.assertIsNone(elem["visible_ratio"])


class TestHarmonyXPathSingleVsMultiple(unittest.TestCase):
    """Test behavioral differences between find_elements and find_all_elements with XPath."""

    def setUp(self):
        ui_snapshot_cache.invalidate()
        self.serial = "mock_serial_harmony"
        self.driver = HarmonyUIDriver(self.serial)
        self.driver.get_screen_size = MagicMock(return_value=(1080, 2340))

    def tearDown(self):
        ui_snapshot_cache.invalidate()

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_elements_returns_at_most_one(self, mock_get_hm):
        """find_elements with multiple matches returns only the first match."""
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        # Multiple Buttons exist in the hierarchy
        res = self.driver.find_elements({"xpath": "//Button"})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "submit_btn")

    @patch("phone_pilot.harmony.driver._get_hm")
    def test_find_all_elements_returns_all_matches(self, mock_get_hm):
        """find_all_elements with multiple matches returns all matching elements."""
        mock_hm = MagicMock(spec=["dump_hierarchy"])
        mock_hm.dump_hierarchy.return_value = SAMPLE_RICH_HIERARCHY
        mock_get_hm.return_value = mock_hm

        res = self.driver.find_all_elements({"xpath": "//Button"})
        self.assertEqual(len(res), 2)
        self.assertEqual([r["id"] for r in res], ["submit_btn", "cancel_btn"])

    def test_empty_or_invalid_selectors(self):
        """find_elements and find_all_elements handle empty or invalid selectors."""
        self.assertEqual(self.driver.find_elements({}), [])
        self.assertEqual(self.driver.find_elements({"xpath": ""}), [])
        self.assertEqual(self.driver.find_elements({"xpath": None}), [])
        self.assertEqual(self.driver.find_all_elements({}), [])
        self.assertEqual(self.driver.find_all_elements({"xpath": ""}), [])
        self.assertEqual(self.driver.find_all_elements({"xpath": None}), [])


if __name__ == "__main__":
    unittest.main()
