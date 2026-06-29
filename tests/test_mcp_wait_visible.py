"""Tests for phone_wait_for_element require_visible passthrough.

验证 MCP phone_wait_for_element 的 require_visible 参数能透传到内部逻辑，
正确过滤掉零尺寸 bounds 的元素。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from phone_pilot.mcp._script_tools import _phone_wait_for_element_sync


def _make_node(
    text="Login",
    bounds=(100, 200, 300, 400),
    clickable=True,
    enabled=True,
):
    node = MagicMock()
    node.text = text
    node.content_desc = ""
    node.resource_id = ""
    node.clickable = clickable
    node.enabled = enabled
    node.bounds_tuple.return_value = bounds
    node.center.return_value = (
        (bounds[0] + bounds[2]) // 2,
        (bounds[1] + bounds[3]) // 2,
    )
    return node


@pytest.fixture(autouse=True)
def _fast_polling():
    """让测试不真的 sleep，但 time.monotonic 正常工作。"""
    with patch("phone_pilot.mcp._script_tools.time.sleep"):
        yield


def _patch_resolve(serial="test_v_serial", plat="android"):
    return patch(
        "phone_pilot.mcp._script_tools._resolve_device_serial",
        return_value=(serial, plat, None),
    )


def _patch_driver(nodes, plat="android"):
    driver = MagicMock()
    driver.platform = plat
    driver.ui.dump_ui_nodes.return_value = nodes
    return patch(
        "phone_pilot.mcp._script_tools.get_driver",
        return_value=driver,
    )


def test_require_visible_satisfied():
    nodes = [_make_node(bounds=(100, 200, 300, 400))]
    with _patch_resolve(), _patch_driver(nodes):
        result = _phone_wait_for_element_sync(
            device_serial=None,
            text="Login",
            resource_id="",
            timeout_s=1.0,
            platform="auto",
            require_visible=True,
        )

    assert result["ok"] is True
    assert result["found"] is True


def test_require_visible_zero_size_not_satisfied():
    nodes = [_make_node(bounds=(100, 200, 100, 200))]  # zero size
    with _patch_resolve(), _patch_driver(nodes):
        result = _phone_wait_for_element_sync(
            device_serial=None,
            text="Login",
            resource_id="",
            timeout_s=0.3,
            platform="auto",
            require_visible=True,
        )

    assert result["ok"] is True
    assert result["found"] is False
    assert result["present_but_not_ready"] is True


def test_require_visible_default_false_allows_zero_size():
    """require_visible 默认 False，零尺寸 bounds 也算找到。"""
    nodes = [_make_node(bounds=(100, 200, 100, 200))]
    with _patch_resolve(), _patch_driver(nodes):
        result = _phone_wait_for_element_sync(
            device_serial=None,
            text="Login",
            resource_id="",
            timeout_s=1.0,
            platform="auto",
        )

    assert result["ok"] is True
    assert result["found"] is True


def test_require_visible_with_clickable_and_enabled():
    nodes = [
        _make_node(
            bounds=(100, 200, 300, 400), clickable=True, enabled=True
        )
    ]
    with _patch_resolve(), _patch_driver(nodes):
        result = _phone_wait_for_element_sync(
            device_serial=None,
            text="Login",
            resource_id="",
            timeout_s=1.0,
            platform="auto",
            require_visible=True,
            require_clickable=True,
            require_enabled=True,
        )

    assert result["found"] is True
