#!/usr/bin/env python3
"""
Memory Analysis Module for Android.
Android 内存分析模块。

This module provides tools for:
本模块提供以下功能：
- Dumping heap memory (hprof) from Android apps / 从 Android 应用 dump 堆内存
- Analyzing hprof files / 分析 hprof 文件
- Memory diff between two snapshots / 两个快照之间的内存差异
- Large object statistics / 大对象统计
- Bitmap analysis (native pixel memory estimation) / Bitmap 分析（native 像素内存估算）
- dumpsys meminfo parsing / dumpsys meminfo 解析
"""

from phone_pilot.memory_analyze.dumper import (
    dump_hprof,
    pull_hprof,
    trigger_gc,
)
from phone_pilot.memory_analyze.analyzer import (
    analyze_hprof,
    diff_hprof,
    get_large_objects,
)
from phone_pilot.memory_analyze.meminfo import (
    capture_meminfo,
    parse_meminfo,
    diff_meminfo,
    format_meminfo,
    format_diff,
    check_activity_leak,
    format_leak_report,
)
from phone_pilot.memory_analyze.bitmap_analyzer import (
    analyze_bitmaps,
    diff_bitmaps,
)
from phone_pilot.memory_analyze.heapprofd import (
    check_heapprofd_support,
    start_heapprofd,
    stop_heapprofd,
    analyze_heapprofd_trace,
    run_heapprofd_session,
    import_android_studio_hprof,
    HeapprofdConfig,
)
from phone_pilot.memory_analyze.shark_analyzer import (
    analyze_with_shark,
    is_shark_available,
)
from phone_pilot.memory_analyze.sampler import MemInfoSampler
from phone_pilot.memory_analyze.perfetto_profiler import (
    check_perfetto_support,
    start_heap_profile,
    stop_and_analyze as stop_and_analyze_perfetto,
)

__all__ = [
    # Dumper
    "dump_hprof",
    "pull_hprof",
    "trigger_gc",
    # Analyzer
    "analyze_hprof",
    "diff_hprof",
    "get_large_objects",
    # Meminfo
    "capture_meminfo",
    "parse_meminfo",
    "diff_meminfo",
    "format_meminfo",
    "format_diff",
    "check_activity_leak",
    "format_leak_report",
    # Bitmap
    "analyze_bitmaps",
    "diff_bitmaps",
    # Heapprofd (Native heap profiling)
    "check_heapprofd_support",
    "start_heapprofd",
    "stop_heapprofd",
    "analyze_heapprofd_trace",
    "run_heapprofd_session",
    "import_android_studio_hprof",
    "HeapprofdConfig",
    # Shark CLI (optional external tool)
    "analyze_with_shark",
    "is_shark_available",
    # Sampler (background thread)
    "MemInfoSampler",
    # Perfetto profiler
    "check_perfetto_support",
    "start_heap_profile",
    "stop_and_analyze_perfetto",
]
