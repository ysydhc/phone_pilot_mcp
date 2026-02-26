"""驱动/几何/文本辅助函数（私有）/ Driver, geometry, and text helper functions (private).

底层工具函数，封装驱动调用和坐标计算。
Low-level utilities wrapping driver calls and coordinate math.
"""
from __future__ import annotations

import pathlib
import time
from typing import Optional

from phone_pilot.core.ui_node import UINode
from phone_pilot.extensions.ocr.config import normalize_ocr_lang
from phone_pilot.core.storage import recordings_root, read_json
from phone_pilot.core.ui import Box
from phone_pilot.core.log import _log as _runner_log

from .context import ScriptContext


# ---------------------------------------------------------------------------
# Low-level helpers – all delegate to ctx.driver (no platform branches)
# ---------------------------------------------------------------------------

def _get_screen_size(ctx: ScriptContext) -> Optional[tuple[int, int]]:
    if ctx.screen_size:
        return ctx.screen_size
    try:
        size = ctx.driver.screen.get_screen_size()
        ctx.screen_size = size
        return size
    except Exception:
        return None


def _input_swipe(ctx: ScriptContext, x1: int, y1: int, x2: int, y2: int, *, duration_ms: int) -> dict:
    return ctx.driver.input.swipe(int(x1), int(y1), int(x2), int(y2), duration_ms=duration_ms)


def _input_tap(ctx: ScriptContext, x: int, y: int, *, wait_s: float = 0.15) -> dict:
    return ctx.driver.input.tap(int(x), int(y), wait_s=wait_s)


def _input_keyevent(ctx: ScriptContext, keycode: str, *, wait_s: float = 0.12) -> dict:
    return ctx.driver.input.keyevent(keycode)


def _input_text(ctx: ScriptContext, text: str, *, enter: bool = False) -> dict:
    return ctx.driver.input.input_text(text, enter=enter)


def _get_current_focus(ctx: ScriptContext) -> dict:
    try:
        return ctx.driver.ui.get_current_activity()
    except Exception:
        return {"package": None, "activity": None}


def _take_screenshot(ctx: ScriptContext) -> bytes:
    """Take a screenshot via driver → PNG/JPEG bytes."""
    return ctx.driver.screen.screenshot()


def _dump_ui_nodes(ctx: ScriptContext) -> list[UINode]:
    """Dump UI nodes via driver → list of universal UINode."""
    try:
        return ctx.driver.ui.dump_ui_nodes()
    except Exception:
        return []


def _ensure_out_dir(ctx: ScriptContext) -> pathlib.Path:
    out_root = pathlib.Path(ctx.out_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    return out_root


def _box_from_bounds(bounds: tuple[int, int, int, int]) -> dict:
    x1, y1, x2, y2 = bounds
    w = max(0, int(x2 - x1))
    h = max(0, int(y2 - y1))
    cx = int(x1 + w / 2)
    cy = int(y1 + h / 2)
    return {
        "x": int(x1),
        "y": int(y1),
        "x2": int(x2),
        "y2": int(y2),
        "w": w,
        "h": h,
        "center_x": cx,
        "center_y": cy,
    }


def _roi_from_box(ctx: ScriptContext, box: Box) -> Optional[tuple[int, int, int, int]]:
    if not isinstance(box, Box):
        return None
    screen = _get_screen_size(ctx)
    if not screen:
        return None
    sw, sh = screen
    x1 = int(round(float(box.x_pct) * sw))
    y1 = int(round(float(box.y_pct) * sh))
    w = int(round(float(box.width) * sw))
    h = int(round(float(box.height) * sh))
    x2 = x1 + w
    y2 = y1 + h
    return (max(0, x1), max(0, y1), max(1, x2), max(1, y2))


def _box_center(box: dict) -> tuple[int, int]:
    cx = box.get("center_x")
    cy = box.get("center_y")
    if cx is None or cy is None:
        if "x2" in box and "y2" in box:
            x1 = int(box.get("x", 0))
            y1 = int(box.get("y", 0))
            x2 = int(box.get("x2", x1))
            y2 = int(box.get("y2", y1))
            return (int((x1 + x2) / 2), int((y1 + y2) / 2))
        x = int(box.get("x", 0))
        y = int(box.get("y", 0))
        w = int(box.get("w", 0))
        h = int(box.get("h", 0))
        return (int(x + w / 2), int(y + h / 2))
    return (int(cx), int(cy))


def _normalize_text(s: str, *, case_sensitive: bool = False) -> str:
    text = str(s or "")
    if not case_sensitive:
        text = text.lower()
    return "".join(text.split())


def _device_locales_from_protocol(ctx: ScriptContext) -> list[str]:
    """
    Best-effort read device locales from persisted device profile.
    Uses stored device info file (platform-agnostic).
    """
    if not ctx.device_serial:
        return []
    try:
        out_root = recordings_root(ctx.out_dir)
        # Convention: device info JSON is at <recordings>/Android_device_info.json
        # or <recordings>/device_info.json.
        for name in ("Android_device_info.json", "device_info.json"):
            info_path = out_root / name
            if info_path.exists():
                break
        else:
            return []
        data = read_json(info_path)
    except Exception:
        return []
    devices = data.get("devices") if isinstance(data, dict) else None
    if not isinstance(devices, dict):
        return []
    prof = devices.get(ctx.device_serial) or {}
    locales = (
        prof.get("locales")
        or prof.get("locale")
        or prof.get("language")
        or prof.get("languages")
    )
    if isinstance(locales, dict):
        locales = locales.get("locales") or locales.get("primary")
    if isinstance(locales, str):
        return [locales]
    if isinstance(locales, list):
        return [str(x) for x in locales if str(x).strip()]
    return []


def _locale_to_ocr_langs(locale: str) -> list[str]:
    lc = (locale or "").lower()
    if not lc:
        return []
    if lc.startswith("zh"):
        if "hant" in lc or "tw" in lc or "hk" in lc:
            return ["chi_tra"]
        return ["chi_sim"]
    if lc.startswith("ja"):
        return ["jpn"]
    if lc.startswith("en"):
        return ["eng"]
    if lc.startswith("ko"):
        return ["kor"]
    if lc.startswith("ar"):
        return ["ara"]
    if lc.startswith("vi"):
        return ["vie"]
    if lc.startswith("ms"):
        return ["msa"]
    if lc.startswith("id"):
        return ["ind"]
    return []


def _expand_ocr_langs(ctx: ScriptContext, base_lang: Optional[str]) -> list[str]:
    langs: list[str] = []
    if base_lang:
        langs.append(normalize_ocr_lang(base_lang))

    defaults = [
        "jpn+chi_sim+eng",
        "jpn+chi_sim",
        "jpn+eng",
        "chi_sim+eng",
        "eng",
    ]
    for d in defaults:
        if d not in langs:
            langs.append(d)

    for loc in _device_locales_from_protocol(ctx):
        for mapped in _locale_to_ocr_langs(loc):
            if mapped not in langs:
                langs.append(mapped)
            combo = "+".join([mapped, "eng"])
            if combo not in langs:
                langs.append(combo)
    return langs


# ---------------------------------------------------------------------------
# auto_log 内部辅助
# ---------------------------------------------------------------------------

def _auto_log_print(msg: str) -> None:
    """内部日志打印（带时间戳），同时写入 console.log。"""
    ts = time.strftime("%H:%M:%S")
    _runner_log(f"[{ts}] {msg}")
