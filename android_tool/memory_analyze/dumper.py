#!/usr/bin/env python3
"""
Android Memory Dump Utilities.
Android 内存 Dump 工具。

Provides functionality to dump heap memory (hprof) from Android apps.
提供从 Android 应用 dump 堆内存的功能。
"""

from __future__ import annotations

import os
import pathlib
import time
import uuid
from typing import Optional

from android_tool.adb_utils import adb_prefix
from android_tool.runner import CommandRunner


def _get_app_pid(device_serial: Optional[str], package_name: str) -> Optional[int]:
    """
    Get the PID of a running app.
    获取运行中应用的 PID。
    """
    cmd = adb_prefix(device_serial) + ["shell", "pidof", package_name]
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return int(out.split()[0])
    except Exception:
        return None


def _is_app_debuggable(device_serial: Optional[str], package_name: str) -> bool:
    """
    Check if app is debuggable.
    检查应用是否可调试。
    """
    cmd = adb_prefix(device_serial) + ["shell", "run-as", package_name, "id"]
    proc = CommandRunner.run(cmd, check=False, delay_s=0.0, log_output=False)
    return proc.returncode == 0


def dump_hprof(
    device_serial: Optional[str],
    package_name: str,
    *,
    out_dir: str = "./recordings",
    hprof_subdir: str = "hprof",
    name: Optional[str] = None,
    timeout_s: float = 60.0,
    convert_to_standard: bool = True,
) -> dict:
    """
    Dump heap memory (hprof) from an Android app.
    从 Android 应用 dump 堆内存。

    This function uses `adb shell am dumpheap` to capture heap memory.
    此函数使用 `adb shell am dumpheap` 来捕获堆内存。

    Note: For non-debuggable apps, this requires root or uses am dumpheap.
    注意：对于非调试应用，需要 root 权限或使用 am dumpheap。

    Args:
        device_serial: Device serial number / 设备序列号
        package_name: App package name / 应用包名
        out_dir: Output directory / 输出目录
        hprof_subdir: Subdirectory name for hprof files (default "hprof") / hprof 子目录名
        name: Optional name for the dump file / dump 文件的可选名称
        timeout_s: Timeout in seconds / 超时时间（秒）
        convert_to_standard: Convert Android hprof to standard format / 转换为标准格式

    Returns:
        dict with ok, path, size_bytes, etc.
    """
    if not package_name or not package_name.strip():
        return {"ok": False, "error": "package_name is required"}

    package_name = package_name.strip()

    # Check if app is running
    pid = _get_app_pid(device_serial, package_name)
    if pid is None:
        return {"ok": False, "error": f"App {package_name} is not running", "package": package_name}

    # Generate unique filename
    ts = time.strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    # Sanitize name: remove special characters that cause shell issues
    raw_name = name or package_name.split(".")[-1]
    # Replace problematic characters: (), [], {}, spaces, quotes, etc.
    safe_name = raw_name
    for ch in "()[]{}\"'` \t\n<>|&;$":
        safe_name = safe_name.replace(ch, "_")
    # Remove consecutive underscores
    while "__" in safe_name:
        safe_name = safe_name.replace("__", "_")
    safe_name = safe_name.strip("_")
    filename = f"{ts}_{safe_name}_{uid}.hprof"

    # Remote path on device
    remote_path = f"/data/local/tmp/{filename}"

    # Use am dumpheap command
    dump_cmd = adb_prefix(device_serial) + [
        "shell", "am", "dumpheap", package_name, remote_path
    ]

    try:
        proc = CommandRunner.run(dump_cmd, check=False, delay_s=0.0, log_output=True, timeout_s=timeout_s)
        if proc.returncode != 0:
            # Try alternative method for debuggable apps
            if _is_app_debuggable(device_serial, package_name):
                # Use run-as for debuggable apps
                alt_remote = f"/data/data/{package_name}/files/{filename}"
                dump_cmd2 = adb_prefix(device_serial) + [
                    "shell", "run-as", package_name,
                    "kill", "-10", str(pid)  # SIGUSR1 triggers heap dump on some systems
                ]
                CommandRunner.run(dump_cmd2, check=False, delay_s=2.0, log_output=False)
            else:
                return {
                    "ok": False,
                    "error": "dumpheap failed",
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or "").strip(),
                    "package": package_name,
                    "pid": pid,
                }

        # Wait for dump to complete (am dumpheap is async)
        time.sleep(2.0)

        # Check if file exists and get size
        stat_cmd = adb_prefix(device_serial) + ["shell", "stat", "-c", "%s", remote_path]
        stat_proc = CommandRunner.run(stat_cmd, check=False, delay_s=0.0, log_output=False)
        remote_size = 0
        if stat_proc.returncode == 0:
            try:
                remote_size = int((stat_proc.stdout or "0").strip())
            except ValueError:
                remote_size = 0

        if remote_size == 0:
            # File might not exist yet, wait more
            for _ in range(5):
                time.sleep(1.0)
                stat_proc = CommandRunner.run(stat_cmd, check=False, delay_s=0.0, log_output=False)
                if stat_proc.returncode == 0:
                    try:
                        remote_size = int((stat_proc.stdout or "0").strip())
                        if remote_size > 0:
                            break
                    except ValueError:
                        pass

        if remote_size == 0:
            return {
                "ok": False,
                "error": "hprof file not created or empty",
                "remote_path": remote_path,
                "package": package_name,
                "pid": pid,
            }

        # Pull file to local
        out_root = pathlib.Path(out_dir).expanduser().resolve()
        if hprof_subdir:
            hprof_dir = out_root / hprof_subdir
        else:
            hprof_dir = out_root
        hprof_dir.mkdir(parents=True, exist_ok=True)
        local_path = hprof_dir / filename

        pull_cmd = adb_prefix(device_serial) + ["pull", remote_path, str(local_path)]
        pull_proc = CommandRunner.run(pull_cmd, check=False, delay_s=0.0, log_output=True)

        if pull_proc.returncode != 0 or not local_path.exists():
            return {
                "ok": False,
                "error": "failed to pull hprof file",
                "remote_path": remote_path,
                "local_path": str(local_path),
                "package": package_name,
                "pid": pid,
            }

        local_size = local_path.stat().st_size

        # Clean up remote file
        rm_cmd = adb_prefix(device_serial) + ["shell", "rm", "-f", remote_path]
        CommandRunner.run(rm_cmd, check=False, delay_s=0.0, log_output=False)

        # Optionally convert Android hprof to standard Java hprof format
        converted_path = None
        if convert_to_standard:
            converted_path = _convert_hprof(local_path)

        return {
            "ok": True,
            "path": str(converted_path or local_path),
            "original_path": str(local_path),
            "size_bytes": local_size,
            "converted": converted_path is not None,
            "package": package_name,
            "pid": pid,
            "timestamp": ts,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "package": package_name,
        }


