"""Tests for require_visible support in wait_for_element_appear.

验证 visible 判定逻辑：
- 有效 bounds（width>0, height>0）→ visible 满足
- 零尺寸 bounds → visible 不满足
- visible/displayed 字段为 False → visible 不满足
- 超时后返回 present_but_not_ready 语义
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from phone_pilot.android.ui.wait import wait_for_element_appear, _match_is_visible


# ---------------------------------------------------------------------------
# _match_is_visible 单元测试
# ---------------------------------------------------------------------------


def test_visible_valid_bounds_string():
    match = {"bounds": "[100,200][300,400]"}
    assert _match_is_visible(match) is True


def test_visible_valid_bounds_list():
    match = {"bounds": [100, 200, 300, 400]}
    assert _match_is_visible(match) is True


def test_visible_zero_size_bounds():
    match = {"bounds": "[100,200][100,200]"}
    assert _match_is_visible(match) is False


def test_visible_false_field():
    match = {"bounds": "[0,0][100,100]", "visible": False}
    assert _match_is_visible(match) is False


def test_visible_displayed_false_field():
    match = {"bounds": "[0,0][100,100]", "displayed": False}
    assert _match_is_visible(match) is False


def test_visible_no_bounds():
    match = {"text": "hello"}
    assert _match_is_visible(match) is False


def test_visible_none_match():
    assert _match_is_visible(None) is False


def test_visible_malformed_bounds():
    match = {"bounds": "garbage"}
    assert _match_is_visible(match) is False


# ---------------------------------------------------------------------------
# wait_for_element_appear 集成测试
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_polling():
    """让测试不真的 sleep。"""
    with patch("phone_pilot.android.ui.wait.time.sleep"):
        yield


def test_require_visible_satisfied():
    """元素有有效 bounds → require_visible 满足。"""
    match = {"text": "Login", "bounds": "[100,200][300,400]", "clickable": True}

    with patch(
        "phone_pilot.android.ui.wait.element_exists_impl",
        return_value={"exists": True, "match": match, "method": "uiautomator"},
    ):
        result = wait_for_element_appear(
            "serial", query="Login", require_visible=True, timeout_s=1.0, interval_s=0.1
        )

    assert result["satisfied"] is True


def test_require_visible_zero_size_not_satisfied():
    """元素 bounds 为零尺寸 → require_visible 不满足，超时。"""
    match = {"text": "Login", "bounds": "[100,200][100,200]"}

    with patch(
        "phone_pilot.android.ui.wait.element_exists_impl",
        return_value={"exists": True, "match": match, "method": "uiautomator"},
    ):
        result = wait_for_element_appear(
            "serial", query="Login", require_visible=True, timeout_s=0.3, interval_s=0.1
        )

    assert result["satisfied"] is False
    assert "present_but_not_ready" in result.get("message", "") or "timeout" in result.get("message", "")


def test_require_visible_with_clickable_and_enabled():
    """require_visible + require_clickable + require_enabled 组合。"""
    match = {
        "text": "Login",
        "bounds": "[100,200][300,400]",
        "clickable": True,
        "enabled": True,
    }

    with patch(
        "phone_pilot.android.ui.wait.element_exists_impl",
        return_value={"exists": True, "match": match, "method": "uiautomator"},
    ):
        result = wait_for_element_appear(
            "serial",
            query="Login",
            require_visible=True,
            require_clickable=True,
            require_enabled=True,
            timeout_s=1.0,
            interval_s=0.1,
        )

    assert result["satisfied"] is True


def test_require_visible_false_field_not_satisfied():
    """visible=False → require_visible 不满足。"""
    match = {"text": "Login", "bounds": "[0,0][100,100]", "visible": False}

    with patch(
        "phone_pilot.android.ui.wait.element_exists_impl",
        return_value={"exists": True, "match": match, "method": "uiautomator"},
    ):
        result = wait_for_element_appear(
            "serial", query="Login", require_visible=True, timeout_s=0.3, interval_s=0.1
        )

    assert result["satisfied"] is False


def test_require_visible_default_false_keeps_old_behavior():
    """require_visible 默认 False，不检查 bounds，保持旧行为。"""
    match = {"text": "Login", "bounds": "[100,200][100,200]"}  # zero size

    with patch(
        "phone_pilot.android.ui.wait.element_exists_impl",
        return_value={"exists": True, "match": match, "method": "uiautomator"},
    ):
        result = wait_for_element_appear(
            "serial", query="Login", timeout_s=1.0, interval_s=0.1
        )

    # Old behavior: only checks present, not visible → satisfied
    assert result["satisfied"] is True
