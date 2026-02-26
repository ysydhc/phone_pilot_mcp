from __future__ import annotations

import re as _re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from phone_pilot.core.resource import res_add

_Op = Callable[..., Any]


def _clamp_pct(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


@dataclass
class UIElement:
    """UI 元素抽象，表示屏幕上的可交互控件。
    UI element abstraction for on-screen interactive controls.

    由 find_text/find_image 等返回，支持 tap()、extract()、方位查找等。
    Returned by find_text/find_image; supports tap(), extract(), directional find.

    字段 / Fields:
        x, y, width, height: 元素 bounds（像素）/ Element bounds in pixels
        desc: 内容描述（content-desc）/ Content description
        resource_id: 资源 ID / Resource ID
        clickable, enabled, focusable, selectable, selected: 控件状态 / Control state
        kind: 控件类型（如 class_name）/ Control type
        meta: 额外元数据 / Extra metadata

    常用方法 / Key methods:
        tap(wait=N): 点击元素中心，可选等待 N 秒 / Tap center, optional wait
        extract(pattern, cast): 从 display_text 用正则提取 / Extract from text via regex
        summary(): 人类可读描述 / Human-readable description
        display_text: 可读文本（text > desc > resource_id）/ Readable text
        right/left/up/down(): 在元素某侧查找 / Find relative to element
    """

    x: Optional[int] = None
    y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    desc: Optional[str] = None
    resource_id: Optional[str] = None
    clickable: Optional[bool] = None
    enabled: Optional[bool] = None
    focusable: Optional[bool] = None
    selectable: Optional[bool] = None
    selected: Optional[bool] = None
    kind: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)
    _ops: dict[str, _Op] = field(default_factory=dict, repr=False, compare=False)
    _locator: Optional[dict] = field(default=None, repr=False, compare=False)

    def bounds(self) -> Optional[tuple[int, int, int, int]]:
        if self.x is None or self.y is None or self.width is None or self.height is None:
            return None
        x1 = int(self.x)
        y1 = int(self.y)
        x2 = x1 + int(self.width)
        y2 = y1 + int(self.height)
        return x1, y1, x2, y2

    def center(self) -> Optional[tuple[int, int]]:
        bounds = self.bounds()
        if not bounds:
            return None
        x1, y1, x2, y2 = bounds
        return int(x1 + (x2 - x1) / 2), int(y1 + (y2 - y1) / 2)

    # ---- display helpers ----

    @property
    def display_text(self) -> str:
        """统一获取元素可读文本 / Get readable text.

        优先级：text > desc > resource_id。用于日志、extract() 等。
        Priority: text > desc > resource_id. For logging, extract(), etc.
        """
        for v in (getattr(self, "text", None), self.desc, self.resource_id):
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    def summary(self) -> str:
        """返回人类可读的简短描述 / Human-readable short description.

        格式如 '文本' @ (x, y)，用于日志和调试。
        Format: 'text' @ (x, y). For logging and debugging.
        """
        c = self.center()
        return f"'{self.display_text}' @ {c}" if c else f"'{self.display_text}'"

    def __str__(self) -> str:
        return self.summary()

    def extract(self, pattern: str, cast: type = str) -> Any:
        """从 display_text 中用正则提取第一个捕获组，可选类型转换。
        Extract first capture group from display_text via regex, optional cast.

        Parameters / 参数:
            pattern: 正则表达式（含捕获组）/ Regex with capture group
            cast: 转换类型，默认 str / Cast type (default str)

        Returns / 返回值:
            提取并转换后的值，失败返回 None / Extracted value or None

        Example / 示例:
            elem.extract(r"(\\d+)/5回", int)   # -> 3
        """
        m = _re.search(pattern, self.display_text)
        if not m:
            return None
        try:
            return cast(m.group(1))
        except (ValueError, IndexError):
            return None

    def to_dict(self) -> dict:
        data = {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "desc": self.desc,
            "resource_id": self.resource_id,
            "clickable": self.clickable,
            "enabled": self.enabled,
            "focusable": self.focusable,
            "selectable": self.selectable,
            "selected": self.selected,
            "kind": self.kind,
        }
        if isinstance(self, TextElement):
            data["text"] = self.text
            data["hint"] = self.hint
        if isinstance(self, ImageElement):
            data["src"] = self.src
            data["match_src"] = self.match_src
        if self.meta:
            data["meta"] = dict(self.meta)
        return data

    def find(self) -> "UIElement":
        return self

    def right(
        self,
        text: Optional[str] = None,
        *,
        texts: Optional[list[str]] = None,
        image: Optional[str] = None,
        rule: str = "band",
        x_tol: int = 80,
        y_tol: int = 40,
        expand_step: int = 20,
        expand_max: int = 200,
        lang: Optional[str] = None,
        exact: bool = False,
        case_sensitive: bool = False,
        psm: int = 6,
        threshold: float = 0.8,
        grayscale: bool = True,
    ) -> Optional["UIElement"]:
        return self._relative(
            direction="right",
            text=text,
            texts=texts,
            image=image,
            rule=rule,
            x_tol=x_tol,
            y_tol=y_tol,
            expand_step=expand_step,
            expand_max=expand_max,
            lang=lang,
            exact=exact,
            case_sensitive=case_sensitive,
            psm=psm,
            threshold=threshold,
            grayscale=grayscale,
        )

    def left(self, text: Optional[str] = None, **kwargs: Any) -> Optional["UIElement"]:
        return self._relative(direction="left", text=text, **kwargs)

    def up(self, text: Optional[str] = None, **kwargs: Any) -> Optional["UIElement"]:
        return self._relative(direction="up", text=text, **kwargs)

    def down(self, text: Optional[str] = None, **kwargs: Any) -> Optional["UIElement"]:
        return self._relative(direction="down", text=text, **kwargs)

    def _relative(
        self,
        *,
        direction: str,
        text: Optional[str] = None,
        texts: Optional[list[str]] = None,
        image: Optional[str] = None,
        rule: str = "band",
        x_tol: int = 80,
        y_tol: int = 40,
        expand_step: int = 20,
        expand_max: int = 200,
        lang: Optional[str] = None,
        exact: bool = False,
        case_sensitive: bool = False,
        psm: int = 6,
        threshold: float = 0.8,
        grayscale: bool = True,
    ) -> Optional["UIElement"]:
        op = self._op("relative")
        if not op:
            return None
        return op(
            self,
            direction=direction,
            text=text,
            texts=texts,
            image=image,
            rule=rule,
            x_tol=x_tol,
            y_tol=y_tol,
            expand_step=expand_step,
            expand_max=expand_max,
            lang=lang,
            exact=exact,
            case_sensitive=case_sensitive,
            psm=psm,
            threshold=threshold,
            grayscale=grayscale,
        )

    @classmethod
    def from_box(cls, box: dict, **kwargs: Any) -> "UIElement":
        x1 = box.get("x")
        y1 = box.get("y")
        w = box.get("w")
        h = box.get("h")
        if w is None and x1 is not None and box.get("x2") is not None:
            w = int(box.get("x2")) - int(x1)
        if h is None and y1 is not None and box.get("y2") is not None:
            h = int(box.get("y2")) - int(y1)
        return cls(
            x=int(x1) if x1 is not None else None,
            y=int(y1) if y1 is not None else None,
            width=int(w) if w is not None else None,
            height=int(h) if h is not None else None,
            **kwargs,
        )

    def _op(self, name: str) -> Optional[_Op]:
        return self._ops.get(name)

    def _sync_from(self, other: "UIElement") -> None:
        for field_name in (
            "x",
            "y",
            "width",
            "height",
            "desc",
            "resource_id",
            "clickable",
            "enabled",
            "focusable",
            "selectable",
            "selected",
            "kind",
        ):
            setattr(self, field_name, getattr(other, field_name, None))
        if isinstance(self, TextElement) and isinstance(other, TextElement):
            self.text = other.text
            self.hint = other.hint
        if isinstance(self, ImageElement) and isinstance(other, ImageElement):
            self.src = other.src
            self.match_src = other.match_src
        if other.meta:
            self.meta.update(other.meta)

    def _point_from_pct(self, pc_x: Optional[float], pc_y: Optional[float]) -> Optional[tuple[int, int]]:
        bounds = self.bounds()
        if not bounds:
            return None
        x1, y1, x2, y2 = bounds
        px = _clamp_pct(pc_x)
        py = _clamp_pct(pc_y)
        if px is None and py is None:
            return self.center()
        if px is None:
            px = 0.5
        if py is None:
            py = 0.5
        x = int(x1 + (x2 - x1) * px)
        y = int(y1 + (y2 - y1) * py)
        return x, y

    def tap(
        self,
        pc_x: Optional[float] = None,
        pc_y: Optional[float] = None,
        times: int = 1,
        interval: int = 0,
        wait: float = 0,
    ) -> dict:
        """点击元素 / Tap element.

        默认点击元素中心。pc_x/pc_y 为 0–1 时按比例偏移（如 0.5 为中心）。
        Taps element center by default. pc_x/pc_y 0–1 = offset from top-left.

        Parameters / 参数:
            pc_x, pc_y: 点击点相对元素的比例（None=中心）/ Tap point ratio (None=center)
            times: 点击次数 / Tap count
            interval: 多次点击间隔毫秒 / Interval between taps (ms)
            wait: 点击后等待秒数 / Wait after tap (seconds)

        Returns / 返回值:
            dict: {"ok": bool, "x": int, "y": int, "times": int, "results": [...]}
        """
        tap_xy = self._op("tap_xy")
        if not tap_xy:
            return {"ok": False, "error": "tap_unavailable"}
        point = self._point_from_pct(pc_x, pc_y)
        if not point:
            return {"ok": False, "error": "no_bounds"}
        x, y = point
        count = max(1, int(times))
        results: list[dict] = []
        for i in range(count):
            res = tap_xy(int(x), int(y))
            results.append(res if isinstance(res, dict) else {"ok": False, "error": "tap_failed"})
            if interval and i < (count - 1):
                time.sleep(max(0, int(interval)) / 1000.0)
        ok = all(bool(r.get("ok")) for r in results)
        if wait > 0:
            time.sleep(float(wait))
        return {"ok": ok, "x": x, "y": y, "times": count, "results": results}

    def scroll(self, pc_x: Optional[float] = None, pc_y: Optional[float] = None, *, max_attempts: int = 2) -> dict:
        swipe = self._op("swipe")
        get_screen = self._op("get_screen_size")
        if not swipe or not get_screen:
            return {"ok": False, "error": "scroll_unavailable"}
        bounds = self.bounds()
        if not bounds:
            return {"ok": False, "error": "no_bounds"}
        x1, y1, _, _ = bounds
        screen = get_screen()
        if not screen:
            return {"ok": False, "error": "screen_size_unavailable"}
        sw, sh = screen
        target_x = x1 if pc_x is None else int(round(float(pc_x) * sw))
        target_y = y1 if pc_y is None else int(round(float(pc_y) * sh))
        tolerance = 5
        last_res = None
        for _ in range(max(1, int(max_attempts))):
            last_res = swipe(int(x1), int(y1), int(target_x), int(target_y))
            refreshed = self.refresh()
            if refreshed and refreshed.x is not None and refreshed.y is not None:
                dx = abs(int(refreshed.x) - int(target_x)) if pc_x is not None else 0
                dy = abs(int(refreshed.y) - int(target_y)) if pc_y is not None else 0
                if dx <= tolerance and dy <= tolerance:
                    return {"ok": True, "result": last_res}
        return {"ok": False, "error": "scroll_not_aligned", "result": last_res}

    def zoom(
        self,
        pc_x: Optional[float] = None,
        pc_y: Optional[float] = None,
        zoom_size: float = 0.0,
    ) -> dict:
        if zoom_size == 0:
            return {"ok": True, "method": "noop"}
        bounds = self.bounds()
        if not bounds:
            return {"ok": False, "error": "no_bounds"}
        point = self._point_from_pct(pc_x, pc_y)
        if not point:
            return {"ok": False, "error": "no_bounds"}
        cx, cy = point
        air_pinch = self._op("air_pinch")
        if air_pinch:
            try:
                res = air_pinch(int(cx), int(cy), float(zoom_size))
                if isinstance(res, dict) and res.get("ok"):
                    res.setdefault("method", "airtest")
                    return res
            except Exception as exc:
                return {"ok": False, "error": "airtest_pinch_failed", "detail": str(exc)}
        swipe = self._op("swipe")
        if not swipe:
            return {"ok": False, "error": "zoom_unavailable"}
        _, _, x2, y2 = bounds
        delta = int(min(abs(x2 - cx), abs(y2 - cy), max(self.width or 0, 1), max(self.height or 0, 1)) * abs(zoom_size) / 2)
        delta = max(10, delta)
        if zoom_size > 0:
            r1 = swipe(int(cx), int(cy), int(cx - delta), int(cy))
            r2 = swipe(int(cx), int(cy), int(cx + delta), int(cy))
        else:
            r1 = swipe(int(cx - delta), int(cy), int(cx), int(cy))
            r2 = swipe(int(cx + delta), int(cy), int(cx), int(cy))
        ok = bool(r1.get("ok")) and bool(r2.get("ok")) if isinstance(r1, dict) and isinstance(r2, dict) else False
        return {"ok": ok, "method": "adb", "results": [r1, r2]}

    def refresh(self) -> Optional["UIElement"]:
        locate = self._op("locate")
        if not locate or not self._locator:
            return None
        fresh = locate(self._locator)
        if isinstance(fresh, UIElement):
            self._sync_from(fresh)
            return self
        return None

    def exists(self) -> bool:
        return self.refresh() is not None

    def wait(self, timeout_s: float = 5.0, interval_s: float = 0.4) -> Optional["UIElement"]:
        end_at = time.time() + max(0.1, float(timeout_s))
        while time.time() <= end_at:
            hit = self.refresh()
            if hit:
                return hit
            time.sleep(max(0.05, float(interval_s)))
        return None

    def wait_visible(self, timeout_s: float = 5.0, interval_s: float = 0.4) -> Optional["UIElement"]:
        end_at = time.time() + max(0.1, float(timeout_s))
        while time.time() <= end_at:
            hit = self.refresh()
            if hit and (hit.enabled is None or bool(hit.enabled)):
                return hit
            time.sleep(max(0.05, float(interval_s)))
        return None

    def _candidate_texts(self) -> list[str]:
        vals = []
        for v in (getattr(self, "text", None), getattr(self, "hint", None), self.desc, self.resource_id):
            if isinstance(v, str) and v.strip():
                vals.append(v.strip())
        return vals

    def contains(self, text: str, *, case_sensitive: bool = False) -> bool:
        query = str(text or "")
        if not case_sensitive:
            query = query.lower()
        for cand in self._candidate_texts():
            c = cand if case_sensitive else cand.lower()
            if query and query in c:
                return True
        return False

    def match_text(self, text: str, *, exact: bool = True, case_sensitive: bool = False) -> bool:
        query = str(text or "")
        if not case_sensitive:
            query = query.lower()
        for cand in self._candidate_texts():
            c = cand if case_sensitive else cand.lower()
            if exact and query == c:
                return True
            if not exact and query in c:
                return True
        return False

    def screenshot_crop(self, *, name: Optional[str] = None, out_dir: Optional[str] = None) -> dict:
        snap = self._op("screenshot")
        if not snap:
            return {"ok": False, "error": "screenshot_unavailable"}
        bounds = self.bounds()
        if not bounds:
            return {"ok": False, "error": "no_bounds"}
        res = snap(str(name) if name else "uielement_crop")
        if not (isinstance(res, dict) and res.get("ok") and res.get("path")):
            return {"ok": False, "error": "screenshot_failed"}
        src_path = str(res.get("path"))
        try:
            import cv2
        except Exception as exc:
            return {"ok": False, "error": "cv2_unavailable", "detail": str(exc)}
        x1, y1, x2, y2 = bounds
        img = cv2.imread(src_path, cv2.IMREAD_COLOR)
        if img is None:
            return {"ok": False, "error": "screenshot_decode_failed"}
        h, w = img.shape[:2]
        x1 = max(0, min(x1, w - 1))
        y1 = max(0, min(y1, h - 1))
        x2 = max(x1 + 1, min(x2, w))
        y2 = max(y1 + 1, min(y2, h))
        crop = img[y1:y2, x1:x2]
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            ok = cv2.imwrite(tmp.name, crop)
            tmp_path = tmp.name
        if not ok:
            return {"ok": False, "error": "crop_write_failed"}
        res_add_result = res_add(tmp_path, out_dir=out_dir or self.meta.get("out_dir"))
        return {"ok": True, "path": tmp_path, "resource": res_add_result}

    def info(self) -> dict:
        info_op = self._op("uia_info")
        if info_op:
            res = info_op(self)
            if isinstance(res, dict):
                return res
        meta_info = self.meta.get("uia")
        return meta_info if isinstance(meta_info, dict) else {}


