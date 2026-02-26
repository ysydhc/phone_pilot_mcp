from phone_pilot.core.ui.element import ImageElement, TextElement, UIElement


def test_uielement_fields_and_dict() -> None:
    elem = UIElement(
        x=10,
        y=20,
        width=30,
        height=40,
        desc="desc",
        resource_id="res_id",
        clickable=True,
        enabled=False,
        focusable=True,
        selectable=False,
        selected=True,
    )
    data = elem.to_dict()
    assert data["x"] == 10
    assert data["y"] == 20
    assert data["width"] == 30
    assert data["height"] == 40
    assert data["desc"] == "desc"
    assert data["resource_id"] == "res_id"
    assert data["clickable"] is True
    assert data["enabled"] is False
    assert data["focusable"] is True
    assert data["selectable"] is False
    assert data["selected"] is True


def test_text_element_fields() -> None:
    elem = TextElement(text="hello", hint="tip")
    data = elem.to_dict()
    assert data["text"] == "hello"
    assert data["hint"] == "tip"


def test_image_element_fields() -> None:
    elem = ImageElement(src="/tmp/a.png", match_src="/tmp/b.png")
    data = elem.to_dict()
    assert data["src"] == "/tmp/a.png"
    assert data["match_src"] == "/tmp/b.png"


def test_relative_calls_op() -> None:
    calls = {}

    def _relative(element, **kwargs):
        calls["direction"] = kwargs.get("direction")
        return TextElement(text="ok")

    anchor = UIElement()
    anchor._ops = {"relative": _relative}
    out = anchor.right("target")
    assert isinstance(out, TextElement)
    assert calls["direction"] == "right"


def test_find_text_returns_uielement(monkeypatch) -> None:
    from phone_pilot import script_api as api
    from phone_pilot.script_api import find as _find_mod

    ctx = api.ScriptContext(device_serial="dummy")

    def _fake_uia(*args, **kwargs):
        return [{"x": 1, "y": 2, "w": 3, "h": 4, "text": "hello", "uia": {}}]

    monkeypatch.setattr(_find_mod, "_uia_find_text_boxes", _fake_uia)
    elem = api.find_text(ctx, "hello")
    assert isinstance(elem, UIElement)
    assert elem.text == "hello"
    assert elem.x == 1

    monkeypatch.setattr(_find_mod, "_uia_find_text_boxes", lambda *a, **k: [])
    monkeypatch.setattr(_find_mod, "_ocr_find_text_boxes", lambda *a, **k: [])
    monkeypatch.setattr(_find_mod, "_expand_ocr_langs", lambda *a, **k: [])
    assert api.find_text(ctx, "hello") is None


def test_find_image_returns_uielement(monkeypatch) -> None:
    from phone_pilot import script_api as api
    from phone_pilot.script_api import find as _find_mod

    ctx = api.ScriptContext(device_serial="dummy")

    class _DummyAir:
        class Template:
            def __init__(self, path, threshold=0.8):
                self.path = path
                self.threshold = threshold

        def exists(self, _tpl):
            return (10, 20)

    monkeypatch.setattr(ctx, "ensure_air", lambda: _DummyAir())
    monkeypatch.setattr(_find_mod, "_template_size", lambda _p: (10, 20))
    elem = api.find_image(ctx, "/tmp/dummy.png")
    assert isinstance(elem, UIElement)
    assert elem.kind == "image"

    class _NoMatchAir:
        class Template:
            def __init__(self, path, threshold=0.8):
                self.path = path
                self.threshold = threshold

        def exists(self, _tpl):
            return None

    monkeypatch.setattr(ctx, "ensure_air", lambda: _NoMatchAir())
    monkeypatch.setattr(_find_mod, "_ocr_texts_from_image", lambda *a, **k: [])
    monkeypatch.setattr(_find_mod, "_ocr_find_text_boxes", lambda *a, **k: [])
    assert api.find_image(ctx, "/tmp/dummy.png") is None


