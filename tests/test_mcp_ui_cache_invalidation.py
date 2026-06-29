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
    fake_mcp.tool = lambda *a, **kw: (lambda fn: fn)

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
