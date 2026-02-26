#!/usr/bin/env python3
"""Shark CLI 集成 — LeakCanary 核心引擎的命令行泄漏检测。
Shark CLI integration — Command-line leak detection powered by LeakCanary's core engine.

Shark 是 LeakCanary（29.9k stars）的独立堆分析引擎。
Shark CLI 可分析任意 debuggable app 的 hprof 文件，输出完整泄漏链路。

安装 / Installation:
    brew install leakcanary-shark

健壮性设计 / Robustness Design:
    检测到泄漏 → trigger_gc → 等 5s → 重新 dump hprof → 再次分析
    → 仍然泄漏 → 确认泄漏（保存两次分析结果）
    → 不再泄漏 → 标记为 "GC 后恢复"

Usage:
    from phone_pilot.memory_analyze.shark_analyzer import analyze_with_shark
    result = analyze_with_shark("path/to/app.hprof")
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from phone_pilot.core.log import _log


def is_shark_available() -> bool:
    """检查 shark-cli 是否可用 / Check if shark-cli is available."""
    return shutil.which("shark-cli") is not None


def _run_shark(hprof_path: str, *, timeout_s: float = 120.0) -> dict:
    """执行 shark-cli 分析单个 hprof 文件 / Run shark-cli on a single hprof file.

    Returns:
        {
            "ok": bool,
            "has_leak": bool,
            "leak_count": int,
            "leaks": [{"class_name": str, "leak_trace": str, "signature": str}],
            "raw_output": str,
            "shark_version": str,
        }
    """
    if not Path(hprof_path).is_file():
        return {"ok": False, "error": f"hprof file not found: {hprof_path}"}

    shark = shutil.which("shark-cli")
    if not shark:
        return {"ok": False, "error": "shark-cli not found. Install: brew install leakcanary-shark"}

    try:
        proc = subprocess.run(
            [shark, "-h", str(hprof_path), "analyze"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        output = proc.stdout + "\n" + proc.stderr
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"shark-cli timeout ({timeout_s}s)", "has_leak": False}
    except Exception as e:
        return {"ok": False, "error": str(e), "has_leak": False}

    # Parse output
    return _parse_shark_output(output)


def _parse_shark_output(output: str) -> dict:
    """解析 shark-cli 分析输出 / Parse shark-cli analysis output.

    Shark CLI output format (simplified):
        ====================================
        LEAKING ...
        ┬───
        │ GC Root: ...
        │
        ├─ com.example.SomeClass instance
        │    Leaking: YES (...)
        │    ...
        ╰→ com.example.LeakedActivity instance
             Leaking: YES (...)
        ====================================
    """
    result: dict[str, Any] = {
        "ok": True,
        "has_leak": False,
        "leak_count": 0,
        "leaks": [],
        "raw_output": output,
        "shark_version": "",
    }

    # Try to extract version
    ver_match = re.search(r"Shark\s+([\d.]+)", output, re.IGNORECASE)
    if ver_match:
        result["shark_version"] = ver_match.group(1)

    # Look for LEAKING patterns
    leak_blocks = re.split(r"={4,}", output)
    for block in leak_blocks:
        if "LEAKING" not in block.upper() and "Leaking: YES" not in block:
            continue

        # Extract class name from leak trace
        class_names = re.findall(r"[╰→├─│]+\s*(\S+(?:\.\S+)+)\s+instance", block)
        leaking_classes = []
        for cn in class_names:
            # Check if this specific class is marked as leaking
            pattern = re.escape(cn) + r"\s+instance.*?Leaking:\s*YES"
            if re.search(pattern, block, re.DOTALL):
                leaking_classes.append(cn)

        if not leaking_classes and class_names:
            # Fallback: use last class in the trace (usually the leaking one)
            leaking_classes = [class_names[-1]]

        # Extract the full leak trace text
        trace_lines = []
        in_trace = False
        for line in block.splitlines():
            stripped = line.strip()
            if any(c in stripped for c in ("┬", "├", "│", "╰", "→")):
                in_trace = True
            if in_trace and stripped:
                trace_lines.append(line.rstrip())
            elif in_trace and not stripped:
                break

        trace_text = "\n".join(trace_lines) if trace_lines else block.strip()[:500]

        # Build signature from class names
        sig = "::".join(leaking_classes) if leaking_classes else ""

        for cls in (leaking_classes or ["Unknown"]):
            result["leaks"].append({
                "class_name": cls,
                "leak_trace": trace_text,
                "signature": sig,
                "raw_trace": block.strip()[:2000],
            })

    # Also check for simpler patterns (some shark-cli versions)
    if not result["leaks"]:
        simple_leaks = re.findall(
            r"(\d+)\s+(?:APPLICATION|LIBRARY)\s+LEAKS?",
            output, re.IGNORECASE,
        )
        if simple_leaks:
            for count_str in simple_leaks:
                result["leaks"].append({
                    "class_name": "detected_via_count",
                    "leak_trace": output[:1000],
                    "signature": "",
                })

    result["leak_count"] = len(result["leaks"])
    result["has_leak"] = result["leak_count"] > 0
    return result


def analyze_with_shark(
    hprof_path: str,
    *,
    device_serial: str | None = None,
    package_name: str | None = None,
    obfuscation_mapping: str | None = None,
    timeout_s: float = 120.0,
) -> dict:
    """使用 Shark CLI 分析 hprof 文件，含 GC 重试确认机制。
    Analyze hprof with Shark CLI, with GC-retry confirmation mechanism.

    健壮性流程 / Robustness Flow:
        1. shark-cli analyze hprof
        2. 若检测到泄漏:
           a. trigger_gc → 等 5s → 重新 dump_hprof → 再次 shark-cli
           b. 若仍然泄漏 → confirmed=True（真实泄漏）
           c. 若不再泄漏 → gc_recovered=True（GC 后恢复）
        3. 保存两次分析结果

    Parameters:
        hprof_path: hprof 文件路径
        device_serial: 设备序列号（GC 重试需要）
        package_name: 包名（GC 重试需要）
        obfuscation_mapping: ProGuard/R8 mapping 文件路径（可选）
        timeout_s: shark-cli 超时秒数

    Returns:
        {
            "ok": bool,
            "has_leak": bool,
            "leak_count": int,
            "confirmed": bool,      # 二次确认是否为真实泄漏
            "gc_recovered": bool,    # GC 后是否恢复
            "leaks": [...],
            "first_analysis": dict,  # 第一次分析结果
            "second_analysis": dict | None,  # 第二次分析结果（GC 后）
            "shark_version": str,
        }
    """
    if not is_shark_available():
        # Fallback: try existing analyzer
        _log("[shark] shark-cli 未安装，降级到内置分析器")
        try:
            from phone_pilot.memory_analyze.analyzer import analyze_hprof
            return analyze_hprof(hprof_path)
        except Exception as e:
            return {"ok": False, "error": f"shark-cli 未安装且内置分析失败: {e}"}

    # ---- First analysis ----
    _log(f"[shark] 分析 hprof: {hprof_path}")
    first = _run_shark(hprof_path, timeout_s=timeout_s)

    result: dict[str, Any] = {
        "ok": first.get("ok", False),
        "has_leak": first.get("has_leak", False),
        "leak_count": first.get("leak_count", 0),
        "leaks": first.get("leaks", []),
        "confirmed": False,
        "gc_recovered": False,
        "first_analysis": first,
        "second_analysis": None,
        "shark_version": first.get("shark_version", ""),
    }

    if not first.get("ok"):
        return result

    # ---- GC retry confirmation if leak detected ----
    if first.get("has_leak") and device_serial and package_name:
        _log("[shark] 检测到泄漏，执行 GC 重试确认...")

        try:
            from phone_pilot.memory_analyze.dumper import trigger_gc, dump_hprof

            # Trigger GC and wait
            trigger_gc(device_serial, package_name, wait_s=5.0)
            _log("[shark] GC 完成，等待 5s 后重新 dump...")
            time.sleep(5.0)

            # Dump new hprof
            tmp_dir = str(Path(hprof_path).parent)
            new_dump = dump_hprof(
                device_serial, package_name,
                out_dir=tmp_dir,
                hprof_subdir="",
                name="shark_gc_recheck",
                timeout_s=timeout_s,
            )

            if new_dump.get("ok") and new_dump.get("path"):
                new_hprof = new_dump["path"]
                _log(f"[shark] 二次分析: {new_hprof}")
                second = _run_shark(new_hprof, timeout_s=timeout_s)
                result["second_analysis"] = second

                if second.get("has_leak"):
                    result["confirmed"] = True
                    result["leak_count"] = second.get("leak_count", 0)
                    result["leaks"] = second.get("leaks", [])
                    _log(f"[shark] 二次确认泄漏: {result['leak_count']} 个")
                else:
                    result["gc_recovered"] = True
                    result["has_leak"] = False
                    result["leak_count"] = 0
                    result["leaks"] = []
                    _log("[shark] GC 后泄漏已恢复")
            else:
                _log(f"[shark] 二次 dump 失败: {new_dump.get('error', '未知')}")
                # Keep first analysis result as-is
                result["confirmed"] = False

        except Exception as e:
            _log(f"[shark] GC 重试异常: {e}")
            # Keep first analysis result
            result["confirmed"] = False
    elif first.get("has_leak"):
        _log("[shark] 检测到泄漏但缺少设备/包名信息，无法执行 GC 重试")

    return result
