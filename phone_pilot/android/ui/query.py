#!/usr/bin/env python3
"""
Element Query - Enhanced UI element querying utilities.
元素查询 - 增强的 UI 元素查询工具。

This module provides advanced element querying capabilities.
本模块提供高级元素查询功能。

Key functions / 关键函数:
- element_query_impl: Query elements with rich selectors / 使用丰富选择器查询元素
- element_exists_impl: Check if element exists / 检查元素是否存在
"""

from __future__ import annotations

import asyncio
from typing import Optional

from phone_pilot.android.ui.automator import UINode, dump_ui_xml, parse_uiautomator_nodes, find_nodes
from phone_pilot.extensions.vision.template import find_template_on_screen_with_fallback
from phone_pilot.extensions.ocr.tesseract import ocr_screenshot_and_find
from phone_pilot.extensions.lang.translate import translate_candidates


def collect_node_texts(nodes: list[UINode]) -> list[str]:
    """
    Collect visible text/content_desc/hint from nodes.
    """
    texts: list[str] = []
    for n in nodes:
        if not isinstance(n, UINode):
            continue
        for val in (n.text, n.content_desc, n.hint):
            if isinstance(val, str) and val.strip():
                texts.append(val.strip())
    return texts


def _match_selector(node: UINode, selector: dict, case_sensitive: bool = False) -> bool:
    """
    Check if a UINode matches the given selector.
    检查 UINode 是否匹配给定的选择器。
    
    Selector keys / 选择器键:
    - text: Match node text or hint / 匹配节点文本或提示
    - text_contains: Text contains (text or hint) / 文本包含（文本或提示）
    - hint: Match hint text / 匹配提示
    - hint_contains: Hint contains / 提示包含
    - desc: Match content_desc / 匹配 content_desc
    - desc_contains: content_desc contains / content_desc 包含
    - resource_id: Match resource_id / 匹配 resource_id
    - resource_id_contains: resource_id contains / resource_id 包含
    - class_name: Match class name / 匹配类名
    - package: Match package / 匹配包名
    - clickable: Match clickable attribute / 匹配 clickable 属性
    - enabled: Match enabled attribute / 匹配 enabled 属性
    - focusable: Match focusable attribute / 匹配 focusable 属性
    - scrollable: Match scrollable attribute / 匹配 scrollable 属性
    - checkable: Match checkable attribute / 匹配 checkable 属性
    - checked: Match checked attribute / 匹配 checked 属性
    """
    def norm(s: Optional[str]) -> str:
        """Normalize strings for comparison based on case sensitivity."""
        if s is None:
            return ""
        return s if case_sensitive else s.lower()
    
    def match_str(node_val: Optional[str], sel_val: str, exact: bool = True) -> bool:
        """Match string selector against node value."""
        nv = norm(node_val)
        sv = norm(sel_val) if not case_sensitive else sel_val
        if not nv:
            return False
        return nv == sv if exact else sv in nv
    
    def match_bool(node_val: Optional[bool], sel_val: bool) -> bool:
        """Match boolean selector against node value."""
        return node_val == sel_val
    
    # Check each selector key
    for key, val in selector.items():
        if key == "text":
            if not (match_str(node.text, val, exact=True) or match_str(node.hint, val, exact=True)):
                return False
        elif key == "text_contains":
            if not (match_str(node.text, val, exact=False) or match_str(node.hint, val, exact=False)):
                return False
        elif key == "hint":
            if not match_str(node.hint, val, exact=True):
                return False
        elif key == "hint_contains":
            if not match_str(node.hint, val, exact=False):
                return False
        elif key == "desc":
            if not match_str(node.content_desc, val, exact=True):
                return False
        elif key == "desc_contains":
            if not match_str(node.content_desc, val, exact=False):
                return False
        elif key == "resource_id":
            if not match_str(node.resource_id, val, exact=True):
                return False
        elif key == "resource_id_contains":
            if not match_str(node.resource_id, val, exact=False):
                return False
        elif key == "class_name":
            if not match_str(node.class_name, val, exact=True):
                return False
        elif key == "class_name_contains":
            if not match_str(node.class_name, val, exact=False):
                return False
        elif key == "package":
            if not match_str(node.package, val, exact=True):
                return False
        elif key == "clickable":
            if not match_bool(node.clickable, val):
                return False
        elif key == "enabled":
            if not match_bool(node.enabled, val):
                return False
        elif key == "focusable":
            if not match_bool(node.focusable, val):
                return False
        elif key == "scrollable":
            if not match_bool(node.scrollable, val):
                return False
        elif key == "checkable":
            if not match_bool(node.checkable, val):
                return False
        elif key == "checked":
            if not match_bool(node.checked, val):
                return False
        elif key == "selected":
            if not match_bool(node.selected, val):
                return False
        elif key == "focused":
            if not match_bool(node.focused, val):
                return False
        elif key == "long_clickable":
            if not match_bool(node.long_clickable, val):
                return False
        elif key == "password":
            if not match_bool(node.password, val):
                return False
    
    return True