@dataclass
class TextElement(UIElement):
    text: Optional[str] = None
    hint: Optional[str] = None


@dataclass
class ImageElement(UIElement):
    src: Optional[str] = None
    match_src: Optional[str] = None


@dataclass(frozen=True)
class Box:
    x_pct: float
    y_pct: float
    width: float
    height: float

    # ---- 快捷构造方法 ----

    @staticmethod
    def bottom(height: float = 0.15) -> "Box":
        """屏幕底部区域（如导航栏）。"""
        return Box(x_pct=0.0, y_pct=1.0 - height, width=1.0, height=height)

    @staticmethod
    def top(height: float = 0.15) -> "Box":
        """屏幕顶部区域（如状态栏/标题栏）。"""
        return Box(x_pct=0.0, y_pct=0.0, width=1.0, height=height)

    @staticmethod
    def left_side(width: float = 0.3) -> "Box":
        """屏幕左侧区域。"""
        return Box(x_pct=0.0, y_pct=0.0, width=width, height=1.0)

    @staticmethod
    def right_side(width: float = 0.3) -> "Box":
        """屏幕右侧区域。"""
        return Box(x_pct=1.0 - width, y_pct=0.0, width=width, height=1.0)


class UIElementList:
    def __init__(self, items: list[UIElement]):
        self._items = list(items or [])

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def for_(self, fn):
        for item in self._items:
            fn(item)
        return self

    def for_each(self, fn):
        return self.for_(fn)

    def filter(self, fn):
        return UIElementList([item for item in self._items if fn(item)])

    def find_first(self) -> Optional[UIElement]:
        return self._items[0] if self._items else None

    def find_last(self) -> Optional[UIElement]:
        return self._items[-1] if self._items else None

    def find_index(self, index: int) -> Optional[UIElement]:
        try:
            return self._items[int(index)]
        except Exception:
            return None

    def to_list(self) -> list[UIElement]:
        return list(self._items)

    def __getattr__(self, name: str):
        if name == "for":
            return self.for_
        raise AttributeError(name)
