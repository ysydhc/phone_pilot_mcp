#!/usr/bin/env python3
"""验证环境自检：在运行 A 类真机验证前执行，检查 adb、设备、Python、工作目录等。

Verification env self-check: run before A-class device verification.

运行约定 / Run conventions:
- 运行时机：A 类真机验证前 / Timing: before A-class device verification
- 工作目录：默认项目根 / Working dir: project root by default
  可通过 PHONE_PILOT_PROJECT_ROOT 或 --cwd 指定 / Override with env or --cwd
- adb：PATH 或 ANDROID_ADB_PATH / adb: from PATH or ANDROID_ADB_PATH
- 推荐命令 / Recommended: python scripts/check_verification_env.py
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def check_adb() -> tuple[bool, str]:
    """检查 adb 可执行文件是否可用。Check if adb executable is available."""
    adb_path = os.environ.get("ANDROID_ADB_PATH")
    if adb_path:
        # 环境变量指定路径 / env-specified path
        if os.path.isfile(adb_path) and os.access(adb_path, os.X_OK):
            return True, adb_path
        return False, f"ANDROID_ADB_PATH={adb_path} not executable or not found"
    # 从 PATH 查找 / lookup from PATH
    adb = shutil.which("adb")
    if adb:
        return True, adb
    return False, "adb not in PATH and ANDROID_ADB_PATH not set"


def check_device(adb_cmd: str) -> tuple[bool, str]:
    """检查至少一台设备已连接且状态为 device。Check at least one device connected with status 'device'."""
    try:
        result = subprocess.run(
            [adb_cmd, "devices"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return False, str(e)

    lines = result.stdout.strip().splitlines()
    # 跳过首行 "List of devices attached" / skip header
    devices: list[str] = []
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            serial, status = parts[0], parts[1]
            if status == "device":
                devices.append(serial)

    if devices:
        return True, devices[0]  # 返回 serial 供输出 / return serial for output
    return False, "no device with status 'device' (check: unauthorized/offline/empty)"


def check_python() -> tuple[bool, str]:
    """检查 Python 版本与 pyproject.toml 一致（>= 3.13）。Check Python version matches pyproject.toml (>= 3.13)."""
    v = sys.version_info
    if v.major >= 3 and v.minor >= 13:
        return True, f"{v.major}.{v.minor}.{v.micro}"
    return False, f"{v.major}.{v.minor}.{v.micro} (need >= 3.13)"


def check_project_root(cwd: str) -> tuple[bool, str]:
    """检查工作目录为项目根（含 phone_pilot/mcp/server.py）。Check working dir is project root."""
    marker = os.path.join(cwd, "phone_pilot", "mcp", "server.py")
    if os.path.isfile(marker):
        return True, cwd
    return False, f"{cwd} (missing phone_pilot/mcp/server.py)"


def check_optional(module: str, label: str) -> tuple[str, str]:
    """检查可选依赖，返回 'OK' 或 'WARN' 及消息。Check optional dependency, return OK or WARN."""
    try:
        __import__(module)
        return "OK", ""
    except ImportError:
        if module == "cv2":
            return "WARN", "not installed, B-class image tools may fail"
        if module == "pytesseract":
            return "WARN", "not installed, OCR tools may fail"
        return "WARN", f"not installed ({label})"


def _resolve_adb() -> str:
    """解析 adb 命令路径。Resolve adb command path."""
    adb_path = os.environ.get("ANDROID_ADB_PATH")
    if adb_path and os.path.isfile(adb_path) and os.access(adb_path, os.X_OK):
        return adb_path
    adb = shutil.which("adb")
    return adb or "adb"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verification env self-check for A-class device verification")
    parser.add_argument("--cwd", type=str, help="Override working directory (project root)")
    args = parser.parse_args()

    # 工作目录：--cwd > PHONE_PILOT_PROJECT_ROOT > 当前目录
    # Working dir: --cwd > PHONE_PILOT_PROJECT_ROOT > current dir
    cwd = args.cwd or os.environ.get("PHONE_PILOT_PROJECT_ROOT") or os.getcwd()
    cwd = os.path.abspath(cwd)

    required_pass = 0
    required_total = 4
    warnings: list[str] = []

    # 1. adb
    ok, msg = check_adb()
    status = "OK" if ok else "FAIL"
    detail = msg if ok else msg
    print(f"[CHECK] adb executable .............. {status}")
    if ok:
        required_pass += 1
    else:
        print(f"        -> {detail}")

    # 2. device (only if adb ok)
    adb_cmd = _resolve_adb()
    if ok:
        dev_ok, dev_msg = check_device(adb_cmd)
        if dev_ok:
            print(f"[CHECK] device connected ............ OK (serial: {dev_msg})")
            required_pass += 1
        else:
            print("[CHECK] device connected ............ FAIL")
            print(f"        -> {dev_msg}")
    else:
        print("[CHECK] device connected ............ SKIP (adb not available)")

    # 3. Python
    py_ok, py_msg = check_python()
    status = "OK" if py_ok else "FAIL"
    print(f"[CHECK] Python version .............. {status} ({py_msg})")
    if py_ok:
        required_pass += 1
    else:
        print("        -> need >= 3.13")

    # 4. project root
    root_ok, root_msg = check_project_root(cwd)
    status = "OK" if root_ok else "FAIL"
    print(f"[CHECK] project root ................ {status} ({root_msg})")
    if root_ok:
        required_pass += 1
    else:
        print(f"        -> {root_msg}")

    # 5. optional: opencv
    opt_status, opt_msg = check_optional("cv2", "opencv")
    if opt_status == "WARN":
        warnings.append("opencv")
    print(f"[CHECK] opencv (cv2) ................ {opt_status}" + (f" ({opt_msg})" if opt_msg else ""))

    # 6. optional: tesseract
    opt_status2, opt_msg2 = check_optional("pytesseract", "tesseract")
    if opt_status2 == "WARN":
        warnings.append("tesseract")
    print(f"[CHECK] tesseract ................... {opt_status2}" + (f" ({opt_msg2})" if opt_msg2 else ""))

    # 汇总 / summary
    warn_count = len(warnings)
    print()
    w = "warning" if warn_count == 1 else "warnings"
    print(f"Result: {required_pass}/{required_total} required checks passed, {warn_count} {w}.")

    sys.exit(0 if required_pass == required_total else 1)


if __name__ == "__main__":
    main()
