"""
Tests for HarmonyOS UI hierarchy parsing and node finding.
"""

import unittest

from phone_pilot.harmony.ui.automator import (
    parse_hierarchy_nodes,
    find_nodes,
    pick_scrollable_bounds,
    ui_signature,
    collect_node_texts,
)


SAMPLE_HIERARCHY = {
    "attributes": {
        "text": "",
        "type": "RootView",
        "bounds": {"left": 0, "top": 0, "right": 1080, "bottom": 2340},
    },
    "children": [
        {
            "attributes": {
                "text": "抖音",
                "type": "Text",
                "description": "Douyin App",
                "id": "app_icon_douyin",
                "bounds": {"left": 100, "top": 200, "right": 200, "bottom": 250},
                "boundsCenter": {"x": 150, "y": 225},
                "clickable": True,
            },
            "children": [],
        },
        {
            "attributes": {
                "text": "微信",
                "type": "Text",
                "description": "WeChat",
                "id": "app_icon_wechat",
                "bounds": {"left": 300, "top": 200, "right": 400, "bottom": 250},
                "boundsCenter": {"x": 350, "y": 225},
                "clickable": True,
            },
            "children": [],
        },
        {
            "attributes": {
                "text": "",
                "type": "List",
                "scrollable": True,
                "bounds": {"left": 0, "top": 300, "right": 1080, "bottom": 2000},
            },
            "children": [
                {
                    "attributes": {
                        "text": "Item 1",
                        "type": "ListItem",
                        "bounds": {"left": 0, "top": 300, "right": 1080, "bottom": 500},
                    },
                    "children": [],
                },
                {
                    "attributes": {
                        "text": "Item 2",
                        "type": "ListItem",
                        "bounds": {"left": 0, "top": 500, "right": 1080, "bottom": 700},
                    },
                    "children": [],
                },
            ],
        },
    ],
}


class TestParseHierarchy(unittest.TestCase):

    def test_parse_returns_nodes(self):
        nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)
        self.assertGreater(len(nodes), 0)
        # Should include 抖音, 微信, List, Item 1, Item 2
        texts = [n.text for n in nodes if n.text]
        self.assertIn("抖音", texts)
        self.assertIn("微信", texts)
        self.assertIn("Item 1", texts)

    def test_node_center(self):
        nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)
        douyin = [n for n in nodes if n.text == "抖音"][0]
        cx, cy = douyin.center()
        self.assertEqual(cx, 150)
        self.assertEqual(cy, 225)

    def test_node_bounds_tuple(self):
        nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)
        douyin = [n for n in nodes if n.text == "抖音"][0]
        left, t, r, b = douyin.bounds_tuple()
        self.assertEqual(left, 100)
        self.assertEqual(t, 200)
        self.assertEqual(r, 200)
        self.assertEqual(b, 250)

    def test_node_to_dict(self):
        nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)
        douyin = [n for n in nodes if n.text == "抖音"][0]
        d = douyin.to_dict()
        self.assertEqual(d["text"], "抖音")
        self.assertEqual(d["center_x"], 150)
        self.assertEqual(d["center_y"], 225)
        self.assertTrue(d["clickable"])


class TestFindNodes(unittest.TestCase):

    def setUp(self):
        self.nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)

    def test_find_by_text(self):
        results = find_nodes(self.nodes, "抖音", field="text")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].text, "抖音")

    def test_find_by_text_substring(self):
        results = find_nodes(self.nodes, "Item", field="text")
        self.assertEqual(len(results), 2)

    def test_find_exact(self):
        results = find_nodes(self.nodes, "Item", field="text", exact=True)
        self.assertEqual(len(results), 0)

    def test_find_by_description(self):
        results = find_nodes(self.nodes, "Douyin", field="description")
        self.assertEqual(len(results), 1)

    def test_find_by_id(self):
        results = find_nodes(self.nodes, "app_icon_wechat", field="id")
        self.assertEqual(len(results), 1)

    def test_find_any_field(self):
        results = find_nodes(self.nodes, "WeChat", field="any")
        self.assertEqual(len(results), 1)

    def test_find_with_limit(self):
        results = find_nodes(self.nodes, "Item", field="text", limit=1)
        self.assertEqual(len(results), 1)

    def test_find_case_insensitive(self):
        results = find_nodes(self.nodes, "douyin", field="description", case_sensitive=False)
        self.assertEqual(len(results), 1)


class TestScrollable(unittest.TestCase):

    def test_pick_scrollable_bounds(self):
        nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)
        bounds = pick_scrollable_bounds(nodes)
        self.assertIsNotNone(bounds)
        left, t, r, b = bounds
        self.assertEqual(left, 0)
        self.assertEqual(r, 1080)


class TestUISignature(unittest.TestCase):

    def test_signature_stable(self):
        nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)
        sig1 = ui_signature(nodes)
        sig2 = ui_signature(nodes)
        self.assertEqual(sig1, sig2)
        self.assertIn("Text:", sig1)


class TestCollectNodeTexts(unittest.TestCase):

    def test_collect(self):
        nodes = parse_hierarchy_nodes(SAMPLE_HIERARCHY)
        texts = collect_node_texts(nodes)
        self.assertIn("抖音", texts)
        self.assertIn("微信", texts)
        # Descriptions should also be collected if different from text
        self.assertIn("Douyin App", texts)


if __name__ == "__main__":
    unittest.main()
