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

import json
import os
import pathlib
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from android_tool.adb_utils import adb_prefix
from android_tool.runner import CommandRunner


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
            result["requirements"].append(f"Android 10+ required (current: API {result['api_level']})")
        else:
            result["supported"] = True

        # Check if device is rooted
        cmd = adb_prefix(device_serial) + ["shell", "su", "-c", "id"]
        proc = CommandRunner.run(cmd, check=False, timeout_s=5, log_output=False)
        result["is_rooted"] = proc.returncode == 0 and "uid=0" in proc.stdout

        if not result["is_rooted"]:
            result["requirements"].append("Root access OR debuggable app required")

    except Exception as e:
        result["error"] = str(e)

    return result


def _api_to_android_version(api: int) -> int:
    """Convert API level to Android version number."""
    mapping = {
        29: 10, 30: 11, 31: 12, 32: 12, 33: 13, 34: 14, 35: 15,
    }
    return mapping.get(api, api - 19 if api >= 29 else 0)


def start_heapprofd(
    device_serial: Optional[str],
    config: HeapprofdConfig,
    *,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
) -> dict:
    """
    Start heapprofd profiling session using perfetto.
    使用 perfetto 启动 heapprofd 分析会话。

    This starts a background profiling session that records native heap
    allocations with stack traces.

    Args:
        device_serial: Device serial number
        config: HeapprofdConfig with profiling settings
        out_dir: Output directory for results
        name: Optional name for this session

    Returns:
        dict with session info including session_id for later stop/analysis
    """
    session_id = uuid.uuid4().hex[:12]
    safe_name = name or config.package_name.split(".")[-1]
    for ch in "()[]{}\"'` \t\n<>|&;$":
        safe_name = safe_name.replace(ch, "_")

    # Create output directory
    out_root = pathlib.Path(out_dir).expanduser().resolve()
    session_dir = out_root / "heapprofd" / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_name}_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Remote paths - use /data/misc/perfetto-traces for proper permissions
    remote_trace = f"/data/misc/perfetto-traces/heapprofd_{session_id}.pb"

    # Generate perfetto config
    perfetto_config = _generate_perfetto_config(config)
    config_path = session_dir / "perfetto_config.txt"
    config_path.write_text(perfetto_config)

    # Start perfetto with config via stdin (using heredoc)
    # This avoids file permission issues
    heredoc_cmd = f"""cat <<'PERFETTO_CONFIG_EOF' | perfetto -c - -o {remote_trace} --background --txt
{perfetto_config}
PERFETTO_CONFIG_EOF"""

    cmd = adb_prefix(device_serial) + ["shell", heredoc_cmd]
    proc = CommandRunner.run(cmd, check=False, timeout_s=30)

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": "Failed to start perfetto",
            "stderr": proc.stderr,
            "hint": "Make sure the app is debuggable or device is rooted",
        }

    # Save session metadata
    meta = {
        "session_id": session_id,
        "package_name": config.package_name,
        "duration_ms": config.duration_ms,
        "sampling_interval_bytes": config.sampling_interval_bytes,
        "remote_trace": remote_trace,
        "session_dir": str(session_dir),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "running",
    }
    meta_path = session_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    return {
        "ok": True,
        "session_id": session_id,
        "session_dir": str(session_dir),
        "remote_trace": remote_trace,
        "duration_ms": config.duration_ms,
        "message": f"heapprofd started, will run for {config.duration_ms}ms",
    }


def _generate_perfetto_config(config: HeapprofdConfig) -> str:
    """Generate perfetto configuration for heapprofd."""
    return f"""buffers: {{
  size_kb: 65536
  fill_policy: RING_BUFFER
}}

data_sources: {{
  config {{
    name: "android.heapprofd"
    heapprofd_config {{
      shmem_size_bytes: {config.shmem_size_bytes}
      sampling_interval_bytes: {config.sampling_interval_bytes}
      process_cmdline: "{config.package_name}"
      all_heaps: {str(config.all_heaps).lower()}
      block_client: {str(config.block_client).lower()}
    }}
  }}
}}

duration_ms: {config.duration_ms}
"""


