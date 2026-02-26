import unittest

from phone_pilot.android.ui.automator import UINode, find_nodes


class TestFindNodesFuzzy(unittest.TestCase):
    def test_fuzzy_short_query(self) -> None:
        nodes = [
            UINode(
                package="pkg",
                text="个人中心",
                content_desc=None,
                hint=None,
                resource_id=None,
                class_name="android.widget.TextView",
                bounds="[0,0][10,10]",
                clickable=True,
                enabled=True,
            )
        ]
        hits = find_nodes(nodes, query="个人资料", exact=False, limit=1)
        self.assertTrue(hits)
