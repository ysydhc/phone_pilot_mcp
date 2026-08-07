"""验证三处 bounds 正则解析都支持负数坐标（悬浮窗场景）。"""

from __future__ import annotations

from phone_pilot.android.ui.automator import UINode as AndroidUINode
from phone_pilot.core.ui_node import UINode as CoreUINode
from phone_pilot.android.ui.wait import _parse_bounds


def test_android_uinode_bounds_tuple_negative():
    node = AndroidUINode(
        package="p", text="t", content_desc=None, hint=None, resource_id=None,
        class_name=None, bounds="[-50,100][200,300]",
    )
    assert node.bounds_tuple() == (-50, 100, 200, 300)


def test_core_uinode_bounds_tuple_negative():
    node = CoreUINode(bounds="[-50,100][200,300]")
    assert node.bounds_tuple() == (-50, 100, 200, 300)


def test_wait_parse_bounds_negative():
    assert _parse_bounds("[-50,100][200,300]") == (-50, 100, 200, 300)


def test_wait_parse_bounds_negative_both_coords():
    assert _parse_bounds("[-50,-30][200,300]") == (-50, -30, 200, 300)


def test_positive_bounds_still_work():
    """回归：修复负号支持不应破坏正常正数坐标解析。"""
    assert _parse_bounds("[33,1246][1047,1656]") == (33, 1246, 1047, 1656)