def stop_heapprofd(
    device_serial: Optional[str],
    session_id: str,
    *,
    out_dir: str = "./recordings",
    wait_completion: bool = True,
    timeout_s: float = 60.0,
) -> dict:
    """
    Stop heapprofd session and pull trace file.
    停止 heapprofd 会话并拉取 trace 文件。

    Args:
        device_serial: Device serial number
        session_id: Session ID from start_heapprofd
        out_dir: Output directory
        wait_completion: Wait for perfetto to finish if still running
        timeout_s: Timeout for waiting

    Returns:
        dict with trace file path and status
    """
    # Find session directory
    out_root = pathlib.Path(out_dir).expanduser().resolve()
    heapprofd_dir = out_root / "heapprofd"

    session_dir = None
    for d in heapprofd_dir.iterdir() if heapprofd_dir.exists() else []:
        if d.is_dir() and session_id in d.name:
            session_dir = d
            break

    if not session_dir:
        return {"ok": False, "error": f"Session {session_id} not found"}

    # Load metadata
    meta_path = session_dir / "meta.json"
    if not meta_path.exists():
        return {"ok": False, "error": "Session metadata not found"}

    meta = json.loads(meta_path.read_text())
    remote_trace = meta.get("remote_trace")

    # Check if perfetto is still running
    if wait_completion:
        # Just wait for expected duration plus buffer, since pgrep matching is unreliable
        duration_ms = meta.get("duration_ms", 30000)
        expected_wait = (duration_ms / 1000) + 2
        elapsed = time.time() - time.mktime(time.strptime(meta.get("started_at", ""), "%Y-%m-%d %H:%M:%S")) if meta.get("started_at") else 0

        if elapsed < expected_wait:
            remaining = expected_wait - elapsed
            if remaining > 0:
                time.sleep(min(remaining, timeout_s))

    # Pull trace file
    local_trace = session_dir / "heap_profile.perfetto-trace"
    cmd = adb_prefix(device_serial) + ["pull", remote_trace, str(local_trace)]
    proc = CommandRunner.run(cmd, check=False, timeout_s=120)

    if proc.returncode != 0 or not local_trace.exists():
        return {
            "ok": False,
            "error": "Failed to pull trace file",
            "stderr": proc.stderr,
            "hint": "Trace may not be ready yet. Try increasing duration_ms.",
        }

    # Clean up remote trace file
    cmd = adb_prefix(device_serial) + ["shell", "rm", "-f", remote_trace]
    CommandRunner.run(cmd, check=False, timeout_s=10, log_output=False)

    # Update metadata
    meta["status"] = "completed"
    meta["local_trace"] = str(local_trace)
    meta["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    meta_path.write_text(json.dumps(meta, indent=2))

    return {
        "ok": True,
        "session_id": session_id,
        "trace_path": str(local_trace),
        "trace_size": local_trace.stat().st_size,
        "session_dir": str(session_dir),
    }


def analyze_heapprofd_trace(
    trace_path: str,
    *,
    package_filter: Optional[str] = None,
    min_size_bytes: int = 1024,
    top_n: int = 50,
) -> dict:
    """
    Analyze heapprofd trace file and extract allocation info.
    分析 heapprofd trace 文件并提取分配信息。

    Note: This requires the `trace_processor_shell` tool from perfetto.
    If not available, we'll try to use basic parsing.

    注意：这需要 perfetto 的 `trace_processor_shell` 工具。
    如果不可用，我们将尝试使用基本解析。

    Args:
        trace_path: Path to perfetto trace file
        package_filter: Filter allocations by package/library name
        min_size_bytes: Minimum allocation size to include
        top_n: Number of top allocations to return

    Returns:
        dict with allocation analysis results
    """
    path = pathlib.Path(trace_path)
    if not path.exists():
        return {"ok": False, "error": f"Trace file not found: {trace_path}"}

    # Try to find trace_processor_shell
    trace_processor = _find_trace_processor()

    if trace_processor:
        return _analyze_with_trace_processor(trace_path, trace_processor, package_filter, min_size_bytes, top_n)
    else:
        return _analyze_basic(trace_path, package_filter, min_size_bytes, top_n)


def _find_trace_processor() -> Optional[str]:
    """Find or download trace_processor_shell binary."""
    # Check common locations
    local_bin = pathlib.Path.home() / ".local" / "bin" / "trace_processor_shell"
    candidates = [
        "trace_processor_shell",  # In PATH
        "/usr/local/bin/trace_processor_shell",
        os.path.expanduser("~/Android/Sdk/cmdline-tools/latest/bin/trace_processor_shell"),
        str(local_bin),  # Auto-downloaded location
    ]

    for candidate in candidates:
        try:
            proc = subprocess.run([candidate, "--version"], capture_output=True, timeout=5)
            if proc.returncode == 0:
                return candidate
        except Exception:
            pass

    # Try to auto-download
    downloaded = _download_trace_processor()
    if downloaded:
        return downloaded

    return None


def _download_trace_processor() -> Optional[str]:
    """Download trace_processor_shell using official perfetto script."""
    import platform
    import stat

    # Download location
    local_bin_dir = pathlib.Path.home() / ".local" / "bin"
    local_bin_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_bin_dir / "trace_processor_shell"

    try:
        # Use curl to download from get.perfetto.dev
        print("[heapprofd] Downloading trace_processor_shell from get.perfetto.dev...")
        proc = subprocess.run(
            ["curl", "-Lso", str(local_path), "https://get.perfetto.dev/trace_processor"],
            capture_output=True,
            timeout=120,
        )

        if proc.returncode != 0:
            print(f"[heapprofd] curl failed: {proc.stderr.decode()}", file=__import__('sys').stderr)
            return None

        # Make executable
        local_path.chmod(local_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"[heapprofd] Downloaded to {local_path}")

        # Verify it works
        proc = subprocess.run([str(local_path), "--version"], capture_output=True, timeout=10)
        if proc.returncode == 0:
            return str(local_path)
        else:
            print(f"[heapprofd] Downloaded binary doesn't work: {proc.stderr.decode()}", file=__import__('sys').stderr)
            return None

    except subprocess.TimeoutExpired:
        print("[heapprofd] Download timed out", file=__import__('sys').stderr)
        return None
    except Exception as e:
        print(f"[heapprofd] Failed to download trace_processor_shell: {e}", file=__import__('sys').stderr)
        return None


def _analyze_with_trace_processor(
    trace_path: str,
    trace_processor: str,
    package_filter: Optional[str],
    min_size_bytes: int,
    top_n: int,
) -> dict:
    """Analyze trace using trace_processor_shell."""
    # SQL query to get heap allocations grouped by callsite with stack info
    # Must be single line to avoid trace_processor treating newlines as separate commands
    query = f"SELECT a.callsite_id, SUM(a.size) as total_size, COUNT(*) as alloc_count, f.name as frame_name, m.name as library FROM heap_profile_allocation a LEFT JOIN stack_profile_callsite c ON a.callsite_id = c.id LEFT JOIN stack_profile_frame f ON c.frame_id = f.id LEFT JOIN stack_profile_mapping m ON f.mapping = m.id WHERE a.size >= {min_size_bytes} GROUP BY a.callsite_id ORDER BY total_size DESC LIMIT {top_n};"

    try:
        # Use stdin to pass query (not -q which expects a file path)
        proc = subprocess.run(
            [trace_processor, trace_path],
            input=query,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if proc.returncode != 0:
            return {
                "ok": False,
                "error": "trace_processor query failed",
                "stderr": proc.stderr,
            }

        # Parse results - format is space-separated columns with header
        lines = proc.stdout.strip().split("\n")
        allocations = []

        # Find data lines (skip header and separator lines)
        in_data = False
        for line in lines:
            line = line.strip()
            # Skip loading messages and query messages
            if not line or "Loading trace" in line or "Trace loaded" in line or "Query executed" in line:
                continue
            # Separator line marks start of data
            if line.startswith("-"):
                in_data = True
                continue
            # Skip header line
            if "callsite_id" in line.lower():
                continue

            if in_data:
                # Parse space-separated values (handle multiple spaces)
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        callsite_id = int(parts[0])
                        total_size = int(parts[1])
                        alloc_count = int(parts[2])
                        frame_name = parts[3] if len(parts) > 3 else "<unknown>"
                        library = parts[4] if len(parts) > 4 else ""

                        allocations.append({
                            "callsite_id": callsite_id,
                            "total_size": total_size,
                            "alloc_count": alloc_count,
                            "frame_name": frame_name,
                            "library": library,
                            "total_size_kb": round(total_size / 1024, 2),
                        })
                    except (ValueError, IndexError):
                        continue

        # Calculate totals
        total_size = sum(a["total_size"] for a in allocations)
        total_count = sum(a["alloc_count"] for a in allocations)

        # Get full stack traces for top allocations
        allocations_with_stacks = _get_allocation_stacks(trace_path, trace_processor, allocations[:10])

        return {
            "ok": True,
            "trace_path": trace_path,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "total_allocations": total_count,
            "top_allocations": allocations_with_stacks if allocations_with_stacks else allocations,
            "analysis_method": "trace_processor",
        }

    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "trace_processor timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _get_allocation_stacks(trace_path: str, trace_processor: str, allocations: list) -> list:
    """Get full stack traces for allocations."""
    if not allocations:
        return allocations

    result = []
    for alloc in allocations:
        callsite_id = alloc.get("callsite_id", 0)
        if callsite_id == 0:
            result.append(alloc)
            continue

        # Query to get full stack trace - single line to avoid parsing issues
        query = f"WITH RECURSIVE stack AS (SELECT id, frame_id, parent_id, 0 as depth FROM stack_profile_callsite WHERE id = {callsite_id} UNION ALL SELECT c.id, c.frame_id, c.parent_id, s.depth + 1 FROM stack_profile_callsite c JOIN stack s ON c.id = s.parent_id WHERE s.depth < 20) SELECT f.name as frame_name, m.name as library, f.symbol_set_id, stack.depth FROM stack JOIN stack_profile_frame f ON stack.frame_id = f.id LEFT JOIN stack_profile_mapping m ON f.mapping = m.id ORDER BY stack.depth;"
        try:
            proc = subprocess.run(
                [trace_processor, trace_path],
                input=query,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if proc.returncode == 0:
                stack_frames = []
                for line in proc.stdout.strip().split("\n"):
                    if "Loading trace" in line or "Trace loaded" in line or "Query executed" in line:
                        continue
                    if line.startswith("-") or not line.strip() or "frame_name" in line.lower():
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        frame = parts[0]
                        lib = parts[1] if len(parts) > 1 else ""
                        stack_frames.append(f"{frame} ({lib})" if lib else frame)

                alloc_with_stack = dict(alloc)
                alloc_with_stack["stack_trace"] = stack_frames
                result.append(alloc_with_stack)
            else:
                result.append(alloc)

        except Exception:
            result.append(alloc)

    return result


def _analyze_basic(
    trace_path: str,
    package_filter: Optional[str],
    min_size_bytes: int,
    top_n: int,
) -> dict:
    """Basic analysis without trace_processor (limited info)."""
    path = pathlib.Path(trace_path)
    size = path.stat().st_size

    return {
        "ok": True,
        "trace_path": trace_path,
        "trace_size_bytes": size,
        "trace_size_mb": round(size / (1024 * 1024), 2),
        "analysis_method": "basic",
        "message": "Full analysis requires trace_processor_shell. Install it from https://perfetto.dev/",
        "install_hint": "Download from: https://github.com/nicusen/perfetto/releases or use Android Studio's bundled version",
        "top_allocations": [],
    }


def run_heapprofd_session(
    device_serial: Optional[str],
    package_name: str,
    *,
    duration_ms: int = 30000,
    sampling_interval_bytes: int = 4096,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
    analyze_after: bool = True,
) -> dict:
    """
    Run a complete heapprofd profiling session (start -> wait -> stop -> analyze).
    运行完整的 heapprofd 分析会话（启动 -> 等待 -> 停止 -> 分析）。

    This is the main entry point for automated heap profiling.
    这是自动化堆分析的主入口点。

    Args:
        device_serial: Device serial number
        package_name: Package name to profile
        duration_ms: Profiling duration in milliseconds
        sampling_interval_bytes: Sample every N bytes allocated
        out_dir: Output directory
        name: Optional session name
        analyze_after: Whether to analyze trace after collection

    Returns:
        dict with complete session results including allocations
    """
    # Check support first
    support = check_heapprofd_support(device_serial)
    if not support["supported"]:
        return {
            "ok": False,
            "error": "heapprofd not supported",
            "requirements": support.get("requirements", []),
            "api_level": support.get("api_level"),
        }

    # Create config
    config = HeapprofdConfig(
        package_name=package_name,
        duration_ms=duration_ms,
        sampling_interval_bytes=sampling_interval_bytes,
    )

    # Start profiling
    start_result = start_heapprofd(device_serial, config, out_dir=out_dir, name=name)
    if not start_result.get("ok"):
        return start_result

    session_id = start_result["session_id"]
    print(f"[heapprofd] Started session {session_id}, collecting for {duration_ms}ms...")

    # Wait for duration plus some buffer
    wait_time = (duration_ms / 1000) + 5
    time.sleep(wait_time)

    # Stop and pull trace
    stop_result = stop_heapprofd(device_serial, session_id, out_dir=out_dir)
    if not stop_result.get("ok"):
        return stop_result

    result = {
        "ok": True,
        "session_id": session_id,
        "session_dir": stop_result["session_dir"],
        "trace_path": stop_result["trace_path"],
        "trace_size": stop_result["trace_size"],
    }

    # Analyze if requested
    if analyze_after:
        analysis = analyze_heapprofd_trace(stop_result["trace_path"])
        result["analysis"] = analysis

    return result


def import_android_studio_hprof(
    hprof_path: str,
    *,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
) -> dict:
    """
    Import and analyze hprof file exported from Android Studio Profiler.
    导入并分析从 Android Studio Profiler 导出的 hprof 文件。

    Android Studio hprof files (with "Record allocations" enabled) contain
    allocation stack traces, unlike `am dumpheap` generated files.

    Android Studio hprof 文件（启用"记录分配"后）包含分配堆栈跟踪，
    与 `am dumpheap` 生成的文件不同。

    Args:
        hprof_path: Path to hprof file from Android Studio
        out_dir: Output directory for analysis results
        name: Optional name for this analysis

    Returns:
        dict with analysis results including Bitmap allocation stacks
    """
    from android_tool.memory_analyze.bitmap_analyzer import analyze_bitmaps

    path = pathlib.Path(hprof_path)
    if not path.exists():
        return {"ok": False, "error": f"File not found: {hprof_path}"}

    # Analyze with bitmap analyzer (which now supports allocation stacks)
    result = analyze_bitmaps(str(path))

    if result.get("ok"):
        # Save analysis to output directory
        out_root = pathlib.Path(out_dir).expanduser().resolve()
        analysis_dir = out_root / "imported_hprof"
        analysis_dir.mkdir(parents=True, exist_ok=True)

        safe_name = name or path.stem
        for ch in "()[]{}\"'` \t\n<>|&;$":
            safe_name = safe_name.replace(ch, "_")

        output_path = analysis_dir / f"{safe_name}_analysis.json"
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        result["analysis_path"] = str(output_path)
        result["source_hprof"] = str(path)
        result["source_type"] = "android_studio"

    return result
