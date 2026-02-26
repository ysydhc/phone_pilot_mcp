#!/usr/bin/env python3
"""Perfetto heapprofd 深度内存分析集成。
Perfetto heapprofd deep memory analysis integration.

Perfetto（5.5k stars）是 Google 官方的 Android 系统级性能分析工具。
heapprofd 是其中的 Native 堆分析器，可追踪每次 malloc/free 并生成调用栈。

技术选型理由 / Why Perfetto:
1. Android 官方标准: Android 10+ 内置，无需额外安装
2. Python API 成熟: `pip install perfetto` 即可通过 SQL 查询 trace 数据
3. 调用栈追踪: 能定位到函数级别的内存分配（dumpsys meminfo 做不到）
4. 火焰图: 内置可视化，可嵌入 HTML 报告
5. 低开销: Poisson 采样机制，对应用性能影响极小

对比 / Comparison:
- dumpsys meminfo: 进程级汇总，适合快速概览（保留）
- heapprofd (Perfetto): 函数级追踪，适合深度分析 Native 层（新增）
- simpleperf: CPU profiling，不支持内存分析（排除）

Requirements:
- Android 10+ (API 29+)
- `pip install perfetto` (optional, for trace_processor analysis)

Usage:
    from phone_pilot.memory_analyze.perfetto_profiler import (
        check_perfetto_support, start_heap_profile, stop_and_analyze,
    )
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import time
import uuid
from typing import Any, Optional

from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner
from phone_pilot.core.log import _log


def check_perfetto_support(device_serial: Optional[str] = None) -> dict:
    """检查设备是否支持 Perfetto heapprofd（Android 10+）。
    Check if device supports Perfetto heapprofd (Android 10+).

    Returns:
        {
            "supported": bool,
            "api_level": int,
            "has_perfetto": bool,
            "has_python_lib": bool,
            "requirements": [str],
        }
    """
    result: dict[str, Any] = {
        "supported": False,
        "api_level": 0,
        "has_perfetto": False,
        "has_python_lib": False,
        "requirements": [],
    }

    # Check API level
    try:
        cmd = adb_prefix(device_serial) + ["shell", "getprop", "ro.build.version.sdk"]
        proc = CommandRunner.run(cmd, check=False, timeout_s=10, log_output=False)
        if proc.returncode == 0:
            result["api_level"] = int(proc.stdout.strip())
    except Exception:
        pass

    if result["api_level"] < 29:
        result["requirements"].append("需要 Android 10+ (API 29+)")
        return result

    # Check perfetto binary on device
    try:
        cmd = adb_prefix(device_serial) + ["shell", "which", "perfetto"]
        proc = CommandRunner.run(cmd, check=False, timeout_s=10, log_output=False)
        result["has_perfetto"] = proc.returncode == 0
    except Exception:
        pass

    if not result["has_perfetto"]:
        result["requirements"].append("设备上未找到 perfetto 命令")

    # Check Python perfetto library
    try:
        import perfetto  # noqa: F401
        result["has_python_lib"] = True
    except ImportError:
        result["requirements"].append("pip install perfetto (用于 trace 分析)")

    result["supported"] = result["has_perfetto"]
    return result


def _generate_perfetto_config(
    package_name: str,
    *,
    duration_ms: int = 30000,
    sampling_interval_bytes: int = 4096,
    shmem_size_bytes: int = 8 * 1024 * 1024,
) -> str:
    """生成 Perfetto 配置（protobuf text format）。"""
    return f"""
