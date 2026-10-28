"""
Tests for iOS coordinate scale handling (Points vs Pixels).

Verifies that:
1. automator transforms WDA Points to physical Pixels according to scale.
2. iOSInputDriver scales physical pixel inputs down to WDA Points.
3. iOSUIDriver passes scale down to automator so bounds and center match pixels.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from phone_pilot.ios.driver import iOSDriver, iOSInputDriver, iOSUIDriver
from phone_pilot.ios.ui import automator


_SAMPLE_WDA_SOURCE = json.dumps(
    {
        "type": "Application",
        "name": "TestApp",
        "label": "TestApp",
        "rect": {"x": 0, "y": 0, "width": 393, "height": 852},
        "children": [
            {
                "type": "XCUIElementTypeButton",
                "name": "login_btn",
                "label": "Login",
                "value": "",
                "isEnabled": True,
                "rect": {"x": 50, "y": 100, "width": 200, "height": 40},
                "children": [],
            }
        ],
    }
)


def test_automator_scale_transformation():
    """automator converts WDA rect points to physical pixels when scale > 1."""
    # scale = 3.0 (e.g. iPhone 14 Pro, 393x852 pt -> 1179x2556 px)
    nodes = automator.wda_source_to_ui_nodes(_SAMPLE_WDA_SOURCE, scale=3.0)
    assert len(nodes) == 1
    btn = nodes[0]
    assert btn.text == "Login"
    assert btn.resource_id == "login_btn"
    # Points was x:50..250, y:100..140 -> Pixels should be x:150..750, y:300..420
    assert btn.bounds == "[150,300][750,420]"
    assert btn.center() == (450, 360)

    # find_in_source also uses scale
    found = automator.find_in_source(
        _SAMPLE_WDA_SOURCE, {"text": "Login"}, scale=3.0
    )
    assert len(found) == 1
    assert found[0]["center_x"] == 450
    assert found[0]["center_y"] == 360
    assert found[0]["bounds"] == "[150,300][750,420]"


def test_automator_default_scale_backward_compat():
    """automator with default scale=1.0 preserves points 1:1."""
    nodes = automator.wda_source_to_ui_nodes(_SAMPLE_WDA_SOURCE)
    assert len(nodes) == 1
    btn = nodes[0]
    assert btn.bounds == "[50,100][250,140]"
    assert btn.center() == (150, 120)


def test_input_driver_scale_conversion(monkeypatch):
    """iOSInputDriver converts incoming physical pixels to points for WDA."""
    mock_client = MagicMock()
    mock_client.scale = 3.0

    from phone_pilot.ios import wda_bridge

    monkeypatch.setattr(wda_bridge, "get_wda", lambda serial: mock_client)

    input_drv = iOSInputDriver("MOCK-SERIAL")

    # 1. Tap (physical pixels: 450, 360 -> WDA points: 150, 120)
    res_tap = input_drv.tap(450, 360)
    assert res_tap["ok"] is True
    assert res_tap["x"] == 450
    assert res_tap["y"] == 360
    mock_client.tap.assert_called_once_with(150, 120)

    # 2. Swipe (physical pixels: 300, 600, 300, 300 -> WDA points: 100, 200, 100, 100)
    res_swipe = input_drv.swipe(300, 600, 300, 300, duration_ms=200)
    assert res_swipe["ok"] is True
    mock_client.swipe.assert_called_once_with(100, 200, 100, 100, 0.2)

    # 3. Long press (physical pixels: 450, 360 -> WDA points: 150, 120)
    res_lp = input_drv.long_press(450, 360, duration_ms=600)
    assert res_lp["ok"] is True
    mock_client.tap_hold.assert_called_once_with(150, 120, 0.6)


def test_ui_driver_passes_scale(monkeypatch):
    """iOSUIDriver passes resolved scale down to automator methods."""
    mock_client = MagicMock()
    mock_client.scale = 2.0
    mock_client.source.return_value = _SAMPLE_WDA_SOURCE

    from phone_pilot.ios import wda_bridge

    monkeypatch.setattr(wda_bridge, "get_wda", lambda serial: mock_client)

    ui_drv = iOSUIDriver("MOCK-SERIAL")
    nodes = ui_drv.dump_ui_nodes()
    assert len(nodes) == 1
    # 50*2=100, 100*2=200, 250*2=500, 140*2=280
    assert nodes[0].bounds == "[100,200][500,280]"
    assert nodes[0].center() == (300, 240)

    elems = ui_drv.find_elements({"text": "Login"})
    assert len(elems) == 1
    assert elems[0]["center_x"] == 300
    assert elems[0]["center_y"] == 240


def test_driver_composite_scale_consistency(monkeypatch):
    """iOSDriver exposes scale property and sub-drivers coordinate cleanly."""
    mock_client = MagicMock()
    mock_client.scale = 3.0
    mock_client.source.return_value = _SAMPLE_WDA_SOURCE

    from phone_pilot.ios import wda_bridge

    monkeypatch.setattr(wda_bridge, "get_wda", lambda serial: mock_client)

    driver = iOSDriver("MOCK-SERIAL")
    # Finding element through UI driver gives pixel coords
    elems = driver.ui.find_elements({"text": "Login"})
    assert len(elems) == 1
    target_x = elems[0]["center_x"]  # 450
    target_y = elems[0]["center_y"]  # 360

    # Tapping that element using input driver transparently converts to 150, 120
    driver.input.tap(target_x, target_y)
    mock_client.tap.assert_called_once_with(150, 120)
