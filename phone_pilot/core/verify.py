"""批量验证引擎：多维度断言 + 证据收集。

支持的断言类型：
- **UI 断言**: element_exists, element_not_exists, text_equals, text_contains,
  screen_contains_text, screenshot_match
- **功能断言**: activity_equals, app_foreground
- **日志断言**: logcat_contains, logcat_not_contains
- **内存断言**: memory_snapshot, memory_no_growth
- **性能断言**: elapsed_under

所有文本参数（text, pattern, expected）统一支持 ``re:`` 前缀表示正则表达式。

用法::

    from phone_pilot.core.verify import run_assertions
    results = run_assertions(driver, [
        {"type": "element_exists", "text": "关注"},
        {"type": "logcat_contains", "pattern": "re:init.*ok"},
    ])
"""

from __future__ import annotations

import pathlib
import re
import time
from typing import Any, Optional

from phone_pilot.core.storage import iso_now, recordings_root, write_json


# ---------------------------------------------------------------------------
# 文本匹配工具：统一 re: 前缀
# ---------------------------------------------------------------------------

def _text_matches(pattern: str, target: str) -> bool:
    """检查 target 是否匹配 pattern。

    ``pattern`` 以 ``re:`` 开头时为正则匹配，否则为精确匹配。
    """
    if pattern.startswith("re:"):
        regex = pattern[3:]
        try:
            return bool(re.search(regex, target))
        except re.error:
            return False
    return pattern == target


def _text_contains(pattern: str, target: str) -> bool:
    """检查 target 是否包含 pattern。

    ``pattern`` 以 ``re:`` 开头时为正则搜索，否则为子串包含。
    """
    if pattern.startswith("re:"):
        regex = pattern[3:]
        try:
            return bool(re.search(regex, target))
        except re.error:
            return False
    return pattern in target


# ---------------------------------------------------------------------------
# 单条断言执行器
# ---------------------------------------------------------------------------

def _assert_element_exists(driver: Any, params: dict) -> dict:
    """UI 元素存在断言。"""
    text = params.get("text", "")
    selector = params.get("selector", {})

    if text:
        # 用 find_elements 查找
        if text.startswith("re:"):
            # 正则：需要遍历所有文本
            all_texts = driver.ui.collect_all_texts()
            for t in all_texts:
                if _text_matches(text, t):
                    return {"passed": True, "detail": {"found_text": t}}
            return {"passed": False, "detail": {"query": text, "searched": len(all_texts)}}
        else:
            elems = driver.ui.find_elements({"text": text})
            if elems:
                return {"passed": True, "detail": {"count": len(elems)}}
            return {"passed": False, "detail": {"query": text, "count": 0}}

    if selector:
        elems = driver.ui.find_elements(selector)
        if elems:
            return {"passed": True, "detail": {"count": len(elems)}}
        return {"passed": False, "detail": {"selector": selector, "count": 0}}

    return {"passed": False, "detail": {"error": "text or selector required"}}


def _assert_element_not_exists(driver: Any, params: dict) -> dict:
    """UI 元素不存在断言。"""
    result = _assert_element_exists(driver, params)
    result["passed"] = not result["passed"]
    return result


def _assert_text_equals(driver: Any, params: dict) -> dict:
    """元素文本精确匹配。"""
    query = params.get("query", "")
    expected = params.get("expected", "")

    if not query:
        return {"passed": False, "detail": {"error": "query required"}}

    elems = driver.ui.find_elements({"text": query})
    if not elems:
        return {"passed": False, "detail": {"error": "element_not_found", "query": query}}

    actual = elems[0].get("text", "")
    passed = _text_matches(expected, actual)
    return {"passed": passed, "detail": {"expected": expected, "actual": actual}}


def _assert_text_contains(driver: Any, params: dict) -> dict:
    """元素文本包含匹配。"""
    query = params.get("query", "")
    expected = params.get("expected", "")

    if not query:
        return {"passed": False, "detail": {"error": "query required"}}

    elems = driver.ui.find_elements({"text": query})
    if not elems:
        return {"passed": False, "detail": {"error": "element_not_found", "query": query}}

    actual = elems[0].get("text", "")
    passed = _text_contains(expected, actual)
    return {"passed": passed, "detail": {"expected": expected, "actual": actual}}


