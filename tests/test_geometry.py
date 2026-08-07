"""compute_visibility 纯几何计算单测（平台无关）。"""

from __future__ import annotations

from phone_pilot.core.geometry import compute_visibility


def test_fully_visible():
    visible, ratio = compute_visibility((100, 100, 200, 200), (1080, 1920))
    assert visible is True
    assert abs(ratio - 1.0) < 1e-6


def test_fully_outside_below_screen():
    """回归踩坑案例：btn_simulate_loss 场景，元素完全在屏幕下方。"""
    visible, ratio = compute_visibility((440, 1939, 640, 2039), (1080, 1920))
    assert visible is False
    assert ratio == 0.0


def test_partially_visible():
    visible, ratio = compute_visibility((100, 1900, 200, 2000), (1080, 1920))
    assert visible is True
    assert 0.0 < ratio < 1.0


def test_invalid_bounds_zero_size_returns_none():
    visible, ratio = compute_visibility((100, 100, 100, 100), (1080, 1920))
    assert visible is None
    assert ratio is None


def test_invalid_bounds_none_returns_none():
    visible, ratio = compute_visibility(None, (1080, 1920))
    assert visible is None
    assert ratio is None


def test_screen_size_unavailable_returns_none():
    visible, ratio = compute_visibility((100, 100, 200, 200), None)
    assert visible is None
    assert ratio is None


def test_negative_coordinates_bounds():
    """悬浮窗负坐标场景（依赖 Task 1 的正则修复，这里只测几何计算本身）。"""
    visible, ratio = compute_visibility((-50, -50, 50, 50), (1080, 1920))
    assert visible is True
    assert 0.0 < ratio < 1.0
