#!/usr/bin/env python3
"""
Android dumpsys meminfo parser.
Android dumpsys meminfo 解析器。

Provides functionality to capture and parse memory info including:
提供捕获和解析内存信息的功能，包括：
- Native Heap
- Dalvik (Java) Heap
- Graphics (Bitmap pixels on Android 8.0+)
- GL mtrack
- EGL mtrack
- etc.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner


def capture_meminfo(
    device_serial: Optional[str],
    package_name: str,
    *,
    timeout_s: float = 30.0,
) -> dict:
    """
    Capture memory info using `adb shell dumpsys meminfo <package>`.
    使用 `adb shell dumpsys meminfo <package>` 捕获内存信息。

    This captures detailed memory breakdown including:
    捕获详细的内存分解，包括：
    - Native Heap: Native 堆内存（包含 Bitmap 像素在 Android 8.0+）
    - Dalvik Heap: Java 堆内存
    - Graphics: GPU/Graphics 相关内存（部分 Bitmap 相关）
    - GL mtrack / EGL mtrack: OpenGL 相关

    Args:
        device_serial: Device serial number / 设备序列号
        package_name: App package name / 应用包名
        timeout_s: Timeout in seconds / 超时时间（秒）

    Returns:
        dict with parsed memory info / 包含解析后内存信息的字典
    """
    if not package_name or not package_name.strip():
        return {"ok": False, "error": "package_name is required"}

    package_name = package_name.strip()

    try:
        cmd = adb_prefix(device_serial) + ["shell", "dumpsys", "meminfo", package_name]
        proc = CommandRunner.run(cmd, check=False, timeout_s=timeout_s, log_output=False)

        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "dumpsys meminfo failed",
                "returncode": proc.returncode,
                "stderr": proc.stderr,
                "package": package_name,
            }

        output = proc.stdout
        parsed = parse_meminfo(output)
        parsed["ok"] = True
        parsed["package"] = package_name
        parsed["raw_output"] = output
        parsed["captured_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        return parsed

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "package": package_name,
        }


def parse_meminfo(output: str) -> dict:
    """
    Parse the output of `dumpsys meminfo`.
    解析 `dumpsys meminfo` 的输出。

    Returns:
        dict with memory breakdown / 包含内存分解的字典
    """
    result: Dict[str, Any] = {
        "summary": {},
        "details": {},
        "app_summary": {},
        "objects": {},
    }

    lines = output.strip().split("\n")

    # Parse the main memory table
    # Format (varies by Android version/ROM):
    # Older format:
    #                    Pss  Private  Private  SwapPss     Heap     Heap     Heap
    #                  Total    Dirty    Clean    Dirty     Size    Alloc     Free
    # Newer format (with Rss):
    #                    Pss  Private  Private  SwapPss      Rss     Heap     Heap     Heap
    #                  Total    Dirty    Clean    Dirty    Total     Size    Alloc     Free
    #                 ------   ------   ------   ------   ------   ------   ------   ------
    #   Native Heap    70613    70560        8       83    71944    87076    81605     5470

    # More flexible pattern - capture category name and all numbers
    memory_line_pattern = re.compile(r"^\s*([A-Za-z][A-Za-z0-9\s\.]+?)\s+(\d+(?:\s+\d+)*)\s*$")

    in_memory_table = False
    for line in lines:
        # Check for table header
        # Header can be: "Pss  Private  Private  SwapPss" (new) or contain "Dirty" (old)
        if "Pss" in line and "Private" in line and ("Dirty" in line or "SwapPss" in line):
            in_memory_table = True
            continue

        if in_memory_table:
            # Check for separator line
            if line.strip().startswith("------"):
                continue

            # Check for end of table
            if line.strip() == "" or line.strip().startswith("TOTAL"):
                # Parse TOTAL line
                if "TOTAL" in line:
                    match = re.search(r"TOTAL[:\s]+(\d+)", line)
                    if match:
                        result["summary"]["total_pss_kb"] = int(match.group(1))
                in_memory_table = False
                continue

            # Parse memory category line
            match = memory_line_pattern.match(line)
            if match:
                category = match.group(1).strip()
                numbers_str = match.group(2)
                numbers = [int(n) for n in numbers_str.split()]

                if len(numbers) >= 4:
                    pss_total = numbers[0]
                    private_dirty = numbers[1]
                    private_clean = numbers[2]
                    swap_dirty = numbers[3]

                    result["details"][category] = {
                        "pss_total_kb": pss_total,
                        "private_dirty_kb": private_dirty,
                        "private_clean_kb": private_clean,
                        "swap_dirty_kb": swap_dirty,
                    }

                    # Check for Heap columns (usually at positions 5-7 or 6-8 depending on Rss column)
                    if len(numbers) >= 8:
                        # Has Rss column: [pss, priv_dirty, priv_clean, swap, rss, heap_size, heap_alloc, heap_free]
                        result["details"][category]["rss_total_kb"] = numbers[4]
                        result["details"][category]["heap_size_kb"] = numbers[5]
                        result["details"][category]["heap_alloc_kb"] = numbers[6]
                        result["details"][category]["heap_free_kb"] = numbers[7]
                    elif len(numbers) >= 7:
                        # No Rss column: [pss, priv_dirty, priv_clean, swap, heap_size, heap_alloc, heap_free]
                        result["details"][category]["heap_size_kb"] = numbers[4]
                        result["details"][category]["heap_alloc_kb"] = numbers[5]
                        result["details"][category]["heap_free_kb"] = numbers[6]
                    elif len(numbers) >= 5:
                        # Has Rss but no Heap columns
                        result["details"][category]["rss_total_kb"] = numbers[4]

    # Parse App Summary section
    # Format:
    #  App Summary
    #                        Pss(KB)
    #                         ------
    #            Java Heap:    12732
    #          Native Heap:    58272
    #                 Code:    42936
    #                Stack:     1568
    #             Graphics:    32456
    #        Private Other:     8064
    #               System:    14832
    #              TOTAL PSS:   170860

    app_summary_pattern = re.compile(r"^\s*([\w\s]+):\s+(\d+)\s*$")
    in_app_summary = False

    for line in lines:
        if "App Summary" in line:
            in_app_summary = True
            continue

        if in_app_summary:
            if line.strip().startswith("------"):
                continue
            if "TOTAL" in line:
                match = re.search(r"TOTAL\s+(?:PSS|RSS)[:\s]+(\d+)", line, re.IGNORECASE)
                if match:
                    result["app_summary"]["total_pss_kb"] = int(match.group(1))
                in_app_summary = False
                continue

            match = app_summary_pattern.match(line)
            if match:
                category = match.group(1).strip()
                value_kb = int(match.group(2))
                result["app_summary"][category] = value_kb

    # Parse Objects section
    # Format:
    #  Objects
    #                Views:      384         ViewRootImpl:        1
    #          AppContexts:        6           Activities:        2
    #               Assets:       12        AssetManagers:        0
    #        Local Binders:       67        Proxy Binders:       53
    #        Parcel memory:       10         Parcel count:       43
    #     Death Recipients:        2
    #      WebViews:        0

    objects_pattern = re.compile(r"(\w+[\w\s]*):\s+(\d+)")
    in_objects = False

    for line in lines:
        if line.strip() == "Objects":
            in_objects = True
            continue

        if in_objects:
            if line.strip() == "":
                in_objects = False
                continue

            # Find all key-value pairs on the line
            matches = objects_pattern.findall(line)
            for key, value in matches:
                key = key.strip()
                result["objects"][key] = int(value)

    # Extract key metrics for easy access
    result["summary"]["native_heap_kb"] = result["details"].get("Native Heap", {}).get("pss_total_kb", 0)
    result["summary"]["dalvik_heap_kb"] = result["details"].get("Dalvik Heap", {}).get("pss_total_kb", 0)
    result["summary"]["graphics_kb"] = result["details"].get("Graphics", {}).get("pss_total_kb", 0)
    result["summary"]["gl_mtrack_kb"] = result["details"].get("GL mtrack", {}).get("pss_total_kb", 0)
    result["summary"]["egl_mtrack_kb"] = result["details"].get("EGL mtrack", {}).get("pss_total_kb", 0)

    # Also try from app_summary
    if result["app_summary"]:
        if "Native Heap" in result["app_summary"]:
            result["summary"]["native_heap_kb"] = result["app_summary"]["Native Heap"]
        if "Java Heap" in result["app_summary"]:
            result["summary"]["java_heap_kb"] = result["app_summary"]["Java Heap"]
        if "Graphics" in result["app_summary"]:
            result["summary"]["graphics_kb"] = result["app_summary"]["Graphics"]

    # Convert to MB for convenience
    result["summary"]["native_heap_mb"] = round(result["summary"].get("native_heap_kb", 0) / 1024, 2)
    result["summary"]["dalvik_heap_mb"] = round(result["summary"].get("dalvik_heap_kb", 0) / 1024, 2)
    result["summary"]["java_heap_mb"] = round(result["summary"].get("java_heap_kb", 0) / 1024, 2)
    result["summary"]["graphics_mb"] = round(result["summary"].get("graphics_kb", 0) / 1024, 2)
    result["summary"]["total_pss_mb"] = round(result["summary"].get("total_pss_kb", 0) / 1024, 2)

    return result


def format_meminfo(snapshot: dict, *, label: str = "MEM") -> str:
    """将 meminfo 快照格式化为一行摘要字符串。

    Args:
        snapshot: capture_meminfo 的返回值
        label: 前缀标签，如 "BEFORE" / "AFTER"

    Returns:
        格式化字符串，例如:
        [MEM-BEFORE] total_pss=166.86MB  native=56.91MB  java=12.43MB  graphics=31.70MB
    """
    if not snapshot.get("ok"):
        return f"[{label}] 采集失败: {snapshot.get('error', '未知错误')}"
    s = snapshot.get("summary", {})
    return (
        f"[{label}] "
        f"total_pss={s.get('total_pss_mb', '?')}MB  "
        f"native={s.get('native_heap_mb', '?')}MB  "
        f"java={s.get('java_heap_mb', '?')}MB  "
        f"graphics={s.get('graphics_mb', '?')}MB"
    )


def format_diff(diff_result: dict, *, threshold_mb: float = 30.0) -> str:
    """将 diff_meminfo 的结果格式化为可读字符串（含泄漏判断）。

    Args:
        diff_result: diff_meminfo 的返回值
        threshold_mb: 内存增长超过此值时标记为可疑泄漏

    Returns:
        多行格式化字符串，包含增量和泄漏判断
    """
    d = diff_result.get("diff", {})
    lines = [
        f"[MEM-DIFF]   "
        f"Δtotal_pss={d.get('total_pss_mb', 0):+.2f}MB  "
        f"Δnative={d.get('native_heap_mb', 0):+.2f}MB  "
        f"Δjava={d.get('java_heap_mb', 0):+.2f}MB  "
        f"Δgraphics={d.get('graphics_mb', 0):+.2f}MB"
    ]
    total_growth = d.get("total_pss_mb", 0)
    if total_growth > threshold_mb:
        lines.append(
            f"[MEM-WARN]   ⚠ 内存增长 {total_growth:.2f}MB 超过阈值 "
            f"{threshold_mb}MB，疑似内存泄漏！"
        )
    else:
        lines.append(f"[MEM-OK]     内存增长 {total_growth:.2f}MB，在合理范围内")
    return "\n".join(lines)


def _summary_mb(snapshot: dict) -> dict:
    """从 meminfo 快照中提取只保留 MB 的精简 summary。"""
    s = snapshot.get("summary", {})
    return {
        "total_pss_mb": s.get("total_pss_mb", 0),
        "native_heap_mb": s.get("native_heap_mb", 0),
        "java_heap_mb": s.get("java_heap_mb", 0),
        "dalvik_heap_mb": s.get("dalvik_heap_mb", 0),
        "graphics_mb": s.get("graphics_mb", 0),
    }


def diff_meminfo(before: dict, after: dict) -> dict:
    """比较两个 meminfo 快照，返回精简 diff（仅 MB + 百分比）。"""
    bs = before.get("summary", {})
    as_ = after.get("summary", {})

    diff: Dict[str, Any] = {}
    for key in ["native_heap", "dalvik_heap", "java_heap", "graphics", "total_pss"]:
        bv = bs.get(f"{key}_kb", 0)
        av = as_.get(f"{key}_kb", 0)
        delta_kb = av - bv
        diff[f"{key}_mb"] = round(delta_kb / 1024, 2)
        diff[f"{key}_percent"] = round(delta_kb / bv * 100, 2) if bv > 0 else 0

    return {
        "ok": True,
        "before": {
            "package": before.get("package"),
            "captured_at": before.get("captured_at"),
            **_summary_mb(before),
        },
        "after": {
            "package": after.get("package"),
            "captured_at": after.get("captured_at"),
            **_summary_mb(after),
        },
        "diff": diff,
    }


# ---------------------------------------------------------------------------
# Activity 泄漏检测
# ---------------------------------------------------------------------------

def _get_activity_count_from_meminfo(snapshot: dict) -> int:
    """从 meminfo 快照的 Objects 段获取 Activity 实例数。"""
    return snapshot.get("objects", {}).get("Activities", 0)


def _get_visible_activities(
    device_serial: Optional[str],
    package_name: str,
) -> list[str]:
    """通过 dumpsys activity 获取当前可见的 Activity 列表。"""
    cmd = adb_prefix(device_serial) + [
        "shell", "dumpsys", "activity", "activities"
    ]
    proc = CommandRunner.run(cmd, check=False, timeout_s=15.0, log_output=False)
    if proc.returncode != 0:
        return []
    output = proc.stdout or ""
    activities = []
    for line in output.splitlines():
        line = line.strip()
        # 匹配类似: * TaskRecord{...} ... com.example/.MainActivity
        # 或: Activities=[ActivityRecord{...} ... com.example/.MainActivity t12}]
        if package_name in line and ("ActivityRecord" in line or "Activity" in line):
            # 提取 Activity 名
            import re as _re
            m = _re.search(rf'{_re.escape(package_name)}/([^\s\}},]+)', line)
            if m:
                act_name = m.group(1)
                full = f"{package_name}/{act_name}" if act_name.startswith(".") else act_name
                if full not in activities:
                    activities.append(full)
    return activities


def check_activity_leak(
    device_serial: Optional[str],
    package_name: str,
    *,
    gc_wait_s: float = 5.0,
    save_hprof: bool = True,
    hprof_out_dir: Optional[str] = None,
) -> dict:
    """Activity 泄漏检测：采集 → GC → 等待 → 再验证 → 可选 hprof dump。

    检测流程：
    1. dumpsys meminfo 获取内存中 Activity 实例数 (Objects.Activities)
    2. dumpsys activity 获取当前可见的 Activity 栈
    3. 若 内存中 Activity 数 > 可见 Activity 数 → 疑似泄漏
    4. 触发 GC → 等待 gc_wait_s → 再次 dumpsys meminfo
    5. 若仍然多出来 → 确认泄漏
    6. 可选：dump hprof 并从中找出哪些 Activity 类还存活

    Args:
        device_serial: 设备序列号
        package_name: 应用包名
        gc_wait_s: GC 后等待时间（秒），默认 5
        save_hprof: 泄漏确认后是否 dump hprof，默认 True
        hprof_out_dir: hprof 文件存储目录，None 则用默认

    Returns:
        dict，包含 ok、leaked、detail 等字段
    """
    from phone_pilot.memory_analyze.dumper import trigger_gc, dump_hprof

    result: Dict[str, Any] = {
        "ok": True,
        "leaked": False,
        "package": package_name,
    }

    # Step 1: 第一次采集 meminfo
    snap1 = capture_meminfo(device_serial, package_name)
    if not snap1.get("ok"):
        result["ok"] = False
        result["error"] = f"meminfo 采集失败: {snap1.get('error')}"
        return result

    mem_activities = _get_activity_count_from_meminfo(snap1)
    visible = _get_visible_activities(device_serial, package_name)
    visible_count = len(visible)

    result["initial"] = {
        "activities_in_memory": mem_activities,
        "visible_activities": visible,
        "visible_count": visible_count,
    }

    # 无泄漏嫌疑
    if mem_activities <= visible_count:
        result["verdict"] = "正常：内存中 Activity 数 ≤ 可见 Activity 数"
        return result

    # Step 2: 疑似泄漏 → 触发 GC
    suspect_count = mem_activities - visible_count
    result["suspect"] = f"疑似泄漏 {suspect_count} 个 Activity（内存 {mem_activities} > 可见 {visible_count}）"

    gc_result = trigger_gc(device_serial, package_name, wait_s=gc_wait_s)
    result["gc"] = {
        "triggered": gc_result.get("ok", False),
        "wait_s": gc_wait_s,
        "methods": gc_result.get("methods_tried", []),
    }

    # Step 3: GC 后再次采集
    snap2 = capture_meminfo(device_serial, package_name)
    if not snap2.get("ok"):
        result["error"] = "GC 后 meminfo 采集失败"
        return result

    mem_activities_after_gc = _get_activity_count_from_meminfo(snap2)
    visible_after = _get_visible_activities(device_serial, package_name)
    visible_count_after = len(visible_after)

    result["after_gc"] = {
        "activities_in_memory": mem_activities_after_gc,
        "visible_activities": visible_after,
        "visible_count": visible_count_after,
    }

    # 判定
    leak_count = mem_activities_after_gc - visible_count_after
    if leak_count <= 0:
        result["verdict"] = f"GC 后恢复正常（{mem_activities} → {mem_activities_after_gc}），非真实泄漏"
        return result

    # Step 4: 确认泄漏
    result["leaked"] = True
    result["leak_count"] = leak_count
    result["verdict"] = (
        f"确认泄漏 {leak_count} 个 Activity"
        f"（GC 前 {mem_activities}，GC 后 {mem_activities_after_gc}，可见 {visible_count_after}）"
    )

    # Step 5: 可选 dump hprof 获取泄漏详情
    if save_hprof:
        out_dir = hprof_out_dir or "./.recordings"
        hprof_result = dump_hprof(
            device_serial, package_name,
            out_dir=out_dir, hprof_subdir="", name="leak_dump",
        )
        result["hprof"] = {
            "dumped": hprof_result.get("ok", False),
            "path": hprof_result.get("path"),
            "size_bytes": hprof_result.get("size_bytes"),
            "error": hprof_result.get("error"),
        }

        # 从 hprof 中分析存活的 Activity 类
        if hprof_result.get("ok") and hprof_result.get("path"):
            try:
                from phone_pilot.memory_analyze.analyzer import analyze_hprof
                analysis = analyze_hprof(hprof_result["path"])
                if analysis.get("ok"):
                    # 找出所有 Activity 子类的实例
                    activity_classes = [
                        s for s in analysis.get("stats", [])
                        if "Activity" in s.get("class_name", "")
                        and s.get("instance_count", 0) > 0
                    ]
                    result["leaked_activities"] = activity_classes
            except Exception as e:
                result["hprof_analysis_error"] = str(e)

    return result


def format_leak_report(report: dict) -> str:
    """将 check_activity_leak 的结果格式化为可读字符串。"""
    if not report.get("ok"):
        return f"[LEAK-ERROR] {report.get('error', '未知错误')}"

    lines = []
    initial = report.get("initial", {})
    lines.append(
        f"[LEAK-CHECK] Activity 检测: "
        f"内存中 {initial.get('activities_in_memory', '?')} 个, "
        f"可见 {initial.get('visible_count', '?')} 个"
    )

    if not report.get("leaked"):
        verdict = report.get("verdict", "无泄漏")
        lines.append(f"[LEAK-OK]    {verdict}")
        return "\n".join(lines)

    # 有泄漏
    lines.append(f"[LEAK-WARN]  ⚠ {report.get('verdict', '')}")
    after_gc = report.get("after_gc", {})
    lines.append(
        f"[LEAK-GC]    GC 后: 内存中 {after_gc.get('activities_in_memory', '?')} 个, "
        f"可见 {after_gc.get('visible_count', '?')} 个"
    )

    leaked_acts = report.get("leaked_activities", [])
    if leaked_acts:
        lines.append("[LEAK-DETAIL] 泄漏的 Activity 类:")
        for act in leaked_acts[:10]:
            lines.append(
                f"  - {act.get('class_name', '?')}: "
                f"{act.get('instance_count', 0)} 个实例, "
                f"共 {act.get('total_size', 0)} bytes"
            )

    hprof = report.get("hprof", {})
    if hprof.get("dumped"):
        lines.append(f"[LEAK-HPROF] hprof 已保存: {hprof.get('path', '?')}")

    return "\n".join(lines)
