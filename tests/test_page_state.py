"""phone_get_page_state 工具核心逻辑测试。"""

from phone_pilot.mcp.server import _classify_element, _build_element_label


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
        assert _classify_element("android.widget.HorizontalScrollView") == "scroll_container"

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
