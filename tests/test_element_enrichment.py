"""find_elements/dump_ui_nodes 返回结果的 bounds_rect/visible/visible_ratio 富化。

注意：`find_elements` 实际定义在 `AndroidUIDriver`（`phone_pilot/android/driver.py`），
通过 `AndroidDriver.ui` 暴露，因此这里统一走 `driver.ui.find_elements(...)`
（与 `tests/test_find_elements_compressed.py` 的调用方式一致）。

屏幕尺寸富化直接调用 `phone_pilot.android.driver` 模块里的 `_get_screen_size`
（从 `android/device/utils.get_screen_size` 导入的别名），因此测试直接 patch
这个模块级函数，而不是给 driver 实例打补丁。
"""

from __future__ import annotations

from unittest.mock import patch

from phone_pilot.android.driver import AndroidDriver
from phone_pilot.core import ui_snapshot_cache


def _driver():
    ui_snapshot_cache.invalidate()
    return AndroidDriver("serial1")


_XML_VISIBLE = (
    '<hierarchy><node text="Login" bounds="[100,100][300,200]" '
    'clickable="true" enabled="true" /></hierarchy>'
)


def test_bounds_rect_added():
    driver = _driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": _XML_VISIBLE},
    ), patch(
        "phone_pilot.android.driver._get_screen_size", return_value=(1080, 1920)
    ):
        results = driver.ui.find_elements({"text": "Login"})
    assert results[0]["bounds_rect"] == [100, 100, 300, 200]
    assert results[0]["bounds"] == "[100,100][300,200]"  # 原始字段不变


def test_visible_true_for_onscreen_element():
    driver = _driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": _XML_VISIBLE},
    ), patch(
        "phone_pilot.android.driver._get_screen_size", return_value=(1080, 1920)
    ):
        results = driver.ui.find_elements({"text": "Login"})
    assert results[0]["visible"] is True
    assert abs(results[0]["visible_ratio"] - 1.0) < 1e-6


def test_visible_false_for_offscreen_element():
    """回归 btn_simulate_loss 踩坑案例。"""
    xml = (
        '<hierarchy><node resource-id="btn_simulate_loss" '
        'bounds="[440,1939][640,2039]" /></hierarchy>'
    )
    driver = _driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": xml},
    ), patch(
        "phone_pilot.android.driver._get_screen_size", return_value=(1080, 1920)
    ):
        results = driver.ui.find_elements({"resource_id": "btn_simulate_loss"})
    assert results[0]["visible"] is False
    assert results[0]["visible_ratio"] == 0.0


def test_visible_none_for_zero_size_bounds():
    """WebView/Compose 常见的全 0 bounds → 无法判定，不是 False。"""
    xml = '<hierarchy><node text="webview_content" bounds="[0,0][0,0]" /></hierarchy>'
    driver = _driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": xml},
    ), patch(
        "phone_pilot.android.driver._get_screen_size", return_value=(1080, 1920)
    ):
        results = driver.ui.find_elements({"text": "webview_content"})
    assert results[0]["visible"] is None
    assert results[0]["visible_ratio"] is None


def test_get_screen_size_called_once_per_find_elements_call():
    """一次 find_elements 调用只应查询一次屏幕尺寸（不随节点数线性增长）。"""
    xml = (
        "<hierarchy>"
        + "".join(
            f'<node text="item{i}" bounds="[0,{i * 10}][100,{i * 10 + 10}]" />'
            for i in range(5)
        )
        + "</hierarchy>"
    )
    driver = _driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": xml},
    ), patch(
        "phone_pilot.android.driver._get_screen_size", return_value=(1080, 1920)
    ) as mock_screen:
        driver.ui.find_elements({"text_contains": "item"})
    assert mock_screen.call_count == 1


def test_screen_size_unavailable_degrades_to_none():
    driver = _driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml",
        return_value={"ok": True, "xml": _XML_VISIBLE},
    ), patch(
        "phone_pilot.android.driver._get_screen_size",
        side_effect=RuntimeError("offline"),
    ):
        results = driver.ui.find_elements({"text": "Login"})
    assert results[0]["visible"] is None
    assert results[0]["visible_ratio"] is None
    assert results[0]["visible_reason"] == "screen_size_unavailable"


def test_auto_uncompressed_fallback_results_also_enriched():
    """auto_uncompressed_fallback 兜底路径命中的结果也要经过同一次富化，
    且不应因为兜底再多查一次屏幕尺寸（复用同一次富化调用）。
    """
    from phone_pilot.android.driver import _fallback_cooldown_until

    _fallback_cooldown_until.clear()
    empty_xml = {"ok": True, "xml": "<hierarchy></hierarchy>"}
    hit_xml = {
        "ok": True,
        "xml": (
            '<hierarchy><node resource-id="native_container" '
            'class="android.widget.FrameLayout" bounds="[0,0][100,100]" /></hierarchy>'
        ),
    }
    driver = _driver()
    with patch(
        "phone_pilot.android.driver.dump_ui_xml", side_effect=[empty_xml, hit_xml]
    ), patch(
        "phone_pilot.android.driver._get_screen_size", return_value=(1080, 1920)
    ) as mock_screen:
        results = driver.ui.find_elements(
            {
                "resource_id": "native_container",
                "class_name": "android.widget.FrameLayout",
            },
            auto_uncompressed_fallback=True,
        )
    assert results[0]["from_uncompressed"] is True
    assert results[0]["bounds_rect"] == [0, 0, 100, 100]
    assert results[0]["visible"] is True
    assert mock_screen.call_count == 1
