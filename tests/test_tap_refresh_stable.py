"""Tests for find_text stable param and TextElement.tap() coordinate refresh.

验证修复方案 B+C：
- B: TextElement.tap() 自动 refresh 过时坐标
- C: find_text() 增加 stable 参数，查找前等待页面稳定
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from phone_pilot.core.ui.element import TextElement
from phone_pilot.script_api.find import find_text
from phone_pilot.script_api.context import ScriptContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx() -> ScriptContext:
    ctx = MagicMock(spec=ScriptContext)
    ctx.auto_screenshot = False
    ctx._ui_cache = None
    ctx._observe = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# C: find_text stable param
# ---------------------------------------------------------------------------

def test_find_text_stable_param_defaults_to_true():
    """find_text 默认 stable=True。"""
    import inspect
    sig = inspect.signature(find_text)
    assert sig.parameters["stable"].default is True


def test_find_text_stable_waits_before_search():
    """stable=True 时查找前先等待页面稳定。"""
    ctx = _make_ctx()
    ctx.driver = MagicMock()
    ctx.driver.dump_ui_hierarchy.return_value = "<hierarchy></hierarchy>"

    sleep_calls = []

    def _mock_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("phone_pilot.script_api.find.time.sleep", side_effect=_mock_sleep):
        # 模拟底层库返回有效结果
        with patch("phone_pilot.script_api.find._uia_find_text_boxes", return_value=[{"text": "test"}]):
            result = find_text(ctx, "test", stable=True, retry_attempts=1)

    assert 0.3 in sleep_calls, f"Expected 0.3s stable wait, got {sleep_calls}"
    assert result is not None


def test_find_text_stable_false_skips_wait():
    """stable=False 跳过稳定等待。"""
    ctx = _make_ctx()
    ctx.driver = MagicMock()
    ctx.driver.dump_ui_hierarchy.return_value = "<hierarchy></hierarchy>"

    sleep_calls = []

    def _mock_sleep(seconds):
        sleep_calls.append(seconds)

    with patch("phone_pilot.script_api.find.time.sleep", side_effect=_mock_sleep):
        with patch("phone_pilot.script_api.find._uia_find_text_boxes", return_value=[{"text": "test"}]):
            result = find_text(ctx, "test", stable=False, retry_attempts=1)

    assert 0.3 not in sleep_calls, f"stable=False 不应等待 0.3s, got {sleep_calls}"
    assert result is not None


# ---------------------------------------------------------------------------
# B: TextElement.tap() coordinate refresh
# ---------------------------------------------------------------------------

def test_tap_refreshes_when_dump_is_stale():
    """当 dump 时间戳过旧时，tap() 应自动 refresh 坐标。"""
    elem = TextElement(x=10, y=20, width=100, height=50, text="test")
    elem._ops = {"tap_xy": MagicMock(return_value={"ok": True})}
    elem.meta = {"_dump_ts": time.time() - 2.0}  # 2 秒前的 dump

    # 模拟 refresh 返回新坐标
    refreshed = TextElement(x=15, y=25, width=100, height=50, text="test")
    refreshed._ops = elem._ops

    with patch.object(elem, "refresh", return_value=refreshed) as mock_refresh:
        res = elem.tap()

    assert mock_refresh.called
    assert res["ok"] is True
    # 应该用新坐标点击
    # center of refreshed: (15+50, 25+25) = (65, 50)
    elem._ops["tap_xy"].assert_called_once()
    args = elem._ops["tap_xy"].call_args
    assert args[0][0] == 65
    assert args[0][1] == 50


def test_tap_skips_refresh_when_fresh():
    """当 dump 时间戳很新时，tap() 不应 refresh。"""
    elem = TextElement(x=10, y=20, width=100, height=50, text="test")
    elem._ops = {"tap_xy": MagicMock(return_value={"ok": True})}
    elem.meta = {"_dump_ts": time.time() - 0.1}  # 0.1 秒前

    with patch.object(elem, "refresh", return_value=None) as mock_refresh:
        res = elem.tap()

    assert not mock_refresh.called
    assert res["ok"] is True
    # 应该用原始坐标
    elem._ops["tap_xy"].assert_called_once()
    args = elem._ops["tap_xy"].call_args
    assert args[0][0] == 60  # (10+50, 20+25)
    assert args[0][1] == 45


def test_tap_refresh_failure_uses_original_coords():
    """refresh 失败时，应使用原始坐标。"""
    elem = TextElement(x=10, y=20, width=100, height=50, text="test")
    elem._ops = {"tap_xy": MagicMock(return_value={"ok": True})}
    elem.meta = {"_dump_ts": time.time() - 2.0}

    with patch.object(elem, "refresh", return_value=None) as mock_refresh:
        res = elem.tap()

    assert mock_refresh.called
    assert res["ok"] is True
    # refresh 失败，仍然用原始坐标
    elem._ops["tap_xy"].assert_called_once()
    args = elem._ops["tap_xy"].call_args
    assert args[0][0] == 60
    assert args[0][1] == 45


def test_tap_with_wait_refreshes_after_sleep():
    """wait > 0.5 时，sleep 后应再次 refresh。"""
    elem = TextElement(x=10, y=20, width=100, height=50, text="test")
    elem._ops = {"tap_xy": MagicMock(return_value={"ok": True})}
    elem.meta = {"_dump_ts": time.time() - 0.2}  # 很新

    refreshed = TextElement(x=20, y=30, width=100, height=50, text="test")
    refreshed._ops = elem._ops

    with patch.object(elem, "refresh", return_value=refreshed) as mock_refresh:
        with patch("time.sleep"):
            res = elem.tap(wait=1.0)

    # wait=1.0 > 0.5，应该在 sleep 后 refresh
    assert mock_refresh.called
    assert res["ok"] is True
    # 应该用新坐标
    args = elem._ops["tap_xy"].call_args
    assert args[0][0] == 70  # (20+50, 30+25)
    assert args[0][1] == 55
