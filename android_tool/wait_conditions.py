#!/usr/bin/env python3
"""
Wait Conditions - Wait for specific conditions on Android device.
等待条件 - 等待 Android 设备上的特定条件。

This module provides wait utilities for testing and automation.
本模块提供用于测试和自动化的等待工具。

Key functions / 关键函数:
- wait_for_impl: Wait for various conditions / 等待各种条件
- wait_for_element_appear: Wait for element to appear / 等待元素出现
- wait_for_element_disappear: Wait for element to disappear / 等待元素消失
- wait_for_activity: Wait for specific activity / 等待特定 Activity
- wait_for_page_stable: Wait for page to stabilize / 等待页面稳定
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from android_tool.android_device_utils import get_current_focus
from android_tool.element_query import element_exists_impl
from android_tool.uiautomator import dump_ui_xml, parse_uiautomator_nodes, ui_signature


def wait_for_element_appear(
    device_serial: str,
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
) -> dict:
    """
    Wait for element to appear on screen.
    等待元素出现在屏幕上。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - query: Text to search / 要搜索的文本
    - template_path: Image template path / 图片模板路径
    - selector: Rich selector dict / 丰富选择器字典
    - exact: Exact match / 精确匹配
    - ocr_fallback: Enable OCR fallback / 启用 OCR 回退
    - ocr_lang: OCR language / OCR 语言
    - threshold: Image matching threshold / 图片匹配阈值
    - timeout_s: Timeout in seconds / 超时时间（秒）
    - interval_s: Check interval in seconds / 检查间隔（秒）
    
    Returns / 返回:
    - ok: bool
    - satisfied: bool - Whether element appeared / 元素是否出现
    - elapsed_s: float - Time elapsed / 已用时间
    - attempts: int - Number of attempts / 尝试次数
    - match: dict or None - Match details if found / 匹配详情
    """
    start_time = time.time()
    attempts = 0
    last_match = None
    
    while True:
        attempts += 1
        elapsed = time.time() - start_time
        
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
        
        if result.get("exists"):
            last_match = result.get("match")
            return {
                "ok": True,
                "satisfied": True,
                "condition": "element_appear",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "match": last_match,
                "method": result.get("method"),
                "device_serial": device_serial,
            }
        
        if elapsed >= timeout_s:
            return {
                "ok": True,
                "satisfied": False,
                "condition": "element_appear",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "match": None,
                "message": "timeout_waiting_for_element_appear",
                "device_serial": device_serial,
            }
        
        time.sleep(interval_s)


def wait_for_element_disappear(
    device_serial: str,
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
) -> dict:
    """
    Wait for element to disappear from screen.
    等待元素从屏幕上消失。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - query: Text to search / 要搜索的文本
    - template_path: Image template path / 图片模板路径
    - selector: Rich selector dict / 丰富选择器字典
    - exact: Exact match / 精确匹配
    - ocr_fallback: Enable OCR fallback / 启用 OCR 回退
    - ocr_lang: OCR language / OCR 语言
    - threshold: Image matching threshold / 图片匹配阈值
    - timeout_s: Timeout in seconds / 超时时间（秒）
    - interval_s: Check interval in seconds / 检查间隔（秒）
    
    Returns / 返回:
    - ok: bool
    - satisfied: bool - Whether element disappeared / 元素是否消失
    - elapsed_s: float - Time elapsed / 已用时间
    - attempts: int - Number of attempts / 尝试次数
    """
    start_time = time.time()
    attempts = 0
    
    while True:
        attempts += 1
        elapsed = time.time() - start_time
        
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
        
        if not result.get("exists"):
            return {
                "ok": True,
                "satisfied": True,
                "condition": "element_disappear",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "device_serial": device_serial,
            }
        
        if elapsed >= timeout_s:
            return {
                "ok": True,
                "satisfied": False,
                "condition": "element_disappear",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "last_match": result.get("match"),
                "message": "timeout_waiting_for_element_disappear",
                "device_serial": device_serial,
            }
        
        time.sleep(interval_s)


def wait_for_activity(
    device_serial: str,
    activity: str,
    package: Optional[str] = None,
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
) -> dict:
    """
    Wait for specific activity to be in foreground.
    等待特定 Activity 进入前台。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - activity: Activity name (can be partial, e.g. ".MainActivity" or "MainActivity")
              Activity 名称（可以是部分名称）
    - package: Optional package name to also match / 可选的包名匹配
    - timeout_s: Timeout in seconds / 超时时间（秒）
    - interval_s: Check interval in seconds / 检查间隔（秒）
    
    Returns / 返回:
    - ok: bool
    - satisfied: bool - Whether activity matched / Activity 是否匹配
    - elapsed_s: float - Time elapsed / 已用时间
    - attempts: int - Number of attempts / 尝试次数
    - current_activity: str - Current activity when matched / 匹配时的当前 Activity
    - current_package: str - Current package when matched / 匹配时的当前包名
    """
    start_time = time.time()
    attempts = 0
    last_focus = None
    
    # Normalize activity pattern
    activity_pattern = activity.lower().strip()
    if activity_pattern.startswith("."):
        activity_pattern = activity_pattern[1:]  # Remove leading dot for matching
    
    while True:
        attempts += 1
        elapsed = time.time() - start_time
        
        focus = get_current_focus(device_serial)
        last_focus = focus
        current_activity = (focus.get("activity") or "").lower()
        current_package = (focus.get("package") or "").lower()
        
        # Check activity match
        activity_matched = (
            activity_pattern in current_activity or
            current_activity.endswith(activity_pattern) or
            current_activity == activity_pattern
        )
        
        # Check package match if specified
        package_matched = True
        if package:
            package_pattern = package.lower().strip()
            package_matched = (
                package_pattern in current_package or
                current_package == package_pattern
            )
        
        if activity_matched and package_matched:
            return {
                "ok": True,
                "satisfied": True,
                "condition": "activity",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "current_activity": focus.get("activity"),
                "current_package": focus.get("package"),
                "device_serial": device_serial,
            }
        
        if elapsed >= timeout_s:
            return {
                "ok": True,
                "satisfied": False,
                "condition": "activity",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "current_activity": focus.get("activity"),
                "current_package": focus.get("package"),
                "expected_activity": activity,
                "expected_package": package,
                "message": "timeout_waiting_for_activity",
                "device_serial": device_serial,
            }
        
        time.sleep(interval_s)


def wait_for_page_stable(
    device_serial: str,
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
    stable_count: int = 2,
) -> dict:
    """
    Wait for page to stabilize (UI tree not changing).
    等待页面稳定（UI 树不再变化）。
    
    This is useful after navigation or page load to ensure the UI has settled.
    在导航或页面加载后使用，确保 UI 已稳定。
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - timeout_s: Timeout in seconds / 超时时间（秒）
    - interval_s: Check interval in seconds / 检查间隔（秒）
    - stable_count: Number of consecutive identical UI signatures required / 需要连续相同 UI 签名的次数
    
    Returns / 返回:
    - ok: bool
    - satisfied: bool - Whether page stabilized / 页面是否稳定
    - elapsed_s: float - Time elapsed / 已用时间
    - attempts: int - Number of attempts / 尝试次数
    - consecutive_stable: int - Number of consecutive stable checks / 连续稳定检查次数
    """
    start_time = time.time()
    attempts = 0
    last_signature = None
    consecutive_stable = 0
    
    while True:
        attempts += 1
        elapsed = time.time() - start_time
        
        # Dump UI and get signature
        dump_result = dump_ui_xml(device_serial, compressed=True)
        if not dump_result.get("ok"):
            # UI dump failed, reset consecutive count
            consecutive_stable = 0
            last_signature = None
        else:
            xml = dump_result.get("xml", "")
            nodes = parse_uiautomator_nodes(xml)
            current_signature = ui_signature(nodes)
            
            if last_signature is not None and current_signature == last_signature:
                consecutive_stable += 1
            else:
                consecutive_stable = 1
            
            last_signature = current_signature
            
            if consecutive_stable >= stable_count:
                return {
                    "ok": True,
                    "satisfied": True,
                    "condition": "page_stable",
                    "elapsed_s": round(elapsed, 3),
                    "attempts": attempts,
                    "consecutive_stable": consecutive_stable,
                    "signature": current_signature[:16],  # Truncated for readability
                    "device_serial": device_serial,
                }
        
        if elapsed >= timeout_s:
            return {
                "ok": True,
                "satisfied": False,
                "condition": "page_stable",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "consecutive_stable": consecutive_stable,
                "required_stable_count": stable_count,
                "message": "timeout_waiting_for_page_stable",
                "device_serial": device_serial,
            }
        
        time.sleep(interval_s)


def wait_for_impl(
    device_serial: str,
    condition: str,
    # Element conditions params
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    # Activity condition params
    activity: Optional[str] = None,
    package: Optional[str] = None,
    # Timing params
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
    stable_count: int = 2,
) -> dict:
    """
    Wait for specific condition to be satisfied.
    等待特定条件满足。
    
    Supported conditions / 支持的条件:
    - element_appear: Wait for element to appear / 等待元素出现
    - element_disappear: Wait for element to disappear / 等待元素消失
    - activity: Wait for specific Activity / 等待特定 Activity
    - page_stable: Wait for page to stabilize / 等待页面稳定
    
    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - condition: Condition type / 条件类型
    - query: Text to search (for element conditions) / 要搜索的文本
    - template_path: Image template path (for element conditions) / 图片模板路径
    - selector: Rich selector dict (for element conditions) / 丰富选择器字典
    - exact: Exact match / 精确匹配
    - ocr_fallback: Enable OCR fallback / 启用 OCR 回退
    - ocr_lang: OCR language / OCR 语言
    - threshold: Image matching threshold / 图片匹配阈值
    - activity: Activity name (for activity condition) / Activity 名称
    - package: Package name (for activity condition) / 包名
    - timeout_s: Timeout in seconds / 超时时间（秒）
    - interval_s: Check interval in seconds / 检查间隔（秒）
    - stable_count: Consecutive stable count (for page_stable) / 连续稳定次数
    
    Returns / 返回:
    - ok: bool
    - condition: str - Condition type / 条件类型
    - satisfied: bool - Whether condition was satisfied / 条件是否满足
    - elapsed_s: float - Time elapsed / 已用时间
    - attempts: int - Number of attempts / 尝试次数
    
    Examples / 示例:
    ```python
    # Wait for element to appear
    wait_for_impl(serial, "element_appear", query="登录成功")
    
    # Wait for loading indicator to disappear
    wait_for_impl(serial, "element_disappear", query="加载中...")
    
    # Wait for specific Activity
    wait_for_impl(serial, "activity", activity=".MainActivity")
    
    # Wait for page to stabilize
    wait_for_impl(serial, "page_stable", timeout_s=5.0, stable_count=3)
    ```
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial_required"}
    
    condition = (condition or "").strip().lower()
    
    if condition == "element_appear":
        if not query and not template_path and not selector:
            return {"ok": False, "error": "query_or_template_or_selector_required_for_element_appear"}
        return wait_for_element_appear(
            device_serial=device_serial,
            query=query,
            template_path=template_path,
            selector=selector,
            exact=exact,
            ocr_fallback=ocr_fallback,
            ocr_lang=ocr_lang,
            threshold=threshold,
            timeout_s=timeout_s,
            interval_s=interval_s,
        )
    
    elif condition == "element_disappear":
        if not query and not template_path and not selector:
            return {"ok": False, "error": "query_or_template_or_selector_required_for_element_disappear"}
        return wait_for_element_disappear(
            device_serial=device_serial,
            query=query,
            template_path=template_path,
            selector=selector,
            exact=exact,
            ocr_fallback=ocr_fallback,
            ocr_lang=ocr_lang,
            threshold=threshold,
            timeout_s=timeout_s,
            interval_s=interval_s,
        )
    
    elif condition == "activity":
        if not activity:
            return {"ok": False, "error": "activity_required_for_activity_condition"}
        return wait_for_activity(
            device_serial=device_serial,
            activity=activity,
            package=package,
            timeout_s=timeout_s,
            interval_s=interval_s,
        )
    
    elif condition == "page_stable":
        return wait_for_page_stable(
            device_serial=device_serial,
            timeout_s=timeout_s,
            interval_s=interval_s,
            stable_count=stable_count,
        )
    
    else:
        return {
            "ok": False,
            "error": "unsupported_condition",
            "condition": condition,
            "supported": ["element_appear", "element_disappear", "activity", "page_stable"],
        }


async def wait_for_async(
    device_serial: str,
    condition: str,
    query: Optional[str] = None,
    template_path: Optional[str] = None,
    selector: Optional[dict] = None,
    exact: bool = False,
    ocr_fallback: bool = True,
    ocr_lang: str = "eng+chi_sim",
    threshold: float = 0.80,
    activity: Optional[str] = None,
    package: Optional[str] = None,
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
    stable_count: int = 2,
) -> dict:
    """Async wrapper for wait_for_impl."""
    return await asyncio.to_thread(
        wait_for_impl,
        device_serial,
        condition,
        query,
        template_path,
        selector,
        exact,
        ocr_fallback,
        ocr_lang,
        threshold,
        activity,
        package,
        timeout_s,
        interval_s,
        stable_count,
    )
