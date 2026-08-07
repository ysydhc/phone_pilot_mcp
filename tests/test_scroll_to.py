"""script_api.scroll_to() 单测（mock driver，不依赖真机）。

注意：与设计文档里的伪代码测试相比，这里改为在 ``ctx.driver.ui.dump_ui_nodes``
层 mock scrollable 容器节点（返回真实的 ``UINode`` 对象），而不是 patch
``phone_pilot.android.ui.automator.pick_scrollable_bounds`` —— 因为
``scroll_to()`` 的实现里"寻找包含目标元素的 scrollable 容器"这一步是自己遍历
``dump_ui_nodes()`` 结果做 bounds 包含判断，并不复用只会挑"面积最大"节点、
不做包含判断的 ``pick_scrollable_bounds``。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from phone_pilot.core.ui_node import UINode
from phone_pilot.script_api import scroll_to
from phone_pilot.script_api.context import ScriptContext


@pytest.fixture
def ctx():
    context = MagicMock(spec=ScriptContext)
    context.device_serial = "serial1"
    context.platform = "android"
    # 显式置空，避免 MagicMock(spec=...) 对 dataclass 默认值字段生成的
    # 自动子 Mock 被当成"已缓存的屏幕尺寸"（真值判断会误判为已缓存）。
    context.screen_size = None
    context.driver = MagicMock()
    context.driver.screen.get_screen_size.return_value = (1080, 1920)
    context.driver.input.swipe.return_value = {"ok": True}
    context.driver.ui.dump_ui_nodes.return_value = []
    return context


def _elem(bounds_rect, **extra) -> dict:
    x1, y1, x2, y2 = bounds_rect
    d = {
        "bounds_rect": list(bounds_rect),
        "bounds": f"[{x1},{y1}][{x2},{y2}]",
        "center_x": (x1 + x2) // 2,
        "center_y": (y1 + y2) // 2,
        "scrollable": False,
    }
    d.update(extra)
    return d


def _scrollable_node(bounds_rect) -> UINode:
    x1, y1, x2, y2 = bounds_rect
    return UINode(bounds=f"[{x1},{y1}][{x2},{y2}]", scrollable=True)


def test_already_visible_no_scroll(ctx):
    """元素已完全可见，force_scroll=False（默认）：不执行任何 swipe。"""
    ctx.driver.ui.find_elements.return_value = [_elem([100, 100, 300, 200])]
    result = scroll_to(ctx, {"text": "Login"})
    assert result["ok"] is True
    assert result["attempts"] == 0
    ctx.driver.input.swipe.assert_not_called()


def test_offscreen_element_scrolls_into_view(ctx):
    """元素在屏幕外，滑动一次后第二次查询返回已可见坐标 → 成功。"""
    offscreen = _elem([440, 1939, 640, 2039])
    onscreen = _elem([440, 900, 640, 1000])
    ctx.driver.ui.find_elements.side_effect = [[offscreen], [onscreen]]
    ctx.driver.ui.dump_ui_nodes.return_value = [_scrollable_node([0, 0, 1080, 1920])]

    result = scroll_to(ctx, {"resource_id": "btn"}, max_attempts=4)

    assert result["ok"] is True
    assert result["final_ratio"] >= 0.9
    ctx.driver.input.swipe.assert_called_once()


def test_element_not_found_returns_error(ctx):
    ctx.driver.ui.find_elements.return_value = []
    result = scroll_to(ctx, {"resource_id": "nope"})
    assert result["ok"] is False
    assert result["error"] == "element_not_found"


def test_no_scrollable_container_skips_swipe(ctx):
    """元素不满足 target_ratio，但没有包含目标元素的 scrollable 容器 → 直接失败，不重试。"""
    offscreen = _elem([440, 1939, 640, 2039])
    ctx.driver.ui.find_elements.return_value = [offscreen]
    ctx.driver.ui.dump_ui_nodes.return_value = []  # 页面上没有任何 scrollable 容器

    result = scroll_to(ctx, {"resource_id": "btn"})

    assert result["ok"] is False
    assert result["skipped_no_scrollable"] is True
    ctx.driver.input.swipe.assert_not_called()


def test_force_scroll_true_executes_one_swipe_when_already_visible(ctx):
    onscreen = _elem([100, 100, 300, 200])
    ctx.driver.ui.find_elements.return_value = [onscreen]
    ctx.driver.ui.dump_ui_nodes.return_value = [_scrollable_node([0, 0, 1080, 1920])]

    result = scroll_to(ctx, {"text": "Login"}, force_scroll=True)

    assert result["ok"] is True
    assert result["attempts"] == 1
    ctx.driver.input.swipe.assert_called_once()


def test_force_scroll_true_skips_when_no_scrollable(ctx):
    onscreen = _elem([100, 100, 300, 200])
    ctx.driver.ui.find_elements.return_value = [onscreen]
    ctx.driver.ui.dump_ui_nodes.return_value = []

    result = scroll_to(ctx, {"text": "Login"}, force_scroll=True)

    assert result["ok"] is True
    assert result["skipped_no_scrollable"] is True
    ctx.driver.input.swipe.assert_not_called()


def test_max_attempts_exhausted_returns_final_ratio(ctx):
    offscreen = _elem([440, 1939, 640, 2039])
    ctx.driver.ui.find_elements.return_value = [offscreen]  # 始终不可见
    ctx.driver.ui.dump_ui_nodes.return_value = [_scrollable_node([0, 0, 1080, 1920])]

    result = scroll_to(ctx, {"resource_id": "btn"}, max_attempts=2)

    assert result["ok"] is False
    assert result["attempts"] == 2
    assert result["final_ratio"] == 0.0


def test_selector_matches_scrollable_container_itself_warns(ctx):
    container = _elem([0, 0, 1080, 1920], scrollable=True)
    ctx.driver.ui.find_elements.return_value = [container]

    result = scroll_to(ctx, {"class_name": "android.widget.ScrollView"})

    assert result["ok"] is True
    assert result.get("warning") == "selector_matched_scrollable_container_itself"
    ctx.driver.input.swipe.assert_not_called()


def test_cumulative_scroll_limit_exceeded(ctx):
    """即使还没用完 max_attempts，累计滑动距离超过屏幕高度 3 倍也应提前失败退出。"""
    offscreen = _elem([440, 1939, 640, 2039])
    ctx.driver.ui.find_elements.return_value = [offscreen]  # 始终不可见
    ctx.driver.ui.dump_ui_nodes.return_value = [_scrollable_node([0, 0, 1080, 1920])]

    result = scroll_to(ctx, {"resource_id": "btn"}, max_attempts=100)

    assert result["ok"] is False
    assert result["error"] == "cumulative_scroll_limit_exceeded"
    assert result["attempts"] < 100
