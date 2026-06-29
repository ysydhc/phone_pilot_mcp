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
from typing import Optional

from phone_pilot.android.device.utils import get_current_focus
from phone_pilot.android.ui.query import element_exists_impl
from phone_pilot.android.ui.automator import (
    dump_ui_xml,
    parse_uiautomator_nodes,
    ui_signature,
)


import re

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_bounds(bounds) -> tuple[int, int, int, int] | None:
    """从 match dict 的 bounds 字段解析出 (x1, y1, x2, y2)。

    支持两种格式：
    - 字符串 "[x1,y1][x2,y2]"（UIAutomator 原始格式）
    - 列表 [x1, y1, x2, y2]
    """
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        try:
            return tuple(int(v) for v in bounds)  # type: ignore[return-value]
        except (ValueError, TypeError):
            return None
    if isinstance(bounds, str):
        m = _BOUNDS_RE.match(bounds.strip())
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return None


def _match_is_visible(match: Optional[dict]) -> bool:
    """判断 match 是否满足 visible 条件。

    第一版定义（不判断遮挡）：
    1. 如果 match 有 visible/displayed/visible_to_user 字段且为 False → 不满足
    2. bounds 必须存在且可解析
    3. width > 0 且 height > 0
    """
    if not isinstance(match, dict):
        return False

    # 1. 显式可见性字段
    for key in ("visible", "displayed", "visible_to_user"):
        val = match.get(key)
        if val is False:
            return False

    # 2-3. bounds 有效性
    b = _parse_bounds(match.get("bounds"))
    if b is None:
        return False
    x1, y1, x2, y2 = b
    if (x2 - x1) <= 0 or (y2 - y1) <= 0:
        return False

    return True


