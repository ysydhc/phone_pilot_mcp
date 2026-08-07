"""compressed 参数透出：find_elements/dump_ui_nodes/element_query_impl。

注意：`find_elements`/`dump_ui_nodes` 实际定义在 `AndroidUIDriver`
（`phone_pilot/android/driver.py`），通过 `AndroidDriver.ui` 暴露，
而不是 `AndroidDriver` 本身的方法，因此这里统一走 `driver.ui.xxx()`。
"""

from __future__ import annotations

from unittest.mock import patch

from phone_pilot.android.driver import AndroidDriver
from phone_pilot.android.ui.query import element_query_impl
from phone_pilot.core import ui_snapshot_cache


def _make_driver(serial="serial1"):
    ui_snapshot_cache.invalidate()
    return AndroidDriver(serial)


def test_find_elements_default_compressed_true():
    """默认不传 compressed，行为与改动前一致（走 compressed=True 路径）。"""
    driver = _make_driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": "<hierarchy/>"},
    ) as mock_dump:
        driver.ui.find_elements({"text": "x"})
    assert mock_dump.call_args.kwargs.get("compressed", True) is True


def test_find_elements_compressed_false_passthrough():
    """compressed=False 时，底层 dump_ui_xml 应收到 compressed=False。"""
    driver = _make_driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": "<hierarchy/>"},
    ) as mock_dump:
        driver.ui.find_elements({"text": "x"}, compressed=False)
    assert mock_dump.call_args.kwargs.get("compressed") is False


def test_find_elements_compressed_true_false_not_cross_polluted():
    """compressed=True 和 compressed=False 两次调用各自触发一次真实 dump（Task 2 前置修复的联合验证）。"""
    driver = _make_driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": "<hierarchy/>"},
    ) as mock_dump:
        driver.ui.find_elements({"text": "x"}, compressed=True)
        driver.ui.find_elements({"text": "x"}, compressed=False)
    assert mock_dump.call_count == 2


def test_element_query_impl_compressed_false_passthrough():
    with patch(
        "phone_pilot.android.ui.query.dump_ui_xml",
        return_value={"ok": True, "xml": "<hierarchy/>"},
    ) as mock_dump:
        element_query_impl("serial1", {"text": "x"}, compressed=False)
    assert mock_dump.call_args.kwargs.get("compressed") is False
    # element_query_impl 从未使用 ui_snapshot_cache（设计文档 §4.1 已确认），
    # 这里只验证参数打通，不涉及缓存。


def test_element_query_impl_default_compressed_true():
    with patch(
        "phone_pilot.android.ui.query.dump_ui_xml",
        return_value={"ok": True, "xml": "<hierarchy/>"},
    ) as mock_dump:
        element_query_impl("serial1", {"text": "x"})
    assert mock_dump.call_args.kwargs.get("compressed", True) is True