def _filter_attributes(node_dict: dict, attributes: Optional[list[str]]) -> dict:
    """
    Filter node dict to only include specified attributes.
    过滤节点字典，只包含指定的属性。
    """
    if attributes is None:
        return node_dict
    return {k: v for k, v in node_dict.items() if k in attributes}


def element_query_impl(
    device_serial: Optional[str],
    selector: dict,
    attributes: Optional[list[str]] = None,
    case_sensitive: bool = False,
    limit: int = 50,
) -> dict:
    """
    Query elements with rich selectors.
    使用丰富的选择器查询元素。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - selector: Selector dict / 选择器字典
        - text: Exact text match / 精确文本匹配
        - text_contains: Text contains / 文本包含
        - desc: Exact content_desc match / 精确 content_desc 匹配
        - desc_contains: content_desc contains / content_desc 包含
        - resource_id: Exact resource_id match / 精确 resource_id 匹配
        - resource_id_contains: resource_id contains / resource_id 包含
        - class_name: Exact class name match / 精确类名匹配
        - package: Package name / 包名
        - clickable/enabled/focusable/scrollable: Boolean attributes / 布尔属性
    - attributes: List of attributes to return (None = all) / 要返回的属性列表（None = 全部）
    - case_sensitive: Case sensitive matching / 大小写敏感匹配
    - limit: Max elements to return / 最大返回元素数
    
    Returns / 返回:
    - ok: bool
    - count: int - Number of matched elements / 匹配元素数量
    - elements: list - List of element dicts / 元素字典列表
    
    Example / 示例:
    ```python
    # Find login button
    element_query_impl(serial, {"text": "登录", "clickable": True})
    
    # Find all EditText elements
    element_query_impl(serial, {"class_name": "android.widget.EditText"})
    
    # Find by resource_id
    element_query_impl(serial, {"resource_id_contains": "btn_submit"})
    ```
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required", "count": 0, "elements": []}
    
    if not isinstance(selector, dict) or not selector:
        return {"ok": False, "error": "invalid_selector", "message": "selector must be a non-empty dict", "count": 0, "elements": []}
    
    # Dump UI hierarchy
    dump_result = dump_ui_xml(device_serial, compressed=True)
    if not dump_result.get("ok"):
        return {"ok": False, "error": "ui_dump_failed", "detail": dump_result, "count": 0, "elements": []}
    
    xml = dump_result.get("xml", "")
    nodes = parse_uiautomator_nodes(xml)
    
    # Find matching nodes
    matched: list[dict] = []
    for node in nodes:
        if _match_selector(node, selector, case_sensitive):
            node_dict = node.to_dict()
            filtered = _filter_attributes(node_dict, attributes)
            matched.append(filtered)
            if len(matched) >= limit:
                break

    # Translation fallback for text/desc selectors
    if not matched:
        trans_keys = ["text", "text_contains", "desc", "desc_contains"]
        for key in trans_keys:
            if key not in selector:
                continue
            try:
                candidates = translate_candidates(str(selector.get(key) or ""))
            except Exception:
                candidates = [str(selector.get(key) or "")]
            for cand in candidates[1:]:
                selector2 = dict(selector)
                selector2[key] = cand
                for node in nodes:
                    if _match_selector(node, selector2, case_sensitive):
                        node_dict = node.to_dict()
                        filtered = _filter_attributes(node_dict, attributes)
                        filtered["translated_from"] = selector.get(key)
                        matched.append(filtered)
                        if len(matched) >= limit:
                            break
                if matched:
                    break
            if matched:
                break
    
    return {
        "ok": True,
        "count": len(matched),
        "elements": matched,
        "device_serial": device_serial,
    }


def element_exists_impl(
    device_serial: Optional[str],
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
) -> dict:
    """
    Check if element exists (without clicking).
    检查元素是否存在（不点击）。
    
    Supports three search methods (in order):
    支持三种搜索方法（按顺序）：
    1. UIAutomator (query or selector)
    2. OCR fallback (if ocr_fallback=True and query provided)
    3. Image matching (if template_path provided)
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - query: Text to search (text or content_desc) / 要搜索的文本
    - template_path: Image template path / 图片模板路径
    - selector: Rich selector dict (alternative to query) / 丰富选择器字典
    - exact: Exact match for query / 精确匹配
    - ocr_fallback: Enable OCR fallback / 启用 OCR 回退
    - ocr_lang: OCR language / OCR 语言
    - threshold: Image matching threshold / 图片匹配阈值
    
    Returns / 返回:
    - ok: bool
    - exists: bool - Whether element exists / 元素是否存在
    - method: str - Method used to find ("uiautomator", "ocr", "image") / 使用的方法
    - match: dict or None - Match details if found / 匹配详情
    
    Example / 示例:
    ```python
    # Check if "登录" button exists
    result = element_exists_impl(serial, query="登录")
    if result["exists"]:
        print("Login button found at:", result["match"])
    
    # Check using image template
    result = element_exists_impl(serial, template_path="icon.png")
    
    # Check using rich selector
    result = element_exists_impl(serial, selector={"resource_id_contains": "login_btn"})
    ```
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required", "exists": False, "match": None}
    
    if not query and not template_path and not selector:
        return {"ok": False, "error": "query_or_template_or_selector_required", "exists": False, "match": None}
    
    # Method 1: Try UIAutomator with selector
    if selector and isinstance(selector, dict):
        result = element_query_impl(device_serial, selector, limit=1)
        if result.get("ok") and result.get("count", 0) > 0:
            return {
                "ok": True,
                "exists": True,
                "method": "uiautomator_selector",
                "match": result["elements"][0],
                "device_serial": device_serial,
            }
    
    # Method 2: Try UIAutomator with query
    if query:
        dump_result = dump_ui_xml(device_serial, compressed=True)
        if dump_result.get("ok"):
            xml = dump_result.get("xml", "")
            nodes = parse_uiautomator_nodes(xml)
            hits = find_nodes(nodes, query=query, field="auto", exact=exact, limit=1)
            if hits:
                return {
                    "ok": True,
                    "exists": True,
                    "method": "uiautomator",
                    "match": hits[0].to_dict(),
                    "device_serial": device_serial,
                }

            # Translation fallback (UIAutomator)
            try:
                candidates = translate_candidates(str(query))
            except Exception:
                candidates = [str(query)]
            for cand in candidates[1:]:
                hits = find_nodes(nodes, query=cand, field="auto", exact=exact, limit=1)
                if hits:
                    return {
                        "ok": True,
                        "exists": True,
                        "method": "uiautomator_translated",
                        "match": hits[0].to_dict(),
                        "translated_from": query,
                        "device_serial": device_serial,
                    }
        
        # Method 3: Try OCR fallback
        if ocr_fallback:
            try:
                queries = candidates if "candidates" in locals() else [query]
                for cand in queries:
                    ocr_result = ocr_screenshot_and_find(
                        device_serial=device_serial,
                        query=cand,
                        exact=exact,
                        case_sensitive=False,
                        limit=1,
                        lang=ocr_lang,
                    )
                    if isinstance(ocr_result, dict) and ocr_result.get("ok") and ocr_result.get("count", 0) > 0:
                        matches = ocr_result.get("matches", [])
                        if matches:
                            return {
                                "ok": True,
                                "exists": True,
                                "method": "ocr",
                                "match": matches[0],
                                "device_serial": device_serial,
                            }
            except Exception:
                pass  # OCR failed, continue to image matching
    
    # Method 4: Try image matching
    if template_path:
        try:
            img_result = find_template_on_screen_with_fallback(
                device_serial=device_serial,
                template_path=template_path,
                threshold=threshold,
                grayscale=True,
                max_results=1,
            )
            if isinstance(img_result, dict) and img_result.get("ok"):
                matches = img_result.get("matches", [])
                if matches:
                    return {
                        "ok": True,
                        "exists": True,
                        "method": "image",
                        "match": matches[0],
                        "device_serial": device_serial,
                    }
        except Exception:
            pass  # Image matching failed
    
    # Not found by any method
    methods_tried = []
    if selector:
        methods_tried.append("uiautomator_selector")
    if query:
        methods_tried.append("uiautomator")
        if ocr_fallback:
            methods_tried.append("ocr")
    if template_path:
        methods_tried.append("image")
    
    return {
        "ok": True,
        "exists": False,
        "method": None,
        "match": None,
        "methods_tried": methods_tried,
        "device_serial": device_serial,
    }


async def element_query_async(
    device_serial: Optional[str],
    selector: dict,
    attributes: Optional[list[str]] = None,
    case_sensitive: bool = False,
    limit: int = 50,
) -> dict:
    """Async wrapper for element_query_impl."""
    return await asyncio.to_thread(
        element_query_impl,
        device_serial,
        selector,
        attributes,
        case_sensitive,
        limit,
    )


async def element_exists_async(
    device_serial: Optional[str],
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
) -> dict:
    """Async wrapper for element_exists_impl."""
    return await asyncio.to_thread(
        element_exists_impl,
        device_serial,
        query,
        template_path,
        selector,
        exact,
        ocr_fallback,
        ocr_lang,
        threshold,
    )