def _convert_hprof(hprof_path: pathlib.Path) -> Optional[pathlib.Path]:
    """
    Convert Android hprof to standard Java hprof format.
    将 Android hprof 转换为标准 Java hprof 格式。

    Android hprof files have a different header format. This function
    converts them to standard format that can be read by standard tools.
    Android hprof 文件有不同的头部格式，此函数将其转换为可被标准工具读取的格式。

    Returns:
        Path to converted file, or None if conversion failed / 转换后的文件路径，失败则返回 None
    """
    try:
        with open(hprof_path, "rb") as f:
            header = f.read(32)

        # Check if it's an Android hprof (starts with "JAVA PROFILE 1.0.3" or similar)
        if b"JAVA PROFILE" in header:
            # Already in standard format
            return None

        # Android format starts with a version string followed by different structure
        # For now, we'll keep the original file as most analysis can work with it
        # A full converter would need hprof-conv from Android SDK
        return None

    except Exception:
        return None


def trigger_gc(
    device_serial: Optional[str],
    package_name: str,
    *,
    wait_s: float = 2.0,
) -> dict:
    """
    Trigger garbage collection for an Android app.
    触发 Android 应用的垃圾回收。

    Uses multiple methods to trigger GC (best-effort):
    使用多种方法触发 GC（尽力而为）：
    1. `dumpsys meminfo --force-gc <package>` - 强制 GC（最可靠）
    2. `am send-trim-memory <package> RUNNING_CRITICAL` - 前台进程内存裁剪
    3. For debuggable apps: SIGUSR1 signal

    Note: Android trim memory levels:
    - Foreground: RUNNING_MODERATE(5), RUNNING_LOW(10), RUNNING_CRITICAL(15)
    - Background: BACKGROUND(40), MODERATE(60), COMPLETE(80)
    使用 RUNNING_CRITICAL 因为它适用于前台进程。

    Args:
        device_serial: Device serial number / 设备序列号
        package_name: App package name / 应用包名
        wait_s: Wait time after triggering GC / 触发 GC 后的等待时间

    Returns:
        dict with ok, method, etc.
    """
    if not package_name or not package_name.strip():
        return {"ok": False, "error": "package_name is required"}

    package_name = package_name.strip()

    # Check if app is running
    pid = _get_app_pid(device_serial, package_name)
    if pid is None:
        return {"ok": False, "error": f"App {package_name} is not running", "package": package_name}

    methods_tried = []
    success = False

    # Method 1: dumpsys meminfo --force-gc (most reliable)
    # This forces a GC and returns memory info
    try:
        gc_cmd = adb_prefix(device_serial) + [
            "shell", "dumpsys", "meminfo", "--force-gc", package_name
        ]
        proc = CommandRunner.run(gc_cmd, check=False, delay_s=0.5, log_output=False, timeout_s=30.0)
        methods_tried.append({
            "method": "dumpsys-force-gc",
            "returncode": proc.returncode,
            "success": proc.returncode == 0,
        })
        if proc.returncode == 0:
            success = True
    except Exception as e:
        methods_tried.append({
            "method": "dumpsys-force-gc",
            "error": str(e),
            "success": False,
        })

    # Method 2: am send-trim-memory with RUNNING_CRITICAL (works for foreground apps)
    # RUNNING_CRITICAL (15) is the highest level that works for foreground processes
    try:
        trim_cmd = adb_prefix(device_serial) + [
            "shell", "am", "send-trim-memory", package_name, "RUNNING_CRITICAL"
        ]
        proc = CommandRunner.run(trim_cmd, check=False, delay_s=0.5, log_output=False)
        methods_tried.append({
            "method": "send-trim-memory-RUNNING_CRITICAL",
            "returncode": proc.returncode,
            "success": proc.returncode == 0,
        })
        if proc.returncode == 0:
            success = True
    except Exception as e:
        methods_tried.append({
            "method": "send-trim-memory-RUNNING_CRITICAL",
            "error": str(e),
            "success": False,
        })

    # Method 3: For debuggable apps, try SIGUSR1
    if _is_app_debuggable(device_serial, package_name):
        try:
            # SIGUSR1 (signal 10) can trigger GC on some Android versions
            sig_cmd = adb_prefix(device_serial) + [
                "shell", "run-as", package_name, "kill", "-10", str(pid)
            ]
            proc = CommandRunner.run(sig_cmd, check=False, delay_s=0.5, log_output=False)
            methods_tried.append({
                "method": "sigusr1",
                "returncode": proc.returncode,
                "success": proc.returncode == 0,
            })
            if proc.returncode == 0:
                success = True
        except Exception as e:
            methods_tried.append({
                "method": "sigusr1",
                "error": str(e),
                "success": False,
            })

    # Wait for GC to complete
    if wait_s > 0:
        time.sleep(wait_s)

    return {
        "ok": success,
        "package": package_name,
        "pid": pid,
        "methods_tried": methods_tried,
        "wait_s": wait_s,
    }


