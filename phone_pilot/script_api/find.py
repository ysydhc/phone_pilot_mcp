"""公开查找 API / Public find API.

find_text, find_image, find_text_list, find_image_list —
脚本中最常用的元素查找函数。
The most commonly used element-finding functions in scripts.
"""
from __future__ import annotations

import pathlib
import re
import time
from typing import Optional

from phone_pilot.core.ui import Box, UIElement, UIElementList
from phone_pilot.core.log import _log as _runner_log

from .context import ScriptContext
from ._helpers import _normalize_text, _roi_from_box, _expand_ocr_langs, _box_center
from ._ocr import (
    _ocr_find_text_boxes,
    _ocr_find_text_boxes_roi,
    _ocr_find_text_boxes_regex,
    _ocr_texts_from_image,
)
from ._locate import (
    _uia_find_text_boxes,
    _airtest_find_image_boxes,
    _template_size,
    _ui_element_from_box,
)


# ---------------------------------------------------------------------------
# 自愈辅助 / Self-healing helpers
# ---------------------------------------------------------------------------

def _try_self_heal(ctx: ScriptContext, action: str, query: str, error: str, retry_count: int) -> Optional[dict]:
    """尝试自愈并返回 HealAction 信息（或 None）。
    Try self-healing and return HealAction info (or None).
    """
    try:
        from .self_healer import get_self_healer
        healer = get_self_healer(ctx)
        if healer:
            heal = healer.try_heal(action, query, error, retry_count=retry_count)
            if heal:
                return {
                    "fix_type": heal.fix_type,
                    "fix_detail": heal.fix_detail,
                    "source": heal.source,
                    "confidence": heal.confidence,
                    "record_id": heal.record_id,
                    "_heal_action": heal,
                }
    except Exception:
        pass
    return None


def _report_heal_outcome(ctx: ScriptContext, heal_info: dict, success: bool) -> None:
    """反馈自愈结果 / Report self-healing outcome."""
    try:
        from .self_healer import get_self_healer
        healer = get_self_healer(ctx)
        if healer and "_heal_action" in heal_info:
            healer.report_outcome(heal_info["_heal_action"], success)
    except Exception:
        pass


