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
from typing import Any, Dict, List, Optional

from android_tool.adb_utils import adb_prefix
from android_tool.runner import CommandRunner


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


def diff_meminfo(before: dict, after: dict) -> dict:
    """
    Compare two meminfo snapshots.
    比较两个 meminfo 快照。

    Args:
        before: Before meminfo snapshot / 之前的 meminfo 快照
        after: After meminfo snapshot / 之后的 meminfo 快照

    Returns:
        dict with diff results / 包含差异结果的字典
    """
    result = {
        "ok": True,
        "before": {
            "package": before.get("package"),
            "captured_at": before.get("captured_at"),
            "summary": before.get("summary", {}),
        },
        "after": {
            "package": after.get("package"),
            "captured_at": after.get("captured_at"),
            "summary": after.get("summary", {}),
        },
        "diff": {},
    }

    # Calculate diff for summary metrics
    before_summary = before.get("summary", {})
    after_summary = after.get("summary", {})

    for key in ["native_heap_kb", "dalvik_heap_kb", "java_heap_kb", "graphics_kb", "total_pss_kb"]:
        before_val = before_summary.get(key, 0)
        after_val = after_summary.get(key, 0)
        diff_val = after_val - before_val
        result["diff"][key] = diff_val
        result["diff"][key.replace("_kb", "_mb")] = round(diff_val / 1024, 2)

    # Calculate percentages
    for key in ["native_heap", "dalvik_heap", "java_heap", "graphics", "total_pss"]:
        before_val = before_summary.get(f"{key}_kb", 0)
        diff_val = result["diff"].get(f"{key}_kb", 0)
        if before_val > 0:
            result["diff"][f"{key}_percent"] = round(diff_val / before_val * 100, 2)
        else:
            result["diff"][f"{key}_percent"] = 0

    return result