def _match_meets_state(
    match: Optional[dict],
    require_clickable: bool,
    require_enabled: bool,
    require_visible: bool = False,
) -> bool:
    """Check that a match dict satisfies the required interactive state.

    When a state is required but the match has no such field (e.g. OCR/image
    matches carry no clickable/enabled info), treat it as NOT satisfied — we
    cannot confirm the element is actually interactive.

    require_visible: 检查 bounds 有效且尺寸 > 0（不判断遮挡）。
    """
    if not require_clickable and not require_enabled and not require_visible:
        return True
    if not isinstance(match, dict):
        return False
    if require_visible and not _match_is_visible(match):
        return False
    if require_clickable and not match.get("clickable"):
        return False
    if require_enabled and not match.get("enabled"):
        return False
    return True


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
    require_clickable: bool = False,
    require_enabled: bool = False,
    require_visible: bool = False,
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
    - require_clickable: Element must be clickable to count / 必须可点击才算满足
    - require_enabled: Element must be enabled to count / 必须 enabled 才算满足
    - require_visible: Element must have valid non-zero bounds to count / 必须有有效非零 bounds 才算满足

    Returns / 返回:
    - ok: bool
    - satisfied: bool - Whether element appeared / 元素是否出现
    - elapsed_s: float - Time elapsed / 已用时间
    - attempts: int - Number of attempts / 尝试次数
    - match: dict or None - Match details if found / 匹配详情

    Notes / 说明:
    - require_clickable/require_enabled 解决"元素已在 UI 树但尚不可点"的时序问题。
      对 OCR/图片匹配结果（无 clickable/enabled 字段）会判为不满足。
      Solves the "element present but not yet tappable" timing issue. OCR/image
      matches (no clickable/enabled field) are treated as not satisfied.
    - require_visible 检查 bounds 有效且尺寸 > 0；不判断遮挡。
      Checks valid non-zero bounds; does NOT detect occlusion.
    """
    start_time = time.time()
    attempts = 0
    last_match = None
    last_method = None

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
            match = result.get("match")
            if _match_meets_state(
                match, require_clickable, require_enabled, require_visible
            ):
                return {
                    "ok": True,
                    "satisfied": True,
                    "condition": "element_appear",
                    "elapsed_s": round(elapsed, 3),
                    "attempts": attempts,
                    "match": match,
                    "method": result.get("method"),
                    "device_serial": device_serial,
                }
            # Found but not in required state yet — remember and keep polling.
            last_match = match
            last_method = result.get("method")

        if elapsed >= timeout_s:
            timed_out_on_state = last_match is not None
            return {
                "ok": True,
                "satisfied": False,
                "condition": "element_appear",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "match": last_match,
                "method": last_method,
                "message": (
                    "timeout_element_present_but_not_ready"
                    if timed_out_on_state
                    else "timeout_waiting_for_element_appear"
                ),
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

    # Normalize activity pattern
    activity_pattern = activity.lower().strip()
    if activity_pattern.startswith("."):
        activity_pattern = activity_pattern[1:]  # Remove leading dot for matching

    while True:
        attempts += 1
        elapsed = time.time() - start_time

        focus = get_current_focus(device_serial)
        current_activity = (focus.get("activity") or "").lower()
        current_package = (focus.get("package") or "").lower()

        # Check activity match
        activity_matched = (
            activity_pattern in current_activity
            or current_activity.endswith(activity_pattern)
            or current_activity == activity_pattern
        )

        # Check package match if specified
        package_matched = True
        if package:
            package_pattern = package.lower().strip()
            package_matched = (
                package_pattern in current_package or current_package == package_pattern
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


def _activity_matches(pattern: str, current_activity: str) -> bool:
    """Match an activity pattern against a current activity (case-insensitive,
    leading-dot tolerant, substring/suffix/exact)."""
    p = (pattern or "").lower().strip()
    if p.startswith("."):
        p = p[1:]
    cur = (current_activity or "").lower()
    return p in cur or cur.endswith(p) or cur == p


def wait_for_activity_change(
    device_serial: str,
    from_activity: Optional[str] = None,
    to_activity: Optional[str] = None,
    package: Optional[str] = None,
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
) -> dict:
    """
    Wait for the foreground activity to change away from a baseline.
    等待前台 Activity 从基线发生变化（确认确实发生了跳转）。

    与 wait_for_activity 的区别 / Difference from wait_for_activity:
        wait_for_activity 只检查"当前是否为 B"——若一开始就在 B 会立即成功，
        无法确认"发生了一次跳转"。本函数先确立基线，再等待离开基线。
        wait_for_activity returns success if you are already on B. This one
        establishes a baseline first, then waits until the activity leaves it.

    Args / 参数:
    - device_serial: Device serial / 设备序列号
    - from_activity: Baseline activity. None = capture current as baseline.
                     基线 Activity。None 则进函数时自动取当前为基线。
    - to_activity: Target activity. None = any activity other than baseline.
                   目标 Activity。None 则"变成任何别的"都算成功；指定则必须变成它。
    - package: Optional package match for the target / 目标可选包名匹配
    - timeout_s: Timeout in seconds / 超时时间（秒）
    - interval_s: Check interval in seconds / 检查间隔（秒）

    Returns / 返回:
    - ok: bool
    - satisfied: bool - Whether the change happened / 是否发生了预期变化
    - elapsed_s / attempts
    - from_activity: baseline used / 实际使用的基线
    - current_activity / current_package: state at return / 返回时的状态
    """
    start_time = time.time()
    attempts = 0

    # Establish baseline.
    baseline_focus = get_current_focus(device_serial)
    baseline_activity = baseline_focus.get("activity") or ""
    if from_activity is None:
        baseline = baseline_activity
    else:
        baseline = from_activity

    while True:
        attempts += 1
        elapsed = time.time() - start_time

        focus = get_current_focus(device_serial)
        current_activity = focus.get("activity") or ""
        current_package = focus.get("package") or ""

        # Has it left the baseline?
        left_baseline = not _activity_matches(baseline, current_activity)

        # Does it match the desired target (if any)?
        if to_activity is None:
            reached_target = left_baseline
        else:
            reached_target = _activity_matches(to_activity, current_activity)
            if package:
                pkg = package.lower().strip()
                reached_target = reached_target and (
                    pkg in current_package or current_package == pkg
                )

        if reached_target:
            return {
                "ok": True,
                "satisfied": True,
                "condition": "activity_change",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "from_activity": baseline,
                "current_activity": focus.get("activity"),
                "current_package": focus.get("package"),
                "device_serial": device_serial,
            }

        if elapsed >= timeout_s:
            return {
                "ok": True,
                "satisfied": False,
                "condition": "activity_change",
                "elapsed_s": round(elapsed, 3),
                "attempts": attempts,
                "from_activity": baseline,
                "to_activity": to_activity,
                "current_activity": focus.get("activity"),
                "current_package": focus.get("package"),
                "message": "timeout_waiting_for_activity_change",
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
    from_activity: Optional[str] = None,
    # Timing params
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
    stable_count: int = 2,
    # Element state requirements (element_appear)
    require_clickable: bool = False,
    require_enabled: bool = False,
    require_visible: bool = False,
) -> dict:
    """
    Wait for specific condition to be satisfied.
    等待特定条件满足。

    Supported conditions / 支持的条件:
    - element_appear: Wait for element to appear / 等待元素出现
    - element_disappear: Wait for element to disappear / 等待元素消失
    - activity: Wait for specific Activity / 等待特定 Activity
    - activity_change: Wait for activity to change from a baseline / 等待 Activity 从基线变化
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
            return {
                "ok": False,
                "error": "query_or_template_or_selector_required_for_element_appear",
            }
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
            require_clickable=require_clickable,
            require_enabled=require_enabled,
            require_visible=require_visible,
        )

    elif condition == "element_disappear":
        if not query and not template_path and not selector:
            return {
                "ok": False,
                "error": "query_or_template_or_selector_required_for_element_disappear",
            }
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

    elif condition == "activity_change":
        return wait_for_activity_change(
            device_serial=device_serial,
            from_activity=from_activity,
            to_activity=activity,
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
            "supported": [
                "element_appear",
                "element_disappear",
                "activity",
                "activity_change",
                "page_stable",
            ],
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
    require_clickable: bool = False,
    require_enabled: bool = False,
    require_visible: bool = False,
) -> dict:
    """Async wrapper for wait_for_impl.

    参数与 wait_for_impl 对称，包括 require_clickable/require_enabled/require_visible。
    """
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
        require_clickable,
        require_enabled,
        require_visible,
    )
