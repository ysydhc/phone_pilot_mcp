"""Tests for MCP action tool UI cache invalidation.

验证 MCP 动作类工具成功执行后失效 ui_snapshot_cache。
通过直接测试 _safe_invalidate_ui 逻辑和 hook 包装行为来验证。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from phone_pilot.core import ui_snapshot_cache
from phone_pilot.mcp._cache_hook import (
    _ACTION_INVALIDATES_UI,
    _safe_invalidate_ui,
    install_cache_invalidation_hook,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    ui_snapshot_cache.invalidate()
    yield
    ui_snapshot_cache.invalidate()


# ---------------------------------------------------------------------------
# _safe_invalidate_ui 逻辑测试
# ---------------------------------------------------------------------------


def test_action_tool_ok_invalidates():
    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        _safe_invalidate_ui(
            "phone_tap",
            {"device_serial": "serial1", "platform": "android"},
            {"ok": True, "device_serial": "serial1", "platform": "android"},
        )
    mock_inv.assert_called_once_with("serial1", platform="android")


def test_action_tool_failed_does_not_invalidate():
    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        _safe_invalidate_ui(
            "phone_tap",
            {"device_serial": "serial1"},
            {"ok": False, "error": "device_offline"},
        )
    mock_inv.assert_not_called()


def test_non_action_tool_does_not_invalidate():
    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        _safe_invalidate_ui(
            "phone_get_page_state",
            {"device_serial": "serial1"},
            {"ok": True, "device_serial": "serial1"},
        )
    mock_inv.assert_not_called()


def test_invalidate_uses_result_serial_when_kwargs_missing():
    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        _safe_invalidate_ui(
            "phone_tap",
            {},
            {"ok": True, "device_serial": "from_result", "platform": "android"},
        )
    mock_inv.assert_called_once_with("from_result", platform="android")


def test_invalidate_skips_when_no_serial():
    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        _safe_invalidate_ui("phone_tap", {}, {"ok": True})
    mock_inv.assert_not_called()


def test_tap_element_is_action():
    assert "phone_tap_element" in _ACTION_INVALIDATES_UI


def test_get_page_state_is_not_action():
    assert "phone_get_page_state" not in _ACTION_INVALIDATES_UI


# ---------------------------------------------------------------------------
# Hook 包装行为测试
# ---------------------------------------------------------------------------


def test_hook_wraps_action_tool_and_invalidates():
    """验证 hook 能包装异步 action 工具并在成功后失效缓存。"""
    fake_mcp = MagicMock()

    # 模拟 mcp.tool() 返回一个 decorator
    def fake_tool(*args, **kwargs):
        def decorator(fn):
            return fn  # 直接返回函数，不真正注册

        return decorator

    fake_mcp.tool = fake_tool

    install_cache_invalidation_hook(fake_mcp)

    # 现在用 patched tool 注册一个函数
    @fake_mcp.tool()
    async def phone_tap(device_serial: str = "", platform: str = "android"):
        return {"ok": True, "device_serial": device_serial, "platform": platform}

    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        result = asyncio.run(phone_tap(device_serial="hook_serial", platform="android"))

    assert result["ok"] is True
    mock_inv.assert_called_once_with("hook_serial", platform="android")


def test_hook_does_not_invalidate_for_query_tool():
    """验证 query 类工具不被失效缓存。"""
    fake_mcp = MagicMock()
    fake_mcp.tool = lambda *a, **kw: lambda fn: fn

    install_cache_invalidation_hook(fake_mcp)

    @fake_mcp.tool()
    async def phone_get_page_state(device_serial: str = ""):
        return {"ok": True, "device_serial": device_serial}

    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        asyncio.run(phone_get_page_state(device_serial="q_serial"))

    mock_inv.assert_not_called()


def test_hook_does_not_wrap_non_action_tool():
    """非 action 工具注册时应跳过 wrapper（性能优化）。"""
    fake_mcp = MagicMock()
    # Track whether decorator was called with the original fn or a wrapper.
    original_fn = None

    def fake_tool(*args, **kwargs):
        def decorator(fn):
            nonlocal original_fn
            original_fn = fn  # capture what was actually registered
            return fn

        return decorator

    fake_mcp.tool = fake_tool
    install_cache_invalidation_hook(fake_mcp)

    async def phone_screenshot(device_serial: str = ""):
        return {"ok": True, "device_serial": device_serial}

    # phone_screenshot is NOT in _ACTION_INVALIDATES_UI
    registered = fake_mcp.tool()(phone_screenshot)
    # The registered function should be the original, not a wrapper.
    assert registered is phone_screenshot


def test_action_tool_skips_invalidation_when_page_state_updated():
    """When result has page_state_updated=True, _safe_invalidate_ui must not wipe the new snapshot."""
    with patch.object(ui_snapshot_cache, "invalidate_device_ui") as mock_inv:
        _safe_invalidate_ui(
            "phone_tap_element",
            {"device_serial": "serial1", "platform": "android"},
            {
                "ok": True,
                "device_serial": "serial1",
                "platform": "android",
                "page_state_updated": True,
            },
        )
    mock_inv.assert_not_called()


def test_action_invalidates_page_elements_snapshot():
    """B2: An intervening UI action invalidates the 30s page_elements snapshot."""
    from phone_pilot.core.ui_node import UINode
    from phone_pilot.mcp._page_state_tools import (
        phone_get_page_state,
        phone_tap_element,
    )

    driver = MagicMock()
    driver.ui.get_current_activity.return_value = {"package": "pkg", "activity": ".Main"}
    driver.screen.get_screen_size.return_value = (1080, 1920)
    driver.ui.dump_ui_nodes.return_value = [
        UINode(
            text="Btn",
            bounds="[10,20][110,80]",
            clickable=True,
            class_name="android.widget.Button",
        )
    ]
    driver.input.tap.return_value = {"ok": True}

    with (
        patch(
            "phone_pilot.mcp._page_state_tools._resolve_device_serial",
            return_value=("dev1", "android", None),
        ),
        patch("phone_pilot.mcp._page_state_tools.get_driver", return_value=driver),
    ):
        asyncio.run(phone_get_page_state(device_serial="dev1", platform="android"))
        assert driver.ui.dump_ui_nodes.call_count == 1

        # Simulate intervening action invalidating UI cache
        _safe_invalidate_ui(
            "phone_swipe",
            {"device_serial": "dev1", "platform": "android"},
            {"ok": True, "device_serial": "dev1", "platform": "android"},
        )

        tap_res = asyncio.run(
            phone_tap_element(
                index=0, device_serial="dev1", platform="android", wait_ms=0
            )
        )
        assert tap_res["ok"] is True
        assert tap_res["snapshot_hit"] is False
        assert driver.ui.dump_ui_nodes.call_count == 2


def test_mixed_ttl_cache_eviction():
    """B6: Short-TTL (0.5s) entries expire at t+1.0s while 30s page_elements snapshot remains alive."""
    base_t = 1000.0
    with patch("phone_pilot.core.ui_snapshot_cache.time.monotonic", return_value=base_t):
        ui_snapshot_cache.put_snapshot(
            ("android", "dev1", "ui_nodes"), ["raw_node"], ttl_s=0.5
        )
        ui_snapshot_cache.put_snapshot(
            ("android", "dev1", "page_elements", False),
            ([{"index": 0}], [], ["Btn"]),
            ttl_s=30.0,
        )

    # At t + 1.0s: 0.5s entry is expired, 30.0s entry is still valid
    with patch(
        "phone_pilot.core.ui_snapshot_cache.time.monotonic", return_value=base_t + 1.0
    ):
        ui_snapshot_cache._evict_expired_or_oldest()
        assert ui_snapshot_cache.get_snapshot(("android", "dev1", "ui_nodes")) is None
        assert (
            ui_snapshot_cache.get_snapshot(
                ("android", "dev1", "page_elements", False)
            )
            is not None
        )
