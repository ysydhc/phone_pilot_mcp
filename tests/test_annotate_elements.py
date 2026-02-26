"""截图 + 元素标注合一 功能测试。
Tests for annotate_elements_on_screenshot, _collect_page_elements, phone_tap_element.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from phone_pilot.extensions.vision.annotate import (
    annotate_elements_on_screenshot,
    _color_for_type,
)
from phone_pilot.mcp.server import (
    _collect_page_elements,
)


# ---------------------------------------------------------------------------
# Helpers: fake UINode & driver
# ---------------------------------------------------------------------------

@dataclass
class FakeUINode:
    text: str = ""
    content_desc: str = ""
    hint: str = ""
    resource_id: str = ""
    class_name: str = ""
    key: str = ""
    package: str = ""
    bounds: str = ""
    clickable: bool = False
    enabled: bool = True
    focusable: bool = False
    focused: bool = False
    selected: bool = False
    checkable: bool = False
    checked: bool = False
    long_clickable: bool = False
    scrollable: bool = False
    password: bool = False
    _center_x: int = 0
    _center_y: int = 0

    def bounds_tuple(self) -> Optional[tuple[int, int, int, int]]:
        import re
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", self.bounds)
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))

    def center(self) -> Optional[tuple[int, int]]:
        if self._center_x and self._center_y:
            return (self._center_x, self._center_y)
        b = self.bounds_tuple()
        if not b:
            return None
        x1, y1, x2, y2 = b
        return ((x1 + x2) // 2, (y1 + y2) // 2)


class FakeUIDriver:
    def __init__(self, nodes):
        self._nodes = nodes

    def dump_ui_nodes(self):
        return self._nodes

    def get_current_activity(self):
        return {"package": "com.test", "activity": ".Main"}

    def find_elements(self, selector):
        return []

    def dump_ui_hierarchy(self):
        return "<hierarchy />"


class FakeScreenDriver:
    def get_screen_size(self):
        return (1080, 1920)

    def screenshot(self):
        # Create a simple 100x100 white image
        img = np.ones((100, 100, 3), dtype=np.uint8) * 255
        _, buf = cv2.imencode(".png", img)
        return buf.tobytes()


class FakeInputDriver:
    def __init__(self):
        self.taps = []

    def tap(self, x, y, **kwargs):
        self.taps.append((x, y))
        return {"ok": True, "x": x, "y": y}

    def swipe(self, *a, **kw):
        return {"ok": True}


class FakeAppDriver:
    def launch_app(self, package, activity=None):
        return {"ok": True, "package": package}

    def force_stop(self, package):
        return {"ok": True, "package": package}

    def list_packages(self, include_system=False):
        return []


class FakeDriver:
    platform = "android"

    def __init__(self, nodes=None):
        self.ui = FakeUIDriver(nodes or [])
        self.screen = FakeScreenDriver()
        self.input = FakeInputDriver()
        self.app = FakeAppDriver()


# ---------------------------------------------------------------------------
# annotate_elements_on_screenshot tests
# ---------------------------------------------------------------------------

class TestAnnotateElementsOnScreenshot:
    """Test the annotate_elements_on_screenshot function."""

    def _make_png(self, w: int = 200, h: int = 300) -> bytes:
        img = np.ones((h, w, 3), dtype=np.uint8) * 200
        _, buf = cv2.imencode(".png", img)
        return buf.tobytes()

    def test_empty_elements(self):
        """空元素列表应返回有效 PNG 且不崩溃。"""
        png = self._make_png()
        result = annotate_elements_on_screenshot(png, [])
        assert isinstance(result, bytes)
        assert len(result) > 0
        # Should be a valid PNG (starts with PNG magic)
        assert result[:4] == b"\x89PNG"

    def test_single_element(self):
        """单个元素标注应生成有效 PNG。"""
        png = self._make_png()
        elements = [{
            "index": 0,
            "type": "button",
            "label": "确定",
            "bounds": [10, 20, 80, 60],
            "center": [45, 40],
            "clickable": True,
            "scrollable": False,
            "resource_id": "btn_ok",
        }]
        result = annotate_elements_on_screenshot(png, elements)
        assert isinstance(result, bytes)
        assert result[:4] == b"\x89PNG"
        # Annotated image should differ from original
        assert result != png

    def test_multiple_element_types(self):
        """不同类型的元素应使用不同颜色标注。"""
        png = self._make_png(400, 600)
        elements = [
            {"index": 0, "type": "button", "label": "按钮", "bounds": [10, 10, 100, 50], "center": [55, 30]},
            {"index": 1, "type": "input", "label": "输入框", "bounds": [10, 60, 200, 100], "center": [105, 80]},
            {"index": 2, "type": "text", "label": "文本", "bounds": [10, 110, 150, 140], "center": [80, 125]},
            {"index": 3, "type": "scroll_container", "label": "", "bounds": [10, 150, 390, 580], "center": [200, 365]},
        ]
        result = annotate_elements_on_screenshot(png, elements)
        assert isinstance(result, bytes)
        assert result[:4] == b"\x89PNG"

    def test_skips_invalid_bounds(self):
        """bounds 无效的元素应跳过，不崩溃。"""
        png = self._make_png()
        elements = [
            {"index": 0, "type": "button", "label": "OK", "bounds": None, "center": [50, 50]},
            {"index": 1, "type": "text", "label": "Hi", "bounds": [10, 10, 5, 5], "center": [7, 7]},  # x2 < x1
            {"index": 2, "type": "text", "label": "Valid", "bounds": [10, 10, 90, 50], "center": [50, 30]},
        ]
        result = annotate_elements_on_screenshot(png, elements)
        assert isinstance(result, bytes)

    def test_long_label_truncated(self):
        """超长标签应被截断（不崩溃）。"""
        png = self._make_png()
        elements = [{
            "index": 0,
            "type": "text",
            "label": "这是一个非常非常非常非常非常非常长的标签文字需要被截断",
            "bounds": [10, 10, 180, 50],
            "center": [95, 30],
        }]
        result = annotate_elements_on_screenshot(png, elements)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# _color_for_type tests
# ---------------------------------------------------------------------------

class TestColorForType:
    """Test color mapping for element types."""

    def test_button_green(self):
        assert _color_for_type("button") == (0, 200, 0)

    def test_input_blue(self):
        assert _color_for_type("input") == (200, 120, 0)

    def test_text_gray(self):
        assert _color_for_type("text") == (160, 160, 160)

    def test_scroll_orange(self):
        assert _color_for_type("scroll_container") == (0, 160, 255)

    def test_unknown_default(self):
        color = _color_for_type("unknown_type")
        assert isinstance(color, tuple)
        assert len(color) == 3


# ---------------------------------------------------------------------------
# _collect_page_elements tests
# ---------------------------------------------------------------------------

class TestCollectPageElements:
    """Test the extracted _collect_page_elements function."""

    def test_empty_nodes(self):
        driver = FakeDriver(nodes=[])
        elements, scrollable, texts = _collect_page_elements(driver)
        assert elements == []
        assert scrollable == []
        assert texts == []

    def test_filters_containers(self):
        """纯容器节点应被过滤。"""
        nodes = [
            FakeUINode(class_name="android.widget.FrameLayout", bounds="[0,0][100,100]"),
            FakeUINode(text="Hello", class_name="android.widget.TextView", bounds="[10,10][90,50]"),
        ]
        driver = FakeDriver(nodes=nodes)
        elements, _, _ = _collect_page_elements(driver)
        assert len(elements) == 1
        assert elements[0]["label"] == "Hello"

    def test_interactive_elements_kept(self):
        """可交互元素（无文本但 clickable）应保留。"""
        nodes = [
            FakeUINode(class_name="android.widget.ImageButton", clickable=True, bounds="[10,10][60,60]"),
        ]
        driver = FakeDriver(nodes=nodes)
        elements, _, _ = _collect_page_elements(driver)
        assert len(elements) == 1
        assert elements[0]["type"] == "button"
        assert elements[0]["clickable"] is True

    def test_scrollable_areas_detected(self):
        """可滚动节点应出现在 scrollable_areas 中。"""
        nodes = [
            FakeUINode(
                class_name="android.widget.ScrollView",
                scrollable=True,
                bounds="[0,100][1080,1800]",
            ),
        ]
        driver = FakeDriver(nodes=nodes)
        _, scrollable, _ = _collect_page_elements(driver)
        assert len(scrollable) == 1
        assert scrollable[0]["direction"] == "vertical"

    def test_visual_sort_and_reindex(self):
        """元素应按视觉位置排序，编号应重新从 0 开始。"""
        nodes = [
            FakeUINode(text="Bottom", class_name="android.widget.Button", clickable=True,
                       bounds="[10,500][200,550]"),
            FakeUINode(text="Top", class_name="android.widget.Button", clickable=True,
                       bounds="[10,10][200,60]"),
        ]
        driver = FakeDriver(nodes=nodes)
        elements, _, _ = _collect_page_elements(driver)
        assert len(elements) == 2
        assert elements[0]["label"] == "Top"
        assert elements[0]["index"] == 0
        assert elements[1]["label"] == "Bottom"
        assert elements[1]["index"] == 1

    def test_resource_id_simplified(self):
        """resource_id 应去除包名前缀。"""
        nodes = [
            FakeUINode(
                text="Submit",
                class_name="android.widget.Button",
                clickable=True,
                resource_id="com.example.app:id/btn_submit",
                bounds="[10,10][200,60]",
            ),
        ]
        driver = FakeDriver(nodes=nodes)
        elements, _, _ = _collect_page_elements(driver)
        assert elements[0]["resource_id"] == "btn_submit"

    def test_all_texts_collected(self):
        """all_texts 应包含所有可见文本和 content_desc。"""
        nodes = [
            FakeUINode(text="Hello", bounds="[0,0][100,50]"),
            FakeUINode(content_desc="Logo", bounds="[0,50][100,100]"),
            FakeUINode(text="World", content_desc="World", bounds="[0,100][100,150]"),
        ]
        driver = FakeDriver(nodes=nodes)
        _, _, texts = _collect_page_elements(driver)
        assert "Hello" in texts
        assert "Logo" in texts
        assert "World" in texts

    def test_include_invisible(self):
        """include_invisible=True 时应包含更多元素。"""
        nodes = [
            FakeUINode(class_name="android.view.View", bounds="[0,0][100,100]"),
            FakeUINode(text="Visible", class_name="android.widget.TextView", bounds="[0,100][100,200]"),
        ]
        driver = FakeDriver(nodes=nodes)
        elems_default, _, _ = _collect_page_elements(driver, include_invisible=False)
        elems_all, _, _ = _collect_page_elements(driver, include_invisible=True)
        assert len(elems_all) >= len(elems_default)