def _assert_screen_contains_text(driver: Any, params: dict) -> dict:
    """OCR 全屏搜索文本。"""
    text = params.get("text", "")
    if not text:
        return {"passed": False, "detail": {"error": "text required"}}

    all_texts = driver.ui.collect_all_texts()
    full_text = " ".join(all_texts)

    if _text_contains(text, full_text):
        return {"passed": True, "detail": {"found_in": len(all_texts), "query": text}}

    # 逐项匹配
    for t in all_texts:
        if _text_contains(text, t):
            return {"passed": True, "detail": {"found_text": t, "query": text}}

    return {"passed": False, "detail": {"query": text, "searched": len(all_texts)}}


def _assert_screenshot_match(driver: Any, params: dict, evidence_dir: pathlib.Path) -> dict:
    """截图与基准对比。"""
    baseline = params.get("baseline", "")
    threshold = float(params.get("threshold", 0.9))

    if not baseline:
        return {"passed": False, "detail": {"error": "baseline required"}}

    baseline_path = pathlib.Path(baseline)
    if not baseline_path.exists():
        return {"passed": False, "detail": {"error": f"baseline not found: {baseline}"}}

    try:
        import cv2
        import numpy as np

        cur_png = driver.screen.screenshot()
        old_png = baseline_path.read_bytes()

        old_arr = cv2.imdecode(np.frombuffer(old_png, dtype=np.uint8), cv2.IMREAD_COLOR)
        cur_arr = cv2.imdecode(np.frombuffer(cur_png, dtype=np.uint8), cv2.IMREAD_COLOR)

        if old_arr is None or cur_arr is None:
            return {"passed": False, "detail": {"error": "failed to decode images"}}

        if old_arr.shape != cur_arr.shape:
            cur_arr = cv2.resize(cur_arr, (old_arr.shape[1], old_arr.shape[0]))

        gray_old = cv2.cvtColor(old_arr, cv2.COLOR_BGR2GRAY)
        gray_cur = cv2.cvtColor(cur_arr, cv2.COLOR_BGR2GRAY)

        diff = cv2.absdiff(gray_old, gray_cur)
        non_zero = np.count_nonzero(diff)
        total = diff.size
        similarity = 1.0 - (non_zero / total) if total > 0 else 0.0

        passed = similarity >= threshold
        return {
            "passed": passed,
            "detail": {"similarity": round(similarity, 4), "threshold": threshold},
        }

    except ImportError:
        return {"passed": False, "detail": {"error": "cv2/numpy not available"}}


def _assert_activity_equals(driver: Any, params: dict) -> dict:
    """当前 Activity/Ability 匹配。"""
    expected = params.get("activity", "")
    if not expected:
        return {"passed": False, "detail": {"error": "activity required"}}

    try:
        info = driver.ui.get_current_activity()
    except Exception as e:
        return {"passed": False, "detail": {"error": str(e)}}

    actual_activity = info.get("activity", "")
    actual_package = info.get("package", "")
    # 可能匹配 activity 或 package
    passed = (
        _text_matches(expected, actual_activity)
        or _text_matches(expected, actual_package)
        or _text_matches(expected, f"{actual_package}/{actual_activity}")
    )
    return {"passed": passed, "detail": {"expected": expected, "actual": info}}


def _assert_app_foreground(driver: Any, params: dict) -> dict:
    """检查指定 package 是否在前台。"""
    package = params.get("package", "")
    if not package:
        return {"passed": False, "detail": {"error": "package required"}}

    try:
        info = driver.ui.get_current_activity()
    except Exception as e:
        return {"passed": False, "detail": {"error": str(e)}}

    actual = info.get("package", "")
    passed = _text_matches(package, actual)
    return {"passed": passed, "detail": {"expected": package, "actual": actual}}


