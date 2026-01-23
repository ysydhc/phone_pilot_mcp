#!/usr/bin/env python3
"""
Assertions - Test assertion utilities for Android automation.
断言 - Android 自动化的测试断言工具。

This module provides assertion functions for verifying test conditions.
本模块提供用于验证测试条件的断言函数。

Key functions / 关键函数:
- assert_impl: Execute assertion and save evidence / 执行断言并保存证据
- assert_element_exists: Assert element exists / 断言元素存在
- assert_element_not_exists: Assert element does not exist / 断言元素不存在
- assert_text_equals: Assert element text equals expected / 断言元素文本等于预期值
- assert_text_contains: Assert element text contains expected / 断言元素文本包含预期值
- assert_activity: Assert current activity matches / 断言当前 Activity 匹配
- assert_logcat: Assert logcat contains pattern / 断言 logcat 包含模式
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import time
from typing import Any, Optional

from android_tool.android_device_utils import get_current_focus
from android_tool.element_query import element_exists_impl, element_query_impl
from android_tool.logcat import dump_logcat
from android_tool.recordings_store import ensure_abs, iso_now, now_dirname, safe_name
from android_tool.screenshot import save_screenshot_png
from android_tool.screenshot_diff import assert_screenshot_match as _assert_screenshot_match_impl
from android_tool.uiautomator import dump_ui_xml, parse_uiautomator_nodes, find_nodes


def _save_evidence(
    device_serial: Optional[str],
    out_dir: str,
    name: str,
    assertion_type: str,
    passed: bool,
) -> Optional[str]:
    """
    Save evidence screenshot.
    保存证据截图。
    """
    try:
        out_root = ensure_abs(out_dir)
        ts = now_dirname()
        status = "pass" if passed else "fail"
        safe = safe_name(f"{name}_{assertion_type}_{status}")
        evidence_dir = out_root / "assertions"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"{ts}_{safe}.png"
        save_screenshot_png(device_serial, evidence_path)
        return str(evidence_path)
    except Exception:
        return None


def assert_element_exists(
    device_serial: str,
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """
    Assert element exists on screen.
    断言元素存在于屏幕上。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - query: Text to search / 要搜索的文本
    - template_path: Image template path / 图片模板路径
    - selector: Rich selector dict / 丰富选择器字典
    - exact: Exact match / 精确匹配
    - ocr_fallback: Enable OCR fallback / 启用 OCR 回退
    - ocr_lang: OCR language / OCR 语言
    - threshold: Image matching threshold / 图片匹配阈值
    - name: Assertion name for evidence / 断言名称（用于证据）
    - save_evidence: Save screenshot as evidence / 保存截图作为证据
    - out_dir: Output directory / 输出目录
    
    Returns / 返回:
    - ok: bool
    - passed: bool - Whether assertion passed / 断言是否通过
    - assertion_type: str
    - evidence_path: str or None
    - detail: dict - Match details / 匹配详情
    """
    result = element_exists_impl(
        device_serial=device_serial,
        query=query,
        template_path=template_path,
        selector=selector,
        exact=exact,
        ocr_fallback=ocr_fallback,
        ocr_lang=ocr_lang,
        threshold=threshold,
    )
    
    passed = bool(result.get("exists"))
    evidence_path = None
    
    if save_evidence:
        evidence_path = _save_evidence(
            device_serial=device_serial,
            out_dir=out_dir,
            name=name or "element_exists",
            assertion_type="element_exists",
            passed=passed,
        )
    
    return {
        "ok": True,
        "passed": passed,
        "assertion_type": "element_exists",
        "query": query,
        "template_path": template_path,
        "selector": selector,
        "evidence_path": evidence_path,
        "detail": {
            "exists": result.get("exists"),
            "method": result.get("method"),
            "match": result.get("match"),
        },
        "device_serial": device_serial,
    }


def assert_element_not_exists(
    device_serial: str,
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """
    Assert element does NOT exist on screen.
    断言元素不存在于屏幕上。
    
    Args / 参数:
    - Same as assert_element_exists
    
    Returns / 返回:
    - ok: bool
    - passed: bool - True if element NOT found / 元素未找到时为 True
    - assertion_type: str
    - evidence_path: str or None
    - detail: dict
    """
    result = element_exists_impl(
        device_serial=device_serial,
        query=query,
        template_path=template_path,
        selector=selector,
        exact=exact,
        ocr_fallback=ocr_fallback,
        ocr_lang=ocr_lang,
        threshold=threshold,
    )
    
    # Passed if element does NOT exist
    passed = not bool(result.get("exists"))
    evidence_path = None
    
    if save_evidence:
        evidence_path = _save_evidence(
            device_serial=device_serial,
            out_dir=out_dir,
            name=name or "element_not_exists",
            assertion_type="element_not_exists",
            passed=passed,
        )
    
    return {
        "ok": True,
        "passed": passed,
        "assertion_type": "element_not_exists",
        "query": query,
        "template_path": template_path,
        "selector": selector,
        "evidence_path": evidence_path,
        "detail": {
            "found": result.get("exists"),
            "method": result.get("method"),
            "match": result.get("match"),
        },
        "device_serial": device_serial,
    }


def assert_text_equals(
    device_serial: str,
    query: str,
    expected: str,
    selector: Optional[dict] = None,
    exact: bool = True,
    case_sensitive: bool = True,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """
    Assert element text equals expected value.
    断言元素文本等于预期值。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - query: Text/resource_id to find the element / 用于查找元素的文本/resource_id
    - expected: Expected text value / 预期文本值
    - selector: Alternative selector to find element / 用于查找元素的选择器
    - exact: Exact match for finding element / 精确匹配查找元素
    - case_sensitive: Case sensitive comparison / 大小写敏感比较
    - name: Assertion name / 断言名称
    - save_evidence: Save screenshot / 保存截图
    - out_dir: Output directory / 输出目录
    
    Returns / 返回:
    - ok: bool
    - passed: bool - Whether text equals expected / 文本是否等于预期
    - assertion_type: str
    - actual: str - Actual text found / 实际找到的文本
    - expected: str - Expected text / 预期文本
    - evidence_path: str or None
    """
    # Find element using query or selector
    actual_text = None
    element_found = False
    match_detail = None
    
    if selector and isinstance(selector, dict):
        result = element_query_impl(device_serial, selector, limit=1)
        if result.get("ok") and result.get("count", 0) > 0:
            element_found = True
            elem = result["elements"][0]
            actual_text = elem.get("text") or elem.get("content_desc") or ""
            match_detail = elem
    else:
        # Use UIAutomator to find by query
        dump_result = dump_ui_xml(device_serial, compressed=True)
        if dump_result.get("ok"):
            xml = dump_result.get("xml", "")
            nodes = parse_uiautomator_nodes(xml)
            hits = find_nodes(nodes, query=query, field="text_or_desc", exact=exact, limit=1)
            if hits:
                element_found = True
                node = hits[0]
                actual_text = node.text or node.content_desc or ""
                match_detail = node.to_dict()
    
    # Compare text
    if not element_found:
        passed = False
        message = "element_not_found"
    else:
        if case_sensitive:
            passed = actual_text == expected
        else:
            passed = (actual_text or "").lower() == (expected or "").lower()
        message = None
    
    evidence_path = None
    if save_evidence:
        evidence_path = _save_evidence(
            device_serial=device_serial,
            out_dir=out_dir,
            name=name or "text_equals",
            assertion_type="text_equals",
            passed=passed,
        )
    
    return {
        "ok": True,
        "passed": passed,
        "assertion_type": "text_equals",
        "query": query,
        "expected": expected,
        "actual": actual_text,
        "element_found": element_found,
        "case_sensitive": case_sensitive,
        "evidence_path": evidence_path,
        "message": message,
        "detail": match_detail,
        "device_serial": device_serial,
    }


def assert_text_contains(
    device_serial: str,
    query: str,
    expected: str,
    selector: Optional[dict] = None,
    exact: bool = True,
    case_sensitive: bool = False,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """
    Assert element text contains expected substring.
    断言元素文本包含预期子串。
    
    Args / 参数:
    - Same as assert_text_equals
    
    Returns / 返回:
    - ok: bool
    - passed: bool - Whether text contains expected / 文本是否包含预期
    - assertion_type: str
    - actual: str - Actual text found / 实际找到的文本
    - expected: str - Expected substring / 预期子串
    - evidence_path: str or None
    """
    # Find element using query or selector
    actual_text = None
    element_found = False
    match_detail = None
    
    if selector and isinstance(selector, dict):
        result = element_query_impl(device_serial, selector, limit=1)
        if result.get("ok") and result.get("count", 0) > 0:
            element_found = True
            elem = result["elements"][0]
            actual_text = elem.get("text") or elem.get("content_desc") or ""
            match_detail = elem
    else:
        # Use UIAutomator to find by query
        dump_result = dump_ui_xml(device_serial, compressed=True)
        if dump_result.get("ok"):
            xml = dump_result.get("xml", "")
            nodes = parse_uiautomator_nodes(xml)
            hits = find_nodes(nodes, query=query, field="text_or_desc", exact=exact, limit=1)
            if hits:
                element_found = True
                node = hits[0]
                actual_text = node.text or node.content_desc or ""
                match_detail = node.to_dict()
    
    # Check if text contains expected
    if not element_found:
        passed = False
        message = "element_not_found"
    else:
        if case_sensitive:
            passed = expected in (actual_text or "")
        else:
            passed = (expected or "").lower() in (actual_text or "").lower()
        message = None
    
    evidence_path = None
    if save_evidence:
        evidence_path = _save_evidence(
            device_serial=device_serial,
            out_dir=out_dir,
            name=name or "text_contains",
            assertion_type="text_contains",
            passed=passed,
        )
    
    return {
        "ok": True,
        "passed": passed,
        "assertion_type": "text_contains",
        "query": query,
        "expected": expected,
        "actual": actual_text,
        "element_found": element_found,
        "case_sensitive": case_sensitive,
        "evidence_path": evidence_path,
        "message": message,
        "detail": match_detail,
        "device_serial": device_serial,
    }


def assert_activity(
    device_serial: str,
    activity: str,
    package: Optional[str] = None,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """
    Assert current activity matches expected.
    断言当前 Activity 匹配预期。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - activity: Expected activity name (can be partial) / 预期的 Activity 名称（可以是部分名称）
    - package: Expected package name (optional) / 预期的包名（可选）
    - name: Assertion name / 断言名称
    - save_evidence: Save screenshot / 保存截图
    - out_dir: Output directory / 输出目录
    
    Returns / 返回:
    - ok: bool
    - passed: bool - Whether activity matches / Activity 是否匹配
    - assertion_type: str
    - expected_activity: str
    - expected_package: str or None
    - current_activity: str
    - current_package: str
    - evidence_path: str or None
    """
    focus = get_current_focus(device_serial)
    current_activity = focus.get("activity") or ""
    current_package = focus.get("package") or ""
    
    # Normalize activity pattern
    activity_pattern = activity.lower().strip()
    if activity_pattern.startswith("."):
        activity_pattern = activity_pattern[1:]
    
    current_activity_lower = current_activity.lower()
    
    # Check activity match
    activity_matched = (
        activity_pattern in current_activity_lower or
        current_activity_lower.endswith(activity_pattern) or
        current_activity_lower == activity_pattern
    )
    
    # Check package match if specified
    package_matched = True
    if package:
        package_pattern = package.lower().strip()
        current_package_lower = current_package.lower()
        package_matched = (
            package_pattern in current_package_lower or
            current_package_lower == package_pattern
        )
    
    passed = activity_matched and package_matched
    
    evidence_path = None
    if save_evidence:
        evidence_path = _save_evidence(
            device_serial=device_serial,
            out_dir=out_dir,
            name=name or "activity",
            assertion_type="activity",
            passed=passed,
        )
    
    return {
        "ok": True,
        "passed": passed,
        "assertion_type": "activity",
        "expected_activity": activity,
        "expected_package": package,
        "current_activity": current_activity,
        "current_package": current_package,
        "activity_matched": activity_matched,
        "package_matched": package_matched,
        "evidence_path": evidence_path,
        "device_serial": device_serial,
    }


def assert_logcat(
    device_serial: str,
    pattern: str,
    regex: bool = False,
    lines: int = 500,
    filter_spec: Optional[str] = None,
    tag_filter: Optional[str] = None,
    case_sensitive: bool = False,
    name: Optional[str] = None,
    save_evidence: bool = False,  # Default False for logcat as screenshot is less useful
    out_dir: str = "./recordings",
) -> dict:
    """
    Assert logcat contains pattern.
    断言 logcat 包含模式。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - pattern: Pattern to search in logcat / 要在 logcat 中搜索的模式
    - regex: Use regex matching / 使用正则匹配
    - lines: Number of logcat lines to check / 检查的 logcat 行数
    - filter_spec: Logcat filter spec (e.g. "*:E") / Logcat 过滤规则
    - tag_filter: Filter by tag / 按标签过滤
    - case_sensitive: Case sensitive matching / 大小写敏感匹配
    - name: Assertion name / 断言名称
    - save_evidence: Save screenshot / 保存截图
    - out_dir: Output directory / 输出目录
    
    Returns / 返回:
    - ok: bool
    - passed: bool - Whether pattern found in logcat / 模式是否在 logcat 中找到
    - assertion_type: str
    - pattern: str
    - regex: bool
    - matched_lines: list - Lines containing the pattern / 包含模式的行
    - evidence_path: str or None
    """
    # Dump logcat
    out_root = ensure_abs(out_dir)
    ts = now_dirname()
    logcat_path = out_root / "assertions" / f"{ts}_logcat_assert.txt"
    logcat_path.parent.mkdir(parents=True, exist_ok=True)
    
    dump_result = dump_logcat(
        device_serial=device_serial,
        out_path=logcat_path,
        lines=lines,
        filter_spec=filter_spec,
        tag_filter=tag_filter,
    )
    
    if not dump_result.get("ok"):
        return {
            "ok": False,
            "passed": False,
            "assertion_type": "logcat",
            "error": "logcat_dump_failed",
            "detail": dump_result,
            "device_serial": device_serial,
        }
    
    # Read logcat content
    logcat_content = ""
    try:
        logcat_content = logcat_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    
    # Search for pattern
    matched_lines = []
    logcat_lines = logcat_content.splitlines()
    
    if regex:
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            compiled = re.compile(pattern, flags)
            for line in logcat_lines:
                if compiled.search(line):
                    matched_lines.append(line)
        except re.error as e:
            return {
                "ok": False,
                "passed": False,
                "assertion_type": "logcat",
                "error": "invalid_regex",
                "detail": str(e),
                "device_serial": device_serial,
            }
    else:
        search_pattern = pattern if case_sensitive else pattern.lower()
        for line in logcat_lines:
            search_line = line if case_sensitive else line.lower()
            if search_pattern in search_line:
                matched_lines.append(line)
    
    passed = len(matched_lines) > 0
    
    evidence_path = None
    if save_evidence:
        evidence_path = _save_evidence(
            device_serial=device_serial,
            out_dir=out_dir,
            name=name or "logcat",
            assertion_type="logcat",
            passed=passed,
        )
    
    # Keep only first 10 matched lines to avoid huge response
    matched_lines_truncated = matched_lines[:10]
    
    return {
        "ok": True,
        "passed": passed,
        "assertion_type": "logcat",
        "pattern": pattern,
        "regex": regex,
        "case_sensitive": case_sensitive,
        "total_lines_checked": len(logcat_lines),
        "matched_count": len(matched_lines),
        "matched_lines": matched_lines_truncated,
        "logcat_path": str(logcat_path),
        "evidence_path": evidence_path,
        "device_serial": device_serial,
    }


def assert_impl(
    device_serial: str,
    assertion_type: str,
    # Element assertion params
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    # Text assertion params
    expected: Optional[str] = None,
    case_sensitive: bool = False,
    # Activity assertion params
    activity: Optional[str] = None,
    package: Optional[str] = None,
    # Logcat assertion params
    logcat_pattern: Optional[str] = None,
    regex: bool = False,
    lines: int = 500,
    filter_spec: Optional[str] = None,
    tag_filter: Optional[str] = None,
    # Screenshot match params
    baseline_path: Optional[str] = None,
    similarity_threshold: float = 0.95,
    mask_regions: Optional[list[dict]] = None,
    # Output params
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """
    Execute assertion and save evidence.
    执行断言并保存证据。
    
    Supported assertion types / 支持的断言类型:
    - element_exists: Assert element exists / 断言元素存在
    - element_not_exists: Assert element does not exist / 断言元素不存在
    - text_equals: Assert element text equals expected / 断言元素文本等于预期值
    - text_contains: Assert element text contains expected / 断言元素文本包含预期值
    - activity: Assert current activity matches / 断言当前 Activity 匹配
    - logcat: Assert logcat contains pattern / 断言 logcat 包含模式
    - screenshot_match: Assert current screenshot matches baseline / 断言当前截图与基准匹配
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - assertion_type: Type of assertion / 断言类型
    - query: Text to search for element / 要搜索的文本
    - template_path: Image template path / 图片模板路径
    - selector: Rich selector dict / 丰富选择器字典
    - exact: Exact match / 精确匹配
    - ocr_fallback: Enable OCR fallback / 启用 OCR 回退
    - ocr_lang: OCR language / OCR 语言
    - threshold: Image matching threshold / 图片匹配阈值
    - expected: Expected text for text assertions / 预期文本
    - case_sensitive: Case sensitive comparison / 大小写敏感比较
    - activity: Expected activity / 预期 Activity
    - package: Expected package / 预期包名
    - logcat_pattern: Pattern to search in logcat / 要在 logcat 中搜索的模式
    - regex: Use regex for logcat / 使用正则
    - lines: Logcat lines to check / 检查的 logcat 行数
    - filter_spec: Logcat filter spec / Logcat 过滤规则
    - tag_filter: Logcat tag filter / Logcat 标签过滤
    - baseline_path: Path to baseline image for screenshot_match / 基准图像路径
    - similarity_threshold: Minimum similarity for screenshot_match / 最小相似度阈值
    - mask_regions: Regions to mask in screenshot comparison / 截图比较时的遮罩区域
    - name: Assertion name / 断言名称
    - save_evidence: Save screenshot as evidence / 保存截图作为证据
    - out_dir: Output directory / 输出目录
    
    Returns / 返回:
    - ok: bool
    - passed: bool - Whether assertion passed / 断言是否通过
    - assertion_type: str
    - evidence_path: str or None
    - detail: dict
    
    Examples / 示例:
    ```python
    # Assert login button exists
    assert_impl(serial, "element_exists", query="登录")
    
    # Assert loading indicator disappeared
    assert_impl(serial, "element_not_exists", query="加载中...")
    
    # Assert welcome text equals expected
    assert_impl(serial, "text_equals", query="欢迎", expected="欢迎回来")
    
    # Assert on MainActivity
    assert_impl(serial, "activity", activity=".MainActivity")
    
    # Assert logcat contains success message
    assert_impl(serial, "logcat", logcat_pattern="登录成功")
    
    # Assert screenshot matches baseline
    assert_impl(serial, "screenshot_match", baseline_path="recordings/baselines/login.png")
    ```
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required", "passed": False}
    
    assertion_type = (assertion_type or "").strip().lower()
    
    if assertion_type == "element_exists":
        if not query and not template_path and not selector:
            return {"ok": False, "error": "query_or_template_or_selector_required", "passed": False}
        return assert_element_exists(
            device_serial=device_serial,
            query=query,
            template_path=template_path,
            selector=selector,
            exact=exact,
            ocr_fallback=ocr_fallback,
            ocr_lang=ocr_lang,
            threshold=threshold,
            name=name,
            save_evidence=save_evidence,
            out_dir=out_dir,
        )
    
    elif assertion_type == "element_not_exists":
        if not query and not template_path and not selector:
            return {"ok": False, "error": "query_or_template_or_selector_required", "passed": False}
        return assert_element_not_exists(
            device_serial=device_serial,
            query=query,
            template_path=template_path,
            selector=selector,
            exact=exact,
            ocr_fallback=ocr_fallback,
            ocr_lang=ocr_lang,
            threshold=threshold,
            name=name,
            save_evidence=save_evidence,
            out_dir=out_dir,
        )
    
    elif assertion_type == "text_equals":
        if not query and not selector:
            return {"ok": False, "error": "query_or_selector_required", "passed": False}
        if expected is None:
            return {"ok": False, "error": "expected_required_for_text_equals", "passed": False}
        return assert_text_equals(
            device_serial=device_serial,
            query=query or "",
            expected=expected,
            selector=selector,
            exact=exact,
            case_sensitive=case_sensitive,
            name=name,
            save_evidence=save_evidence,
            out_dir=out_dir,
        )
    
    elif assertion_type == "text_contains":
        if not query and not selector:
            return {"ok": False, "error": "query_or_selector_required", "passed": False}
        if expected is None:
            return {"ok": False, "error": "expected_required_for_text_contains", "passed": False}
        return assert_text_contains(
            device_serial=device_serial,
            query=query or "",
            expected=expected,
            selector=selector,
            exact=exact,
            case_sensitive=case_sensitive,
            name=name,
            save_evidence=save_evidence,
            out_dir=out_dir,
        )
    
    elif assertion_type == "activity":
        if not activity:
            return {"ok": False, "error": "activity_required", "passed": False}
        return assert_activity(
            device_serial=device_serial,
            activity=activity,
            package=package,
            name=name,
            save_evidence=save_evidence,
            out_dir=out_dir,
        )
    
    elif assertion_type == "logcat":
        if not logcat_pattern:
            return {"ok": False, "error": "logcat_pattern_required", "passed": False}
        return assert_logcat(
            device_serial=device_serial,
            pattern=logcat_pattern,
            regex=regex,
            lines=lines,
            filter_spec=filter_spec,
            tag_filter=tag_filter,
            case_sensitive=case_sensitive,
            name=name,
            save_evidence=save_evidence,
            out_dir=out_dir,
        )
    
    elif assertion_type == "screenshot_match":
        if not baseline_path:
            return {"ok": False, "error": "baseline_path_required", "passed": False}
        return _assert_screenshot_match_impl(
            device_serial=device_serial,
            baseline_path=baseline_path,
            similarity_threshold=similarity_threshold,
            mask_regions=mask_regions,
            name=name,
            save_evidence=save_evidence,
            out_dir=out_dir,
        )
    
    else:
        return {
            "ok": False,
            "passed": False,
            "error": "unsupported_assertion_type",
            "assertion_type": assertion_type,
            "supported": ["element_exists", "element_not_exists", "text_equals", "text_contains", "activity", "logcat", "screenshot_match"],
        }


async def assert_async(
    device_serial: str,
    assertion_type: str,
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    expected: Optional[str] = None,
    case_sensitive: bool = False,
    activity: Optional[str] = None,
    package: Optional[str] = None,
    logcat_pattern: Optional[str] = None,
    regex: bool = False,
    lines: int = 500,
    filter_spec: Optional[str] = None,
    tag_filter: Optional[str] = None,
    baseline_path: Optional[str] = None,
    similarity_threshold: float = 0.95,
    mask_regions: Optional[list[dict]] = None,
    name: Optional[str] = None,
    save_evidence: bool = True,
    out_dir: str = "./recordings",
) -> dict:
    """Async wrapper for assert_impl."""
    return await asyncio.to_thread(
        assert_impl,
        device_serial,
        assertion_type,
        query,
        template_path,
        selector,
        exact,
        ocr_fallback,
        ocr_lang,
        threshold,
        expected,
        case_sensitive,
        activity,
        package,
        logcat_pattern,
        regex,
        lines,
        filter_spec,
        tag_filter,
        baseline_path,
        similarity_threshold,
        mask_regions,
        name,
        save_evidence,
        out_dir,
    )