def find_image(
    ctx: ScriptContext,
    path: str,
    *,
    threshold: float = 0.8,
    ocr_lang: Optional[str] = None,
    ocr_exact: bool = False,
    ocr_case_sensitive: bool = False,
    ocr_psm: int = 6,
    retry_interval_ms: int = 1000,
    retry_attempts: int = 3,
    box: Optional[Box] = None,
) -> Optional[UIElement]:
    """在屏幕上查找图像元素。
    Find an image element on screen.

    优先通过 Airtest 模板匹配（Android），失败时降级为 OCR 识别图像中的文字。
    支持 @res: 资源引用和相对路径（从脚本目录解析）。
    Prioritizes Airtest template matching (Android), falls back to OCR on image text.
    Supports @res: resource refs and relative paths (resolved from script dir).

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        path: 图像路径，支持 @res:key 或相对路径 / Image path, @res:key or relative
        threshold: 模板匹配阈值 0–1，默认 0.8 / Template match threshold (default 0.8)
        ocr_lang: OCR 语言（chi_sim/eng/jpn 等）/ OCR language
        ocr_exact: OCR 是否精确匹配 / OCR exact match
        ocr_case_sensitive: OCR 是否区分大小写 / OCR case sensitive
        ocr_psm: Tesseract PSM 模式 / Tesseract PSM
        retry_interval_ms: 重试间隔毫秒 / Retry interval in ms
        retry_attempts: 重试次数 / Retry attempts
        box: 限定搜索区域（Box 百分比坐标）/ Search region (Box percent coords)

    Returns / 返回值:
        UIElement: 找到的元素，可 .tap() / Found element, supports .tap()
        None: 未找到 / None if not found

    Notes / 特殊逻辑:
        - 相对路径会从脚本所在目录解析
        - 截图/步骤会写入 RunSession（若 ctx.session 存在）
    """
    # 解析 @res: 资源引用 → 实际文件路径
    from phone_pilot.core.resource import is_res_ref, resolve_or_cache_path, res_get, resource_cache_dir
    resolved_path = path
    if is_res_ref(path):
        key = path[len("@res:"):].strip()
        try:
            resolved_path = resolve_or_cache_path(path, out_dir=ctx.out_dir)
        except Exception:
            # 查询资源索引获取详情，帮助用户定位问题
            info = res_get(key, out_dir=ctx.out_dir)
            cache_dir = resource_cache_dir(ctx.out_dir)
            if info.get("ok"):
                record = info.get("record", {})
                origin = record.get("origin_path", "未知")
                cached = record.get("cached_path", "未知")
                _runner_log(f"[WARN] 资源 {path} 已注册但文件可能缺失:")
                _runner_log(f"       原始文件: {origin}")
                _runner_log(f"       缓存文件: {cached}")
            else:
                _runner_log(f"[WARN] 资源 {path} 未在索引中注册")
                _runner_log(f"       索引目录: {cache_dir}")
                _runner_log("       请先通过 MCP res_add 或手动注册资源")
    elif not pathlib.Path(path).expanduser().is_absolute():
        # 相对路径 → 尝试从脚本目录或 cwd 解析
        candidate = pathlib.Path(path).expanduser()
        if not candidate.exists() and ctx.script_path:
            script_dir = pathlib.Path(ctx.script_path).parent
            alt = script_dir / path
            if alt.exists():
                resolved_path = str(alt)
        if not pathlib.Path(resolved_path).exists():
            try:
                resolved_path = resolve_or_cache_path(path, out_dir=ctx.out_dir)
            except Exception:
                pass

    def _find_once() -> Optional[UIElement]:
        region = box
        pos = None
        if ctx.driver.platform == "android":
            try:
                air = ctx.ensure_air()
                tpl = air.Template(str(resolved_path), threshold=float(threshold))
                pos = air.exists(tpl)
            except Exception:
                pass
        if not pos:
            texts = _ocr_texts_from_image(str(resolved_path), lang=ocr_lang, psm=int(ocr_psm))
            roi = _roi_from_box(ctx, region) if region else None
            if roi:
                boxes = _ocr_find_text_boxes_roi(
                    ctx,
                    texts=texts,
                    roi=roi,
                    lang=ocr_lang,
                    exact=bool(ocr_exact),
                    case_sensitive=bool(ocr_case_sensitive),
                    psm=int(ocr_psm),
                )
            else:
                boxes = _ocr_find_text_boxes(
                    ctx,
                    texts=texts,
                    lang=ocr_lang,
                    exact=bool(ocr_exact),
                    case_sensitive=bool(ocr_case_sensitive),
                    psm=int(ocr_psm),
                )
            if not boxes:
                return None
            match_box = boxes[0]
            locator = {
                "type": "image",
                "query": str(path),
                "image": str(path),
                "threshold": float(threshold),
                "ocr_lang": ocr_lang,
                "ocr_exact": bool(ocr_exact),
                "ocr_case_sensitive": bool(ocr_case_sensitive),
                "ocr_psm": int(ocr_psm),
            }
            return _ui_element_from_box(
                ctx,
                match_box,
                kind="image",
                query=str(path),
                method="ocr",
                image_src=str(path),
                match_src=str(path),
                locator=locator,
            )
        roi = _roi_from_box(ctx, region) if region else None
        if roi:
            if not (roi[0] <= int(pos[0]) <= roi[2] and roi[1] <= int(pos[1]) <= roi[3]):
                return None
        match_box = {"center_x": int(pos[0]), "center_y": int(pos[1])}
        size = _template_size(str(resolved_path))
        if size:
            tw, th = size
            x1 = int(pos[0] - tw / 2)
            y1 = int(pos[1] - th / 2)
            match_box = {
                "x": x1,
                "y": y1,
                "x2": x1 + int(tw),
                "y2": y1 + int(th),
                "w": tw,
                "h": th,
                "center_x": int(pos[0]),
                "center_y": int(pos[1]),
            }
        locator = {
            "type": "image",
            "query": str(path),
            "image": str(path),
            "threshold": float(threshold),
            "ocr_lang": ocr_lang,
            "ocr_exact": bool(ocr_exact),
            "ocr_case_sensitive": bool(ocr_case_sensitive),
            "ocr_psm": int(ocr_psm),
        }
        return _ui_element_from_box(
            ctx,
            match_box,
            kind="image",
            query=str(path),
            method="airtest",
            image_src=str(path),
            match_src=str(path),
            locator=locator,
        )

    attempts = max(1, int(retry_attempts))
    for i in range(attempts):
        hit = _find_once()
        if hit:
            ctx._observe("find_image", str(hit), element=hit)
            return hit
        if i < attempts - 1:
            # 自愈检测：在重试前尝试自动修复（弹窗关闭/参数调整等）
            heal_info = _try_self_heal(ctx, "find_image", str(path), "element_not_found", i)
            if heal_info:
                if heal_info["fix_type"] == "popup_dismiss":
                    # 弹窗已关闭，立即重试
                    hit = _find_once()
                    if hit:
                        _report_heal_outcome(ctx, heal_info, True)
                        ctx._observe("find_image", str(hit), element=hit)
                        return hit
                    _report_heal_outcome(ctx, heal_info, False)
            time.sleep(max(0, int(retry_interval_ms)) / 1000.0)
    # Record the failed attempt
    ctx._observe("find_image", f"'{path}' 未找到 (重试 {attempts} 次)", status="fail")
    return None