def _assert_logcat_contains(driver: Any, params: dict, evidence_dir: pathlib.Path) -> dict:
    """日志包含模式。"""
    pattern = params.get("pattern", "")
    lines = int(params.get("lines", 5000))
    use_regex = params.get("regex", False)

    if not pattern:
        return {"passed": False, "detail": {"error": "pattern required"}}

    try:
        content = driver.read_log(lines=lines)
    except Exception as e:
        return {"passed": False, "detail": {"error": str(e)}}

    # 统一 re: 前缀或 regex 参数
    is_regex = use_regex or pattern.startswith("re:")
    if pattern.startswith("re:"):
        pattern = pattern[3:]

    hits: list[str] = []
    for line in content.splitlines():
        if is_regex:
            try:
                if re.search(pattern, line):
                    hits.append(line)
            except re.error:
                pass
        else:
            if pattern in line:
                hits.append(line)

    # 保存日志证据
    evidence_path = ""
    if evidence_dir:
        log_file = evidence_dir / "logcat_snapshot.txt"
        try:
            log_file.write_text(content, encoding="utf-8")
            evidence_path = str(log_file)
        except Exception:
            pass

    return {
        "passed": len(hits) > 0,
        "detail": {"pattern": pattern, "count": len(hits), "regex": is_regex},
        "evidence_path": evidence_path,
    }


def _assert_logcat_not_contains(driver: Any, params: dict, evidence_dir: pathlib.Path) -> dict:
    """日志不包含模式。"""
    result = _assert_logcat_contains(driver, params, evidence_dir)
    result["passed"] = not result["passed"]
    return result


def _assert_memory_snapshot(driver: Any, params: dict, evidence_dir: pathlib.Path) -> dict:
    """保存内存快照（作为后续对比的基线）。"""
    package = params.get("package", "")
    name = params.get("name", "memory_baseline")

    if not package:
        return {"passed": False, "detail": {"error": "package required"}}

    try:
        result = driver.dump_memory_profile(
            package, out_dir=str(evidence_dir), name=name, timeout_s=30.0,
        )
        return {
            "passed": result.get("ok", False),
            "detail": result,
        }
    except Exception as e:
        return {"passed": False, "detail": {"error": str(e)}}


def _assert_memory_no_growth(driver: Any, params: dict, evidence_dir: pathlib.Path) -> dict:
    """内存增长不超过阈值（需要先有 memory_snapshot 基线）。"""
    package = params.get("package", "")
    baseline_name = params.get("baseline_name", "")
    threshold_mb = float(params.get("threshold_mb", 50))

    if not package:
        return {"passed": False, "detail": {"error": "package required"}}

    # 获取当前内存 (通过 device driver 的 meminfo)
    try:
        result = driver.dump_memory_profile(
            package, out_dir=str(evidence_dir), name="current_check", timeout_s=30.0,
        )
        current_mb = result.get("total_pss_mb", 0) or result.get("pss_mb", 0)
    except Exception as e:
        return {"passed": False, "detail": {"error": f"current_memory: {e}"}}

    # 尝试读取基线
    baseline_mb = 0
    if baseline_name:
        baseline_dir = evidence_dir.parent if evidence_dir else pathlib.Path(".")
        for candidate in [
            baseline_dir / baseline_name / "meminfo.json",
            evidence_dir / f"{baseline_name}_meminfo.json" if evidence_dir else None,
        ]:
            if candidate and candidate.exists():
                try:
                    from phone_pilot.core.storage import read_json
                    data = read_json(candidate)
                    baseline_mb = data.get("total_pss_mb", 0) or data.get("pss_mb", 0)
                    break
                except Exception:
                    pass

    growth_mb = current_mb - baseline_mb if baseline_mb > 0 else current_mb
    passed = growth_mb <= threshold_mb

    return {
        "passed": passed,
        "detail": {
            "current_mb": current_mb,
            "baseline_mb": baseline_mb,
            "growth_mb": round(growth_mb, 2),
            "threshold_mb": threshold_mb,
        },
    }


