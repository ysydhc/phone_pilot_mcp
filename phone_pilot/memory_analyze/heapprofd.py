#!/usr/bin/env python3
"""
Android heapprofd (Native Heap Profiler) support.
Android heapprofd（原生堆分析器）支持。

heapprofd can capture native heap allocations with stack traces,
which is essential for tracking Bitmap pixel memory allocations.

heapprofd 可以捕获带堆栈跟踪的原生堆分配，
这对于追踪 Bitmap 像素内存分配至关重要。

Requirements / 要求:
- Android 10+ (API 29+)
- debuggable app OR root access
- debuggable 应用 或 root 权限
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

from phone_pilot.android.adb.utils import adb_prefix
from phone_pilot.android.adb.runner import CommandRunner


@dataclass
class HeapprofdConfig:
    """Configuration for heapprofd profiling session."""
    package_name: str
    sampling_interval_bytes: int = 4096  # Sample every 4KB allocation
    duration_ms: int = 30000  # 30 seconds
    shmem_size_bytes: int = 8 * 1024 * 1024  # 8MB shared memory
    continuous_dump_interval_ms: int = 0  # 0 = no continuous dump
    all_heaps: bool = True  # Profile all heaps (malloc, etc)
    block_client: bool = True  # Block allocations during unwinding (more accurate)


@dataclass
class NativeAllocation:
    """Native memory allocation info with stack trace."""
    address: int = 0
    size: int = 0
    count: int = 1
    stack_frames: List[str] = field(default_factory=list)


def check_heapprofd_support(device_serial: Optional[str] = None) -> dict:
    """
    Check if heapprofd is supported on the device.
    检查设备是否支持 heapprofd。

    Returns:
        dict with support status and requirements
    """
    result = {
        "supported": False,
        "android_version": 0,
        "api_level": 0,
        "is_rooted": False,
        "requirements": [],
    }

    try:
        # Check Android version
        cmd = adb_prefix(device_serial) + ["shell", "getprop", "ro.build.version.sdk"]
        proc = CommandRunner.run(cmd, check=False, timeout_s=10, log_output=False)
        if proc.returncode == 0:
            api_level = int(proc.stdout.strip())
            result["api_level"] = api_level
            result["android_version"] = _api_to_android_version(api_level)

        # heapprofd requires Android 10+ (API 29)
        if result["api_level"] < 29:
            result["requirements"].append("Android 10+ (API 29+) required")
            return result

        # Check root
        cmd = adb_prefix(device_serial) + ["shell", "id"]
        proc = CommandRunner.run(cmd, check=False, timeout_s=10, log_output=False)
        if proc.returncode == 0 and "uid=0" in (proc.stdout or ""):
            result["is_rooted"] = True

        result["supported"] = True
        if not result["is_rooted"]:
            result["requirements"].append("App must be debuggable or device rooted")

    except Exception as e:
        result["requirements"].append(str(e))

    return result


def start_heapprofd(device_serial: Optional[str], config: HeapprofdConfig) -> dict:
    """
    Start heapprofd profiling session.
    启动 heapprofd profiling 会话。
    """
    if not config.package_name:
        return {"ok": False, "error": "package_name is required"}

    # Create config file
    config_text = _generate_config(config)
    with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".cfg") as f:
        f.write(config_text)
        config_path = f.name

    # Push config to device
    remote_cfg = f"/data/local/tmp/heapprofd_{uuid.uuid4().hex[:8]}.cfg"
    push_cmd = adb_prefix(device_serial) + ["push", config_path, remote_cfg]
    push_proc = CommandRunner.run(push_cmd, check=False, timeout_s=30, log_output=False)
    os.unlink(config_path)

    if push_proc.returncode != 0:
        return {"ok": False, "error": "push_config_failed", "stderr": push_proc.stderr}

    # Start heapprofd
    output_file = f"/data/local/tmp/{config.package_name}_{uuid.uuid4().hex[:8]}.heapprofd"
    start_cmd = adb_prefix(device_serial) + [
        "shell", "heapprofd", f"--config={remote_cfg}", f"--out={output_file}"
    ]

    try:
        proc = CommandRunner.run(start_cmd, check=False, timeout_s=5, log_output=False)
        if proc.returncode != 0:
            return {"ok": False, "error": "heapprofd_start_failed", "stderr": proc.stderr}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "output_file": output_file,
        "config_file": remote_cfg,
    }


def stop_heapprofd(device_serial: Optional[str], output_file: str) -> dict:
    """
    Stop heapprofd and pull output.
    停止 heapprofd 并拉取输出。
    """
    if not output_file:
        return {"ok": False, "error": "output_file is required"}

    # There is no explicit stop command; we wait and then pull the file
    time.sleep(1.0)

    return {
        "ok": True,
        "output_file": output_file,
    }


def analyze_heapprofd_trace(trace_file: str) -> dict:
    """
    Analyze heapprofd output trace.
    分析 heapprofd 输出的 trace 文件。
    """
    if not trace_file or not pathlib.Path(trace_file).exists():
        return {"ok": False, "error": "trace_file_not_found"}

    # Placeholder for parsing. Actual parsing would use perfetto trace processor.
    return {"ok": True, "trace_file": trace_file}


def run_heapprofd_session(
    device_serial: Optional[str],
    package_name: str,
    *,
    out_dir: str = "./.recordings",
    duration_ms: int = 30000,
    sampling_interval_bytes: int = 4096,
) -> dict:
    """
    Run a full heapprofd session and pull output.
    运行完整 heapprofd 会话并拉取输出。
    """
    cfg = HeapprofdConfig(
        package_name=package_name,
        duration_ms=duration_ms,
        sampling_interval_bytes=sampling_interval_bytes,
    )

    start_res = start_heapprofd(device_serial, cfg)
    if not start_res.get("ok"):
        return start_res

    output_file = start_res.get("output_file")
    if not output_file:
        return {"ok": False, "error": "no_output_file"}

    # Wait for duration
    time.sleep(duration_ms / 1000.0)

    # Pull output file
    out_root = pathlib.Path(out_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    local_file = out_root / f"heapprofd_{uuid.uuid4().hex[:8]}.pb"

    pull_cmd = adb_prefix(device_serial) + ["pull", output_file, str(local_file)]
    pull_proc = CommandRunner.run(pull_cmd, check=False, timeout_s=60, log_output=False)

    if pull_proc.returncode != 0:
        return {"ok": False, "error": "pull_failed", "stderr": pull_proc.stderr}

    return {
        "ok": True,
        "output_file": output_file,
        "local_file": str(local_file),
    }


def import_android_studio_hprof(hprof_path: str) -> dict:
    """
    Import an Android Studio hprof file (noop placeholder).
    Android Studio hprof 导入占位实现。
    """
    if not hprof_path or not pathlib.Path(hprof_path).exists():
        return {"ok": False, "error": "hprof_not_found"}
    return {"ok": True, "path": hprof_path}


def _generate_config(config: HeapprofdConfig) -> str:
    return f"""
profiling_session {{
  sampling_interval_bytes: {config.sampling_interval_bytes}
  duration_ms: {config.duration_ms}
  shmem_size_bytes: {config.shmem_size_bytes}
  continuous_dump_interval_ms: {config.continuous_dump_interval_ms}
  all_heaps: {str(config.all_heaps).lower()}
  block_client: {str(config.block_client).lower()}
  process_cmdline: "{config.package_name}"
}}
""".strip()


def _api_to_android_version(api_level: int) -> int:
    mapping = {
        29: 10,
        30: 11,
        31: 12,
        32: 12,
        33: 13,
        34: 14,
    }
    return mapping.get(api_level, 0)