def find_text(
    ctx: ScriptContext,
    text: str,
    *,
    exact: bool = False,
    ocr_lang: Optional[str] = None,
    ocr_exact: bool = False,
    ocr_case_sensitive: bool = False,
    ocr_psm: int = 6,
    use_ocr: bool = True,
    retry_interval_ms: int = 1000,
    retry_attempts: int = 3,
    box: Optional[Box] = None,
    _silent_fail: bool = False,
) -> Optional[UIElement]:
    """在屏幕上查找文本元素。
    Find a text element on screen.

    优先 UIAutomator 精确匹配，exact=False 时降级为包含匹配。
    支持 "re:" 前缀的正则表达式。短文本（≤2 字）可优先 OCR。
    Prioritizes UIAutomator exact match; falls back to contains when exact=False.
    Supports "re:" prefix for regex. Short text (≤2 chars) may use OCR first.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        text: 要查找的文本，"re:pattern" 表示正则 / Text to find, "re:" for regex
        exact: 仅精确匹配，不降级包含 / Exact match only
        ocr_lang: OCR 语言 / OCR language
        ocr_exact: OCR 精确匹配 / OCR exact match
        ocr_case_sensitive: OCR 区分大小写 / OCR case sensitive
        ocr_psm: Tesseract PSM / Tesseract PSM
        use_ocr: 是否启用 OCR 兜底 / Use OCR fallback
        retry_interval_ms: 重试间隔毫秒 / Retry interval in ms
        retry_attempts: 重试次数 / Retry attempts
        box: 限定搜索区域 / Search region (Box)

    Returns / 返回值:
        UIElement: 找到的元素，可 .tap() / .extract() / Found element
        None: 未找到 / None if not found

    Notes / 特殊逻辑:
        - 查找顺序：UIA 精确 → UIA 包含/正则 → OCR（若 use_ocr=True）
        - 步骤会写入 RunSession（若 auto_screenshot 开启）
    """
    raw_text = str(text or "")
    regex = None
    if raw_text.startswith("re:"):
        try:
            flags = 0 if ocr_case_sensitive else re.IGNORECASE
            regex = re.compile(raw_text[3:], flags=flags)
        except Exception:
            regex = None
    norm_query = _normalize_text(raw_text, case_sensitive=bool(ocr_case_sensitive))
    ocr_langs = _expand_ocr_langs(ctx, ocr_lang)
    roi = _roi_from_box(ctx, box) if box else None

    def _try_ocr_first() -> Optional[UIElement]:
        for lang in ocr_langs:
            if regex:
                boxes = _ocr_find_text_boxes_regex(
                    ctx,
                    pattern=regex,
                    lang=lang,
                    psm=int(ocr_psm),
                    roi=roi,
                )
            elif roi:
                boxes = _ocr_find_text_boxes_roi(
                    ctx,
                    texts=[raw_text],
                    roi=roi,
                    lang=lang,
                    exact=bool(ocr_exact),
                    case_sensitive=bool(ocr_case_sensitive),
                    psm=int(ocr_psm),
                )
            else:
                boxes = _ocr_find_text_boxes(
                    ctx,
                    texts=[raw_text],
                    lang=lang,
                    exact=bool(ocr_exact),
                    case_sensitive=bool(ocr_case_sensitive),
                    psm=int(ocr_psm),
                )
            if boxes:
                box = boxes[0]
                locator = {
                    "type": "text",
                    "query": raw_text,
                    "ocr_lang": lang,
                    "ocr_exact": bool(ocr_exact),
                    "ocr_case_sensitive": bool(ocr_case_sensitive),
                    "ocr_psm": int(ocr_psm),
                    "use_ocr": bool(use_ocr),
                }
                return _ui_element_from_box(
                    ctx,
                    box,
                    kind="text",
                    query=raw_text,
                    method=f"ocr:{lang}",
                    text=raw_text,
                    locator=locator,
                )
        return None

    def _make_elem(box: dict, method: str = "uia") -> UIElement:
        locator = {
            "type": "text",
            "query": raw_text,
            "ocr_lang": ocr_lang,
            "ocr_exact": bool(ocr_exact),
            "ocr_case_sensitive": bool(ocr_case_sensitive),
            "ocr_psm": int(ocr_psm),
            "use_ocr": bool(use_ocr),
        }
        return _ui_element_from_box(
            ctx,
            box,
            kind="text",
            query=raw_text,
            method=method,
            text=box.get("text") or raw_text,
            locator=locator,
        )

    def _find_once() -> Optional[UIElement]:
        if use_ocr and len(norm_query) <= 2:
            hit = _try_ocr_first()
            if hit:
                return hit

        # 第一优先级：精确匹配（全文相等）
        if not regex:
            exact_boxes = _uia_find_text_boxes(
                ctx,
                texts=[raw_text],
                exact=True,
                case_sensitive=False,
                regex=None,
                roi=roi,
            )
            if exact_boxes:
                return _make_elem(exact_boxes[0])

        # 第二优先级：包含匹配 / 正则匹配（仅 exact=False 时降级）
        if not exact:
            ui_boxes = _uia_find_text_boxes(
                ctx,
                texts=[raw_text],
                exact=False,
                case_sensitive=False,
                regex=regex,
                roi=roi,
            )
            if ui_boxes:
                return _make_elem(ui_boxes[0])
        elif regex:
            # exact=True 但使用了正则，仍需正则匹配
            ui_boxes = _uia_find_text_boxes(
                ctx,
                texts=[raw_text],
                exact=False,
                case_sensitive=False,
                regex=regex,
                roi=roi,
            )
            if ui_boxes:
                return _make_elem(ui_boxes[0])

        if use_ocr:
            return _try_ocr_first()
        return None

    attempts = max(1, int(retry_attempts))
    for i in range(attempts):
        hit = _find_once()
        if hit:
            ctx._observe("find_text", str(hit), element=hit)
            return hit
        if i < attempts - 1:
            # 自愈检测：在重试前尝试自动修复（弹窗关闭/参数调整等）
            heal_info = _try_self_heal(ctx, "find_text", raw_text, "element_not_found", i)
            if heal_info:
                if heal_info["fix_type"] == "popup_dismiss":
                    # 弹窗已关闭，立即重试
                    hit = _find_once()
                    if hit:
                        _report_heal_outcome(ctx, heal_info, True)
                        ctx._observe("find_text", str(hit), element=hit)
                        return hit
                    _report_heal_outcome(ctx, heal_info, False)
                elif heal_info["fix_type"] == "param_adjust":
                    # 参数调整：尝试用建议的新参数查找
                    detail = heal_info.get("fix_detail", {})
                    new_regex = detail.get("use_regex")
                    if new_regex and not raw_text.startswith("re:"):
                        # 用建议的正则重试
                        adjusted = find_text(
                            ctx, f"re:{new_regex}",
                            exact=exact, use_ocr=use_ocr,
                            retry_attempts=1, retry_interval_ms=500,
                        )
                        if adjusted:
                            _report_heal_outcome(ctx, heal_info, True)
                            return adjusted
                        _report_heal_outcome(ctx, heal_info, False)
                elif heal_info["fix_type"] == "wait_increase":
                    extra = heal_info.get("fix_detail", {}).get("extra_wait_s", 2.0)
                    time.sleep(float(extra))
                    hit = _find_once()
                    if hit:
                        _report_heal_outcome(ctx, heal_info, True)
                        ctx._observe("find_text", str(hit), element=hit)
                        return hit
                    _report_heal_outcome(ctx, heal_info, False)
            time.sleep(max(0, int(retry_interval_ms)) / 1000.0)
    # Record the failed attempt (skip if called internally, e.g. from scroll_to_find)
    if not _silent_fail:
        ctx._observe("find_text", f"'{raw_text}' 未找到 (重试 {attempts} 次)", status="fail")
    return None


