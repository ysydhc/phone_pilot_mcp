"""compressed 参数透出：find_elements/dump_ui_nodes/element_query_impl。

注意：`find_elements`/`dump_ui_nodes` 实际定义在 `AndroidUIDriver`
（`phone_pilot/android/driver.py`），通过 `AndroidDriver.ui` 暴露，
而不是 `AndroidDriver` 本身的方法，因此这里统一走 `driver.ui.xxx()`。
"""

from __future__ import annotations

from unittest.mock import patch

from phone_pilot.android.driver import AndroidDriver, _fallback_cooldown_until
from phone_pilot.android.ui.query import element_query_impl
from phone_pilot.core import ui_snapshot_cache


def _make_driver(serial="serial1"):
    ui_snapshot_cache.invalidate()
    # auto_uncompressed_fallback 的冷却状态是模块级字典（按 serial 存），
    # 测试间共用同一个默认 serial，需要显式清空以保证测试互相独立。
    _fallback_cooldown_until.clear()
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


def test_auto_fallback_disabled_by_default():
    """默认不开启自动降级：compressed 命中为空时直接返回空列表，不触发第二次 dump。"""
    driver = _make_driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": "<hierarchy></hierarchy>"},
    ) as mock_dump:
        result = driver.ui.find_elements(
            {"resource_id": "native_container", "class_name": "android.widget.FrameLayout"}
        )
    assert result == []
    assert mock_dump.call_count == 1


def test_auto_fallback_triggers_on_container_heuristic():
    """开启后，命中容器类名启发式 + 压缩模式为空 → 触发一次非压缩兜底。"""
    driver = _make_driver()
    empty_xml = {"ok": True, "xml": "<hierarchy></hierarchy>"}
    hit_xml = {
        "ok": True,
        "xml": (
            '<hierarchy><node resource-id="native_container" '
            'class="android.widget.FrameLayout" bounds="[0,0][100,100]" /></hierarchy>'
        ),
    }
    with patch(
        "phone_pilot.android.driver.dump_ui_xml", side_effect=[empty_xml, hit_xml]
    ) as mock_dump:
        result = driver.ui.find_elements(
            {"resource_id": "native_container", "class_name": "android.widget.FrameLayout"},
            auto_uncompressed_fallback=True,
        )
    assert mock_dump.call_count == 2
    assert mock_dump.call_args_list[0].kwargs.get("compressed", True) is True
    assert mock_dump.call_args_list[1].kwargs.get("compressed") is False
    assert result[0]["from_uncompressed"] is True


def test_auto_fallback_skips_without_class_name_heuristic():
    """没有 class_name 命中启发式清单时，不触发兜底（即使 resource_id 命中）。"""
    driver = _make_driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": "<hierarchy></hierarchy>"},
    ) as mock_dump:
        result = driver.ui.find_elements(
            {"resource_id": "native_container"}, auto_uncompressed_fallback=True
        )
    assert result == []
    assert mock_dump.call_count == 1


def test_auto_fallback_cooldown_prevents_repeat_dump():
    """冷却窗口内重复触发不应重复调用非压缩 dump。"""
    driver = _make_driver()
    empty_xml = {"ok": True, "xml": "<hierarchy></hierarchy>"}
    with patch(
        "phone_pilot.android.driver.dump_ui_xml", return_value=empty_xml
    ) as mock_dump:
        selector = {"resource_id": "x", "class_name": "android.widget.FrameLayout"}
        driver.ui.find_elements(selector, auto_uncompressed_fallback=True)
        driver.ui.find_elements(selector, auto_uncompressed_fallback=True)
    # 第一次：compressed dump + fallback dump = 2 次；第二次因冷却窗口只应 compressed dump 1 次
    assert mock_dump.call_count == 3


def test_auto_fallback_dump_error_not_swallowed():
    """非压缩兜底本身失败时，不应静默返回空列表，需透传失败原因。"""
    driver = _make_driver()
    empty_xml = {"ok": True, "xml": "<hierarchy></hierarchy>"}
    dump_error = {"ok": False, "error": "adb_timeout"}
    with patch(
        "phone_pilot.android.driver.dump_ui_xml", side_effect=[empty_xml, dump_error]
    ):
        result = driver.ui.find_elements(
            {"resource_id": "x", "class_name": "android.widget.FrameLayout"},
            auto_uncompressed_fallback=True,
        )
    # 具体返回形状按实现调整，但必须能区分"没找到"和"dump失败"——
    # 这里约定 find_elements 在 driver 层遇到 fallback dump 失败时记录日志但仍返回
    # 空列表（因为 find_elements 契约本身是 list[dict]，无法在返回类型里表达错误），
    # 但 AndroidUIDriver 需要暴露最近一次 fallback 错误供上层查询（见实现步骤）。
    assert result == []
    assert driver.ui._last_fallback_error == "adb_timeout"