def test_find_text_regex_and_box(monkeypatch) -> None:
    from phone_pilot import script_api as api
    from phone_pilot.script_api import find as _find_mod, _helpers as _helpers_mod
    from phone_pilot.core.ui import Box

    ctx = api.ScriptContext(device_serial="dummy")
    monkeypatch.setattr(_helpers_mod, "_get_screen_size", lambda *_: (100, 100))
    calls = {}

    def _uia_find_text_boxes(*_a, **kwargs):
        calls["regex"] = kwargs.get("regex")
        calls["roi"] = kwargs.get("roi")
        return [{"x": 1, "y": 2, "w": 3, "h": 4, "text": "123"}]

    monkeypatch.setattr(_find_mod, "_uia_find_text_boxes", _uia_find_text_boxes)
    elem = api.find_text(ctx, "re:^\\d+$", box=Box(0.1, 0.1, 0.5, 0.5), use_ocr=False)
    assert elem is not None
    assert calls["regex"] is not None
    assert calls["roi"] is not None


def test_uielement_list_methods() -> None:
    from phone_pilot.core.ui.element import UIElementList

    items = UIElementList([UIElement(x=1), UIElement(x=2), UIElement(x=3)])
    assert items.find_first().x == 1
    assert items.find_last().x == 3
    assert items.find_index(1).x == 2
    assert len(items.filter(lambda e: e.x and e.x > 1)) == 2


def test_find_text_list(monkeypatch) -> None:
    from phone_pilot import script_api as api
    from phone_pilot.script_api import find as _find_mod

    ctx = api.ScriptContext(device_serial="dummy")

    monkeypatch.setattr(
        _find_mod,
        "_uia_find_text_boxes",
        lambda *_a, **_k: [{"x": 1, "y": 2, "w": 3, "h": 4, "text": "hello"}],
    )
    lst = api.find_text_list(ctx, "hello", use_ocr=False)
    assert len(lst) == 1
    assert lst.find_first().text == "hello"


def test_tap_scroll_zoom_basic() -> None:
    taps: list[tuple[int, int]] = []
    swipes: list[tuple[int, int, int, int]] = []

    def _tap_xy(x, y):
        taps.append((x, y))
        return {"ok": True, "x": x, "y": y}

    def _swipe(x1, y1, x2, y2):
        swipes.append((x1, y1, x2, y2))
        return {"ok": True}

    def _screen_size():
        return (100, 100)

    def _locate(_locator):
        return UIElement(x=10, y=50, width=10, height=10)

    elem = UIElement(x=10, y=10, width=10, height=10)
    elem._ops = {
        "tap_xy": _tap_xy,
        "swipe": _swipe,
        "get_screen_size": _screen_size,
        "locate": _locate,
        "air_pinch": lambda cx, cy, zoom_size: {"ok": True},
    }
    elem._locator = {"type": "text", "query": "dummy"}

    tap_res = elem.tap(times=2)
    assert tap_res["ok"] is True
    assert len(taps) == 2

    scroll_res = elem.scroll(pc_y=0.5)
    assert scroll_res["ok"] is True
    assert swipes

    zoom_res = elem.zoom(zoom_size=0.5)
    assert zoom_res["ok"] is True
    assert zoom_res["method"] == "airtest"


def test_exists_wait_contains_info_screenshot(tmp_path) -> None:
    calls = {"count": 0}

    def _locate(_locator):
        calls["count"] += 1
        if calls["count"] < 2:
            return None
        return TextElement(x=1, y=2, width=3, height=4, text="hello", desc="greet", resource_id="id1")

    def _screenshot(_name: str):
        import cv2
        import numpy as np

        img = np.zeros((20, 20, 3), dtype=np.uint8)
        path = tmp_path / "screen.png"
        cv2.imwrite(str(path), img)
        return {"ok": True, "path": str(path)}

    elem_exists = TextElement(x=1, y=2, width=3, height=4, text="hello")
    elem_exists._locator = {"type": "text", "query": "hello"}
    elem_exists._ops = {"locate": lambda _l: TextElement(x=1, y=2, width=3, height=4, text="hello"), "screenshot": _screenshot, "uia_info": lambda _e: {"class": "Text"}}

    elem_wait = TextElement(x=1, y=2, width=3, height=4, text="hello")
    elem_wait._locator = {"type": "text", "query": "hello"}
    elem_wait._ops = {"locate": _locate, "screenshot": _screenshot, "uia_info": lambda _e: {"class": "Text"}}

    assert elem_exists.exists() is True
    assert elem_wait.wait(timeout_s=1.0, interval_s=0.1) is not None
    assert elem_exists.contains("hell") is True
    assert elem_exists.match_text("hello") is True
    assert elem_exists.info().get("class") == "Text"

    crop = elem_exists.screenshot_crop()
    assert crop["ok"] is True