def find_text_list(
    ctx: ScriptContext,
    text: str,
    *,
    ocr_lang: Optional[str] = None,
    ocr_exact: bool = False,
    ocr_case_sensitive: bool = False,
    ocr_psm: int = 6,
    use_ocr: bool = True,
    retry_interval_ms: int = 1000,
    retry_attempts: int = 3,
    box: Optional[Box] = None,
) -> UIElementList:
    """查找屏幕上所有匹配的文本元素。
    Find all text elements matching the query.

    返回 UIElementList，可按索引访问或遍历。支持 "re:" 正则。
    Returns UIElementList with indexing and iteration. Supports "re:" regex.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        text: 要查找的文本 / Text to find
        ocr_lang, ocr_exact, ocr_case_sensitive, ocr_psm: OCR 相关 / OCR options
        use_ocr: 启用 OCR 兜底 / Use OCR fallback
        retry_interval_ms, retry_attempts: 重试参数 / Retry parameters
        box: 限定搜索区域 / Search region

    Returns / 返回值:
        UIElementList: 匹配的元素列表，可能为空 / Matched elements list (may be empty)
    """
    raw_text = str(text or "")
    regex = None
    if raw_text.startswith("re:"):
        try:
            flags = 0 if ocr_case_sensitive else re.IGNORECASE
            regex = re.compile(raw_text[3:], flags=flags)
        except Exception:
            regex = None
    roi = _roi_from_box(ctx, box) if box else None

    def _uia_list() -> list[UIElement]:
        ui_boxes = _uia_find_text_boxes(
            ctx,
            texts=[raw_text],
            exact=bool(ocr_exact),
            case_sensitive=bool(ocr_case_sensitive),
            regex=regex,
            roi=roi,
        )
        elems = []
        for b in ui_boxes:
            elems.append(
                _ui_element_from_box(
                    ctx,
                    b,
                    kind="text",
                    query=raw_text,
                    method="uia",
                    text=b.get("text") or raw_text,
                    locator={"type": "text", "query": raw_text, "use_ocr": bool(use_ocr)},
                )
            )
        return elems

    def _ocr_list() -> list[UIElement]:
        elems: list[UIElement] = []
        ocr_langs = _expand_ocr_langs(ctx, ocr_lang)
        for lang in ocr_langs:
            if regex:
                boxes = _ocr_find_text_boxes_regex(ctx, pattern=regex, lang=lang, psm=int(ocr_psm), roi=roi)
            elif roi:
                boxes = _ocr_find_text_boxes_roi(
                    ctx,
                    texts=[raw_text],
                    roi=roi,
                    lang=lang,
                    exact=bool(ocr_exact),
                    case_sensitive=bool(ocr_case_sensitive),
                    psm=int(ocr_psm),
                )
            else:
                boxes = _ocr_find_text_boxes(
                    ctx,
                    texts=[raw_text],
                    lang=lang,
                    exact=bool(ocr_exact),
                    case_sensitive=bool(ocr_case_sensitive),
                    psm=int(ocr_psm),
                )
            if boxes:
                for b in boxes:
                    elems.append(
                        _ui_element_from_box(
                            ctx,
                            b,
                            kind="text",
                            query=raw_text,
                            method=f"ocr:{lang}",
                            text=b.get("text") or raw_text,
                            locator={"type": "text", "query": raw_text, "use_ocr": bool(use_ocr)},
                        )
                    )
                break
        return elems

    attempts = max(1, int(retry_attempts))
    for i in range(attempts):
        elems = _uia_list()
        if not elems and use_ocr:
            elems = _ocr_list()
        if elems:
            return UIElementList(elems)
        if i < attempts - 1:
            time.sleep(max(0, int(retry_interval_ms)) / 1000.0)
    return UIElementList([])


