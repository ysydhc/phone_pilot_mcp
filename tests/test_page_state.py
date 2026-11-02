"""phone_get_page_state 工具核心逻辑测试。"""

import asyncio
import base64
import io
from unittest.mock import MagicMock, patch

from phone_pilot.core import ui_snapshot_cache
from phone_pilot.core.ui_node import UINode
from phone_pilot.mcp._input_tools import phone_tap_text
from phone_pilot.mcp._page_state_tools import (
    _build_element_label,
    _classify_element,
    _encode_screenshot_b64,
    phone_get_page_state,
    phone_tap_element,
)


class TestClassifyElement:
    """Test UI element type classification."""

    def test_button(self):
        assert _classify_element("android.widget.Button") == "button"
        assert _classify_element("android.widget.ImageButton") == "button"

    def test_input(self):
        assert _classify_element("android.widget.EditText") == "input"

    def test_toggle(self):
        assert _classify_element("android.widget.CheckBox") == "toggle"
        assert _classify_element("android.widget.Switch") == "toggle"
        assert _classify_element("android.widget.ToggleButton") == "toggle"

    def test_text(self):
        assert _classify_element("android.widget.TextView") == "text"

    def test_image(self):
        assert _classify_element("android.widget.ImageView") == "image"

    def test_list(self):
        assert _classify_element("androidx.recyclerview.widget.RecyclerView") == "list"
        assert _classify_element("android.widget.ListView") == "list"

    def test_scroll(self):
        assert _classify_element("android.widget.ScrollView") == "scroll_container"
        assert (
            _classify_element("android.widget.HorizontalScrollView")
            == "scroll_container"
        )

    def test_pager(self):
        assert _classify_element("androidx.viewpager.widget.ViewPager") == "pager"

    def test_webview(self):
        assert _classify_element("android.webkit.WebView") == "webview"

    def test_none(self):
        assert _classify_element(None) == "view"
        assert _classify_element("") == "view"

    def test_unknown(self):
        assert _classify_element("android.view.View") == "view"


class TestBuildElementLabel:
    """Test element label construction."""

    class FakeNode:
        def __init__(self, text=None, content_desc=None, hint=None):
            self.text = text
            self.content_desc = content_desc
            self.hint = hint

    def test_text_only(self):
        n = self.FakeNode(text="设置")
        assert _build_element_label(n) == "设置"

    def test_desc_only(self):
        n = self.FakeNode(content_desc="返回")
        assert _build_element_label(n) == "[返回]"

    def test_text_and_desc(self):
        n = self.FakeNode(text="确定", content_desc="确认按钮")
        assert _build_element_label(n) == "确定 [确认按钮]"

    def test_text_same_as_desc(self):
        n = self.FakeNode(text="OK", content_desc="OK")
        assert _build_element_label(n) == "OK"

    def test_hint(self):
        n = self.FakeNode(hint="请输入用户名")
        assert _build_element_label(n) == "(hint: 请输入用户名)"

    def test_all_fields(self):
        n = self.FakeNode(text="搜索", content_desc="搜索框", hint="输入关键词")
        label = _build_element_label(n)
        assert "搜索" in label
        assert "[搜索框]" in label
        assert "(hint: 输入关键词)" in label

    def test_empty(self):
        n = self.FakeNode()
        assert _build_element_label(n) == ""


def _make_fake_node(
    text: str = "Btn",
    bounds: str = "[10,20][110,80]",
    clickable: bool = True,
    class_name: str = "android.widget.Button",
) -> UINode:
    return UINode(
        text=text,
        bounds=bounds,
        clickable=clickable,
        class_name=class_name,
    )


def test_tap_element_reuses_page_state_snapshot():
    """B1: phone_get_page_state followed by phone_tap_element(index=0) only calls dump_ui_nodes once."""
    ui_snapshot_cache.invalidate()
    driver = MagicMock()
    driver.ui.get_current_activity.return_value = {"package": "pkg", "activity": ".Main"}
    driver.screen.get_screen_size.return_value = (1080, 1920)
    driver.ui.dump_ui_nodes.return_value = [_make_fake_node("Start")]
    driver.input.tap.return_value = {"ok": True}

    with (
        patch(
            "phone_pilot.mcp._page_state_tools._resolve_device_serial",
            return_value=("dev1", "android", None),
        ),
        patch("phone_pilot.mcp._page_state_tools.get_driver", return_value=driver),
    ):
        state = asyncio.run(
            phone_get_page_state(device_serial="dev1", platform="android")
        )
        assert state["ok"] is True
        assert driver.ui.dump_ui_nodes.call_count == 1

        tap_res = asyncio.run(
            phone_tap_element(
                index=0, device_serial="dev1", platform="android", wait_ms=0
            )
        )
        assert tap_res["ok"] is True
        assert tap_res["snapshot_hit"] is True
        assert driver.ui.dump_ui_nodes.call_count == 1