buffers: {{
    size_kb: 65536
    fill_policy: RING_BUFFER
}}
data_sources: {{
    config {{
        name: "android.heapprofd"
        heapprofd_config {{
            process_cmdline: "{package_name}"
            sampling_interval_bytes: {sampling_interval_bytes}
            continuous_dump_config {{
                dump_phase_ms: 0
                dump_interval_ms: 5000
            }}
            shmem_size_bytes: {shmem_size_bytes}
            all_heaps: true
            block_client: true
        }}
    }}
}}
duration_ms: {duration_ms}
""".strip()


def start_heap_profile(
    device_serial: str,
    package_name: str,
    *,
    duration_s: float = 30.0,
    sampling_interval_bytes: int = 4096,
    out_dir: str = ".recordings",
) -> dict:
    """启动 Perfetto heapprofd 采集。
    Start Perfetto heapprofd collection.

    Parameters:
        device_serial: 设备序列号
        package_name: 应用包名
        duration_s: 采集时长秒数（0=跟随脚本全程，需手动 stop）
        sampling_interval_bytes: 采样间隔字节数（默认 4KB）
        out_dir: 输出目录

    Returns:
        {"ok": bool, "trace_file": str (remote path), "config_file": str, ...}
    """
    if not package_name:
        return {"ok": False, "error": "package_name required"}

    # Check support
    support = check_perfetto_support(device_serial)
    if not support.get("supported"):
        return {"ok": False, "error": "Perfetto 不可用", "requirements": support.get("requirements", [])}

    duration_ms = int(duration_s * 1000) if duration_s > 0 else 600000  # default 10min if 0

    # Generate config
    config_text = _generate_perfetto_config(
        package_name,
        duration_ms=duration_ms,
        sampling_interval_bytes=sampling_interval_bytes,
    )

    # Write config to temp file and push to device
    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".cfg") as f:
        f.write(config_text)
        local_cfg = f.name

    session_id = uuid.uuid4().hex[:8]
    remote_cfg = f"/data/local/tmp/perfetto_cfg_{session_id}.cfg"
    remote_trace = f"/data/local/tmp/perfetto_trace_{session_id}.pb"

    try:
        # Push config
        push_cmd = adb_prefix(device_serial) + ["push", local_cfg, remote_cfg]
        proc = CommandRunner.run(push_cmd, check=False, timeout_s=30, log_output=False)
        if proc.returncode != 0:
            return {"ok": False, "error": f"push config failed: {proc.stderr}"}

        # Start perfetto (background)
        start_cmd = adb_prefix(device_serial) + [
            "shell", "perfetto",
            "-c", remote_cfg,
            "-o", remote_trace,
            "--background",
        ]
        proc = CommandRunner.run(start_cmd, check=False, timeout_s=10, log_output=False)
        if proc.returncode != 0:
            return {"ok": False, "error": f"perfetto start failed: {proc.stderr}"}

        _log(f"[perfetto] heapprofd 采集已启动: {package_name} (间隔 {sampling_interval_bytes}B)")
        return {
            "ok": True,
            "trace_file": remote_trace,
            "config_file": remote_cfg,
            "session_id": session_id,
            "duration_s": duration_s,
        }

    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try:
            os.unlink(local_cfg)
        except Exception:
            pass


def stop_and_analyze(
    device_serial: str,
    trace_file: str,
    package_name: str,
    *,
    out_dir: Optional[str] = None,
) -> dict:
    """停止 Perfetto 采集并用 trace_processor 分析。
    Stop Perfetto collection and analyze with trace_processor.

    Parameters:
        device_serial: 设备序列号
        trace_file: 设备上的 trace 文件路径（remote）
        package_name: 应用包名
        out_dir: 本地输出目录

    Returns:
        {
            "ok": bool,
            "allocations": {"total_allocated_bytes": int, "total_freed_bytes": int, "net_bytes": int},
            "top_allocators": [{"callsite": str, "bytes": int, "count": int}],
            "timeline": [{"ts_ms": float, "rss_mb": float, "alloc_mb": float}],
            "trace_file": str (local path),
        }
    """
    if not trace_file:
        return {"ok": False, "error": "trace_file required"}

    # Wait for perfetto to finish writing
    time.sleep(2.0)

    # Pull trace file
    if out_dir:
        local_dir = pathlib.Path(out_dir).expanduser().resolve()
    else:
        local_dir = pathlib.Path(".recordings/perfetto").expanduser().resolve()
    local_dir.mkdir(parents=True, exist_ok=True)

    local_trace = str(local_dir / pathlib.Path(trace_file).name)

    try:
        pull_cmd = adb_prefix(device_serial) + ["pull", trace_file, local_trace]
        proc = CommandRunner.run(pull_cmd, check=False, timeout_s=120, log_output=False)
        if proc.returncode != 0:
            return {"ok": False, "error": f"pull trace failed: {proc.stderr}"}
    except Exception as e:
        return {"ok": False, "error": f"pull exception: {e}"}

    # Check file size
    local_path = pathlib.Path(local_trace)
    if not local_path.is_file() or local_path.stat().st_size == 0:
        return {"ok": False, "error": "trace file is empty", "trace_file": local_trace}

    _log(f"[perfetto] trace 已拉取: {local_trace} ({local_path.stat().st_size // 1024}KB)")

    # Analyze with trace_processor (if available)
    analysis = _analyze_with_trace_processor(local_trace, package_name)

    # Clean up remote files
    try:
        rm_cmd = adb_prefix(device_serial) + ["shell", "rm", "-f", trace_file]
        CommandRunner.run(rm_cmd, check=False, timeout_s=10, log_output=False)
    except Exception:
        pass

    analysis["trace_file"] = local_trace
    return analysis


def _analyze_with_trace_processor(trace_file: str, package_name: str) -> dict:
    """使用 Perfetto trace_processor Python API 分析 trace 文件。
    Analyze trace file using Perfetto trace_processor Python API.
    """
    result: dict[str, Any] = {
        "ok": True,
        "allocations": {"total_allocated_bytes": 0, "total_freed_bytes": 0, "net_bytes": 0},
        "top_allocators": [],
        "timeline": [],
    }

    try:
        from perfetto.trace_processor import TraceProcessor
    except ImportError:
        result["error"] = "perfetto Python 库未安装 (pip install perfetto)"
        _log("[perfetto] trace_processor 不可用，跳过深度分析")
        return result

    try:
        tp = TraceProcessor(trace=trace_file)

        # Query 1: Total allocations
        try:
            alloc_query = """
                SELECT
                    SUM(CASE WHEN size > 0 THEN size ELSE 0 END) as total_alloc,
                    SUM(CASE WHEN size < 0 THEN ABS(size) ELSE 0 END) as total_free,
                    SUM(size) as net
                FROM heap_profile_allocation
            """
            df = tp.query(alloc_query)
            if hasattr(df, "as_pandas_dataframe"):
                pdf = df.as_pandas_dataframe()
                if not pdf.empty:
                    result["allocations"] = {
                        "total_allocated_bytes": int(pdf.iloc[0].get("total_alloc", 0) or 0),
                        "total_freed_bytes": int(pdf.iloc[0].get("total_free", 0) or 0),
                        "net_bytes": int(pdf.iloc[0].get("net", 0) or 0),
                    }
        except Exception:
            # Table may not exist if no allocations were captured
            pass

        # Query 2: Top allocators by callsite
        try:
            top_query = """
                SELECT
                    f.name as callsite,
                    f.mapping_name as library,
                    SUM(f.self_size) as bytes,
                    COUNT(*) as count
                FROM heap_profile_allocation f
                WHERE f.self_size > 0
                GROUP BY f.name, f.mapping_name
                ORDER BY bytes DESC
                LIMIT 20
            """
            df = tp.query(top_query)
            if hasattr(df, "as_pandas_dataframe"):
                pdf = df.as_pandas_dataframe()
                for _, row in pdf.iterrows():
                    callsite = str(row.get("callsite", ""))
                    library = str(row.get("library", ""))
                    label = f"{library}!{callsite}" if library and library != "None" else callsite
                    result["top_allocators"].append({
                        "callsite": label,
                        "bytes": int(row.get("bytes", 0) or 0),
                        "count": int(row.get("count", 0) or 0),
                    })
        except Exception:
            pass

        # Query 3: Memory timeline (RSS over time)
        try:
            timeline_query = """
                SELECT ts, value as bytes
                FROM counter c
                JOIN process_counter_track t ON c.track_id = t.id
                WHERE t.name = 'mem.rss'
                ORDER BY ts
            """
            df = tp.query(timeline_query)
            if hasattr(df, "as_pandas_dataframe"):
                pdf = df.as_pandas_dataframe()
                if not pdf.empty:
                    ts_start = pdf.iloc[0].get("ts", 0)
                    for _, row in pdf.iterrows():
                        ts_ns = row.get("ts", 0)
                        result["timeline"].append({
                            "ts_ms": round((ts_ns - ts_start) / 1_000_000, 1),
                            "rss_mb": round(row.get("bytes", 0) / (1024 * 1024), 2),
                        })
        except Exception:
            pass

        tp.close()
        _log(f"[perfetto] 分析完成: {len(result['top_allocators'])} 个热点, {len(result['timeline'])} 个时间点")

    except Exception as e:
        result["error"] = f"trace_processor 分析异常: {e}"
        _log(f"[perfetto] 分析异常: {e}")

    return result