def find_image_list(
    ctx: ScriptContext,
    path: str,
    *,
    threshold: float = 0.8,
    ocr_lang: Optional[str] = None,
    ocr_exact: bool = False,
    ocr_case_sensitive: bool = False,
    ocr_psm: int = 6,
    retry_interval_ms: int = 1000,
    retry_attempts: int = 3,
    box: Optional[Box] = None,
) -> UIElementList:
    """查找屏幕上所有匹配的图像元素。
    Find all image elements matching the template.

    优先 Airtest 模板匹配，失败时用 OCR 识别图像内文字。返回 UIElementList。
    Prioritizes Airtest; falls back to OCR. Returns UIElementList.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        path: 图像路径 / Image path
        threshold: 模板匹配阈值 / Match threshold
        ocr_* / retry_* / box: 同 find_image / Same as find_image

    Returns / 返回值:
        UIElementList: 匹配的元素列表 / Matched elements list
    """
    roi = _roi_from_box(ctx, box) if box else None

    def _in_roi(b: dict) -> bool:
        if not roi:
            return True
        cx, cy = _box_center(b)
        return roi[0] <= cx <= roi[2] and roi[1] <= cy <= roi[3]

    def _air_list() -> list[UIElement]:
        boxes = _airtest_find_image_boxes(ctx, path=path, threshold=threshold, grayscale=True)
        elems = []
        for b in boxes:
            if not _in_roi(b):
                continue
            elems.append(
                _ui_element_from_box(
                    ctx,
                    b,
                    kind="image",
                    query=str(path),
                    method="airtest",
                    image_src=str(path),
                    match_src=str(path),
                    locator={"type": "image", "query": str(path), "image": str(path)},
                )
            )
        return elems

    def _ocr_list() -> list[UIElement]:
        texts = _ocr_texts_from_image(str(path), lang=ocr_lang, psm=int(ocr_psm))
        if not texts:
            return []
        boxes = _ocr_find_text_boxes(
            ctx,
            texts=texts,
            lang=ocr_lang,
            exact=bool(ocr_exact),
            case_sensitive=bool(ocr_case_sensitive),
            psm=int(ocr_psm),
        )
        elems = []
        for b in boxes:
            if not _in_roi(b):
                continue
            elems.append(
                _ui_element_from_box(
                    ctx,
                    b,
                    kind="image",
                    query=str(path),
                    method="ocr",
                    image_src=str(path),
                    match_src=str(path),
                    locator={"type": "image", "query": str(path), "image": str(path)},
                )
            )
        return elems

    attempts = max(1, int(retry_attempts))
    for i in range(attempts):
        elems = _air_list()
        if not elems:
            elems = _ocr_list()
        if elems:
            return UIElementList(elems)
        if i < attempts - 1:
            time.sleep(max(0, int(retry_interval_ms)) / 1000.0)
    return UIElementList([])