def _assert_elapsed_under(driver: Any, params: dict) -> dict:
    """操作耗时不超过阈值（占位——由调用方提供 elapsed_ms）。"""
    elapsed_ms = float(params.get("elapsed_ms", 0))
    max_ms = float(params.get("max_ms", 5000))

    passed = elapsed_ms <= max_ms
    return {
        "passed": passed,
        "detail": {"elapsed_ms": elapsed_ms, "max_ms": max_ms},
    }


# ---------------------------------------------------------------------------
# 断言分发表
# ---------------------------------------------------------------------------

_ASSERTION_HANDLERS: dict[str, Any] = {
    "element_exists": _assert_element_exists,
    "element_not_exists": _assert_element_not_exists,
    "text_equals": _assert_text_equals,
    "text_contains": _assert_text_contains,
    "screen_contains_text": _assert_screen_contains_text,
    "activity_equals": _assert_activity_equals,
    "app_foreground": _assert_app_foreground,
    "elapsed_under": _assert_elapsed_under,
    # 以下需要 evidence_dir 参数
    "screenshot_match": None,  # 特殊处理
    "logcat_contains": None,
    "logcat_not_contains": None,
    "memory_snapshot": None,
    "memory_no_growth": None,
}


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def run_assertions(
    driver: Any,
    assertions: list[dict],
    *,
    evidence_dir: Optional[str] = None,
) -> dict:
    """批量执行断言。

    Parameters
    ----------
    driver : DeviceDriver
        设备驱动实例。
    assertions : list[dict]
        断言列表，每条包含 ``type`` 和对应参数。
    evidence_dir : str | None
        证据保存目录。默认 ``recordings/verify/<timestamp>/``。

    Returns
    -------
    dict
        ``{ok, passed, failed, total, results[], evidence_dir}``
    """
    # 准备证据目录
    if evidence_dir:
        ev_dir = pathlib.Path(evidence_dir)
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        ev_dir = recordings_root() / "verify" / ts
    ev_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    passed = 0
    failed = 0

    for i, assertion in enumerate(assertions):
        atype = assertion.get("type", "")
        t0 = time.monotonic()

        try:
            result = _dispatch_assertion(driver, atype, assertion, ev_dir)
        except Exception as exc:
            result = {"passed": False, "detail": {"error": str(exc)}}

        elapsed = (time.monotonic() - t0) * 1000

        entry = {
            "index": i,
            "type": atype,
            "passed": result.get("passed", False),
            "detail": result.get("detail", {}),
            "elapsed_ms": round(elapsed, 1),
        }
        if result.get("evidence_path"):
            entry["evidence_path"] = result["evidence_path"]

        results.append(entry)

        if entry["passed"]:
            passed += 1
        else:
            failed += 1

    total = passed + failed

    # 保存汇总报告
    summary = {
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "total": total,
        "timestamp": iso_now(),
        "results": results,
    }
    write_json(ev_dir / "verify_report.json", summary)

    return {
        "ok": failed == 0,
        "passed": passed,
        "failed": failed,
        "total": total,
        "results": results,
        "evidence_dir": str(ev_dir),
    }


def _dispatch_assertion(
    driver: Any,
    atype: str,
    params: dict,
    evidence_dir: pathlib.Path,
) -> dict:
    """分发并执行单条断言。"""
    # 需要 evidence_dir 的断言
    if atype == "screenshot_match":
        return _assert_screenshot_match(driver, params, evidence_dir)
    if atype == "logcat_contains":
        return _assert_logcat_contains(driver, params, evidence_dir)
    if atype == "logcat_not_contains":
        return _assert_logcat_not_contains(driver, params, evidence_dir)
    if atype == "memory_snapshot":
        return _assert_memory_snapshot(driver, params, evidence_dir)
    if atype == "memory_no_growth":
        return _assert_memory_no_growth(driver, params, evidence_dir)

    # 普通断言
    handler = _ASSERTION_HANDLERS.get(atype)
    if handler is None:
        return {"passed": False, "detail": {"error": f"unknown assertion type: {atype}"}}

    return handler(driver, params)