def pull_hprof(
    device_serial: Optional[str],
    remote_path: str,
    *,
    out_dir: str = "./recordings",
    name: Optional[str] = None,
) -> dict:
    """
    Pull an existing hprof file from device.
    从设备拉取已存在的 hprof 文件。

    Args:
        device_serial: Device serial number / 设备序列号
        remote_path: Remote path on device / 设备上的远程路径
        out_dir: Output directory / 输出目录
        name: Optional name for the local file / 本地文件的可选名称

    Returns:
        dict with ok, path, size_bytes, etc.
    """
    if not remote_path or not remote_path.strip():
        return {"ok": False, "error": "remote_path is required"}

    remote_path = remote_path.strip()

    # Generate local filename
    ts = time.strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    if name:
        safe_name = name.replace("/", "_").replace(" ", "_")
    else:
        safe_name = os.path.basename(remote_path).replace(".hprof", "")
    filename = f"{ts}_{safe_name}_{uid}.hprof"

    out_root = pathlib.Path(out_dir).expanduser().resolve()
    hprof_dir = out_root / "hprof"
    hprof_dir.mkdir(parents=True, exist_ok=True)
    local_path = hprof_dir / filename

    pull_cmd = adb_prefix(device_serial) + ["pull", remote_path, str(local_path)]
    proc = CommandRunner.run(pull_cmd, check=False, delay_s=0.0, log_output=True)

    if proc.returncode != 0 or not local_path.exists():
        return {
            "ok": False,
            "error": "failed to pull hprof file",
            "remote_path": remote_path,
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "").strip(),
        }

    local_size = local_path.stat().st_size

    return {
        "ok": True,
        "path": str(local_path),
        "size_bytes": local_size,
        "remote_path": remote_path,
    }
