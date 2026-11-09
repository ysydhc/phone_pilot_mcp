"""
Tests for iOSUIDriver + ios/ui/automator.py (design §4.3).
WDA source JSON is a fixture; no real device.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from phone_pilot.core import ui_snapshot_cache
from phone_pilot.ios.driver import iOSDriver, iOSUIDriver
from phone_pilot.ios.ui import automator


SERIAL = "00008030-001234560A80802E"


@pytest.fixture(autouse=True)
def _clear_ui_cache():
    ui_snapshot_cache.invalidate()
    yield
    ui_snapshot_cache.invalidate()

# Representative WDA /source?format=json tree.
WDA_SOURCE = {
    "type": "Application",
    "name": "com.example.app",
    "label": "Example",
    "rect": {"x": 0, "y": 0, "width": 390, "height": 844},
    "isEnabled": True,
    "children": [
        {
            "type": "XCUIElementTypeButton",
            "name": "login_btn",
            "label": "Login",
            "value": "",
            "rect": {"x": 100, "y": 200, "width": 120, "height": 40},
            "isEnabled": True,
            "children": [],
        },
        {
            "type": "XCUIElementTypeStaticText",
            "name": "",
            "label": "Welcome",
            "value": "Welcome",
            "rect": {"x": 20, "y": 80, "width": 200, "height": 30},
            "isEnabled": True,
            "children": [],
        },
    ],
}


def _patch_bridge(client):
    return patch(
        "phone_pilot.ios.driver._wda_call",
        side_effect=lambda serial, fn: fn(client),
    )


# ---------------------------------------------------------------------------
# automator pure functions
# ---------------------------------------------------------------------------


class TestAutomator:
    def test_parse_flattens_tree(self):
        nodes = automator.parse_wda_source(WDA_SOURCE)
        types = [n["type"] for n in nodes]
        assert "XCUIElementTypeButton" in types
        assert "XCUIElementTypeStaticText" in types

    def test_each_element_has_center(self):
        nodes = automator.parse_wda_source(WDA_SOURCE)
        for n in nodes:
            assert "center_x" in n and "center_y" in n
        btn = next(n for n in nodes if n.get("name") == "login_btn")
        assert btn["center_x"] == 160  # 100 + 120/2
        assert btn["center_y"] == 220  # 200 + 40/2

    def test_accepts_json_string(self):
        nodes = automator.parse_wda_source(json.dumps(WDA_SOURCE))
        assert len(nodes) >= 2

    def test_to_universal_uinode_mapping(self):
        nodes = automator.parse_wda_source(WDA_SOURCE)
        btn_raw = next(n for n in nodes if n.get("name") == "login_btn")
        node = automator.wda_node_to_universal(btn_raw)
        assert node.resource_id == "login_btn"  # WDA name → resource_id
        assert node.class_name == "XCUIElementTypeButton"
        assert node.text == "Login"  # label → text
        assert node.center() == (160, 220)

    def test_collect_texts(self):
        texts = automator.collect_texts(WDA_SOURCE)
        assert "Login" in texts
        assert "Welcome" in texts

    def test_match_selector_class_name_short_and_full(self):
        full_elem = {"type": "XCUIElementTypeButton", "label": "Login"}
        short_elem = {"type": "Button", "label": "Login"}

        assert automator.match_selector(full_elem, {"class_name": "XCUIElementTypeButton"})
        assert automator.match_selector(full_elem, {"class_name": "Button"})
        assert automator.match_selector(short_elem, {"class_name": "XCUIElementTypeButton"})
        assert automator.match_selector(short_elem, {"class_name": "Button"})
        assert not automator.match_selector(full_elem, {"class_name": "StaticText"})
        assert not automator.match_selector(short_elem, {"class_name": "XCUIElementTypeStaticText"})

    def test_selector_to_wda_predicate_class_name_variants(self):
        pred_short = automator.selector_to_wda_predicate({"class_name": "Button", "text": "Login"})
        pred_full = automator.selector_to_wda_predicate(
            {"class_name": "XCUIElementTypeButton", "text": "Login"}
        )
        assert pred_short == pred_full
        assert 'type == "XCUIElementTypeButton"' in pred_short
        assert 'type == "Button"' in pred_short
        assert 'label == "Login"' in pred_short


# ---------------------------------------------------------------------------
# iOSUIDriver
# ---------------------------------------------------------------------------


class TestUIDriver:
    def test_dump_ui_hierarchy_returns_string(self):
        client = MagicMock()
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            s = iOSUIDriver(SERIAL).dump_ui_hierarchy()
        assert isinstance(s, str)
        assert "XCUIElementTypeButton" in s

    def test_dump_ui_nodes_returns_universal(self):
        client = MagicMock()
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            nodes = iOSUIDriver(SERIAL).dump_ui_nodes()
        assert len(nodes) >= 2
        assert any(n.resource_id == "login_btn" for n in nodes)

    def test_collect_all_texts(self):
        client = MagicMock()
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            texts = iOSUIDriver(SERIAL).collect_all_texts()
        assert "Login" in texts and "Welcome" in texts

    def test_find_elements_by_text_has_center(self):
        client = MagicMock()
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            results = iOSUIDriver(SERIAL).find_elements({"text": "Login"})
        assert len(results) == 1
        assert results[0]["center_x"] == 160 and results[0]["center_y"] == 220
        assert "visible" in results[0]
        assert "visible_ratio" in results[0]
        assert "bounds_rect" in results[0]
        assert results[0]["bounds_rect"] == [100, 200, 220, 240]

    def test_find_elements_by_resource_id(self):
        client = MagicMock()
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            results = iOSUIDriver(SERIAL).find_elements({"resource_id": "login_btn"})
        assert len(results) == 1
        assert results[0]["name"] == "login_btn"

    def test_find_elements_text_contains(self):
        client = MagicMock()
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            results = iOSUIDriver(SERIAL).find_elements({"text_contains": "Wel"})
        assert len(results) == 1
        assert results[0]["label"] == "Welcome"

    def test_find_elements_no_match_returns_empty(self):
        client = MagicMock()
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            results = iOSUIDriver(SERIAL).find_elements({"text": "Nope"})
        assert results == []

    def test_get_current_activity_maps_bundleid(self):
        client = MagicMock()
        client.app_current.return_value = {"bundleId": "com.example.app"}
        with _patch_bridge(client):
            res = iOSUIDriver(SERIAL).get_current_activity()
        assert res["package"] == "com.example.app"
        assert res["activity"] == ""

    def test_ios_ui_snapshot_cache_hit_and_invalidation(self):
        client = MagicMock()
        client.scale = 1.0
        client.source.return_value = json.dumps(WDA_SOURCE)
        driver = iOSDriver(SERIAL)
        with _patch_bridge(client):
            nodes = driver.ui.dump_ui_nodes()
            elems = driver.ui.find_elements({"text": "Login"})
            assert len(nodes) >= 2
            assert len(elems) == 1
            assert client.source.call_count == 1

            tap_res = driver.input.tap(160, 220)
            assert tap_res["ok"] is True

            elems_after = driver.ui.find_elements({"text": "Login"})
            assert len(elems_after) == 1
            assert client.source.call_count == 2

    def test_ios_ui_empty_source_not_cached(self):
        client = MagicMock()
        client.scale = 1.0
        client.source.side_effect = ["", json.dumps(WDA_SOURCE)]
        driver = iOSDriver(SERIAL)
        with _patch_bridge(client):
            first = driver.ui.dump_ui_hierarchy()
            assert first == ""
            second = driver.ui.find_elements({"text": "Login"})
            assert len(second) == 1
            assert client.source.call_count == 2

    def test_ui_driver_get_screen_size_no_import_error(self):
        client = MagicMock()
        client.window_size.return_value = (390, 844)
        client.scale = 3.0
        ui_drv = iOSUIDriver(SERIAL)
        with _patch_bridge(client):
            size1 = ui_drv.get_screen_size()
            size2 = ui_drv.get_screen_size()
        assert size1 == (1170, 2532)
        assert size2 == (1170, 2532)
        assert client.window_size.call_count == 1

    def test_find_elements_enriches_visibility_with_screen_size(self):
        client = MagicMock()
        client.scale = 1.0
        client.window_size.return_value = (390, 844)
        client.source.return_value = json.dumps(WDA_SOURCE)
        with _patch_bridge(client):
            results = iOSUIDriver(SERIAL).find_elements({"class_name": "Button"})
        assert len(results) == 1
        assert results[0]["name"] == "login_btn"
        assert results[0]["visible"] is True
        assert results[0]["visible_ratio"] == 1.0