def test_tap_element_return_page_state_preserves_cache():
    """B3: phone_tap_element(..., return_page_state=True) returns page_state and preserves the new snapshot."""
    ui_snapshot_cache.invalidate()
    driver = MagicMock()
    driver.ui.get_current_activity.return_value = {"package": "pkg", "activity": ".Next"}
    driver.screen.get_screen_size.return_value = (1080, 1920)
    driver.ui.dump_ui_nodes.side_effect = [
        [_make_fake_node("Page1Btn", "[10,20][110,80]")],
        [
            _make_fake_node("Page2Btn0", "[10,20][110,80]"),
            _make_fake_node("Page2Btn1", "[10,120][110,180]"),
        ],
    ]
    driver.input.tap.return_value = {"ok": True}

    with (
        patch(
            "phone_pilot.mcp._page_state_tools._resolve_device_serial",
            return_value=("dev1", "android", None),
        ),
        patch("phone_pilot.mcp._page_state_tools.get_driver", return_value=driver),
    ):
        # 1st tap with return_page_state=True: 1 dump for pre-tap (miss) + 1 dump for post-tap state = 2 dumps
        res1 = asyncio.run(
            phone_tap_element(
                index=0,
                device_serial="dev1",
                platform="android",
                wait_ms=0,
                return_page_state=True,
            )
        )
        assert res1["ok"] is True
        assert res1["page_state_updated"] is True
        assert "page_state" in res1
        assert res1["page_state"]["elements_count"] == 2
        assert driver.ui.dump_ui_nodes.call_count == 2

        # 2nd tap hits the preserved post-tap snapshot without a 3rd dump!
        res2 = asyncio.run(
            phone_tap_element(
                index=1,
                device_serial="dev1",
                platform="android",
                wait_ms=0,
            )
        )
        assert res2["ok"] is True
        assert res2["snapshot_hit"] is True
        assert res2["label"] == "Page2Btn1"
        assert driver.ui.dump_ui_nodes.call_count == 2


def test_tap_text_return_page_state():
    """phone_tap_text(..., return_page_state=True) returns page_state and page_state_updated=True."""
    ui_snapshot_cache.invalidate()
    driver = MagicMock()
    driver.ui.find_elements.return_value = [{"center_x": 50, "center_y": 60, "text": "Go"}]
    driver.input.tap.return_value = {"ok": True}
    driver.ui.get_current_activity.return_value = {"package": "pkg", "activity": ".Detail"}
    driver.ui.dump_ui_nodes.return_value = [_make_fake_node("DetailItem")]

    with (
        patch("phone_pilot.mcp._input_tools.get_driver", return_value=driver),
        patch("phone_pilot.mcp._page_state_tools.get_driver", return_value=driver),
    ):
        res = asyncio.run(
            phone_tap_text(
                device_serial="dev1",
                text="Go",
                platform="android",
                wait_s=0.0,
                return_page_state=True,
                wait_ms=0,
            )
        )
    assert res["ok"] is True
    assert res["page_state_updated"] is True
    assert "page_state" in res
    assert res["page_state"]["elements_count"] == 1
    assert res["page_state"]["elements"][0]["label"] == "DetailItem"


def test_get_page_state_annotate_only_returns_single_image():
    """B4: include_screenshot=False, annotate_elements=True only populates screenshot_annotated_base64."""
    ui_snapshot_cache.invalidate()
    driver = MagicMock()
    driver.ui.get_current_activity.return_value = {"package": "pkg", "activity": ".Main"}
    driver.screen.get_screen_size.return_value = (1080, 1920)
    driver.ui.dump_ui_nodes.return_value = [_make_fake_node("Btn")]
    driver.screen.screenshot.return_value = b"fakepng"

    with (
        patch(
            "phone_pilot.mcp._page_state_tools._resolve_device_serial",
            return_value=("dev1", "android", None),
        ),
        patch("phone_pilot.mcp._page_state_tools.get_driver", return_value=driver),
        patch(
            "phone_pilot.extensions.vision.annotate.annotate_elements_on_screenshot",
            return_value=b"annotated_fakepng",
        ),
    ):
        res = asyncio.run(
            phone_get_page_state(
                device_serial="dev1",
                platform="android",
                include_screenshot=False,
                annotate_elements=True,
            )
        )
    assert res["ok"] is True
    assert res["screenshot_base64"] is None
    assert res["screenshot_annotated_base64"] == base64.b64encode(
        b"annotated_fakepng"
    ).decode("ascii")


def test_encode_screenshot_b64_compression_and_fallback():
    """Verify _encode_screenshot_b64 compresses real images to JPEG and falls back gracefully on mock bytes."""
    # Fallback on non-image bytes
    assert _encode_screenshot_b64(b"fakepng", compress=True) == base64.b64encode(
        b"fakepng"
    ).decode("ascii")

    # Real image compression and downscaling
    from PIL import Image

    img = Image.new("RGBA", (2000, 1000), color=(255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_png = buf.getvalue()

    b64_compressed = _encode_screenshot_b64(raw_png, compress=True, max_side=1080, quality=75)
    decoded = base64.b64decode(b64_compressed)
    with Image.open(io.BytesIO(decoded)) as out_img:
        assert out_img.format == "JPEG"
        assert max(out_img.size) == 1080
