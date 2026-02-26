"""HTML 可视化报告生成 / HTML visual report generator.

读取 RunSession 目录中的全部数据，生成类 Allure 风格的 HTML 报告。
Reads all data from a RunSession directory and generates an Allure-style HTML report.

用法 / Usage:
    from phone_pilot.core.html_report import generate_html_report
    report_path = generate_html_report(run_dir)

报告输出到 run_dir/report.html，截图通过相对路径引用。
Report written to run_dir/report.html; screenshots referenced via relative paths.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Optional


def generate_html_report(run_dir: str | Path) -> Path:
    """生成 HTML 可视化报告 / Generate HTML visual report.

    Parameters / 参数:
        run_dir: RunSession 运行目录路径 / RunSession run directory path

    Returns / 返回值:
        Path: 生成的 report.html 路径 / Path to generated report.html

    Raises / 异常:
        FileNotFoundError: run_dir 不存在 / run_dir does not exist
        ImportError: jinja2 未安装 / jinja2 not installed
    """
    run_dir = Path(run_dir).expanduser().resolve()
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    data = _load_run_data(run_dir)
    html = _render_html(data)

    report_path = run_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def _load_run_data(run_dir: Path) -> dict[str, Any]:
    """加载 RunSession 的全部数据 / Load all RunSession data.

    读取 run_meta.json、steps.json、截图列表、logcat 文件、
    meminfo 数据、healing_events.json、console.log 等。
    """
    data: dict[str, Any] = {}

    # ---- run_meta.json ----
    meta = _read_json(run_dir / "run_meta.json") or {}
    # Ensure nested dicts exist to avoid template errors
    meta.setdefault("device", {})
    meta.setdefault("git", {})
    meta.setdefault("steps", {"total": 0, "passed": 0, "failed": 0})
    meta.setdefault("artifacts", {
        "screenshots": 0, "screen_recordings": 0,
        "logcat_dumps": 0, "meminfo_snapshots": 0,
        "console_log_lines": 0,
    })
    data["meta"] = meta

    # ---- Duration display ----
    duration_s = meta.get("duration_s", 0)
    mins = int(duration_s) // 60
    secs = int(duration_s) % 60
    data["duration_display"] = f"{mins}m{secs:02d}s" if mins else f"{secs}s"

    # ---- Steps pass rate (recalculated from actual steps, including retry_ok) ----
    # Will be recalculated after steps classification below
    data["steps_pass_rate"] = 100  # placeholder

    # ---- steps.json ----
    steps_data = _read_json(run_dir / "steps.json") or {}
    steps = steps_data.get("steps", [])
    # Post-process: mark "retry found" steps as success
    steps = _classify_retry_steps(steps)
    data["steps"] = steps

    # Recalculate pass rate from filtered steps (retry failures already removed)
    if steps:
        step_passed = sum(1 for s in steps if s.get("status") in ("ok", "passed"))
        data["steps_pass_rate"] = round(step_passed / len(steps) * 100)
        data["meta"]["steps"]["passed"] = step_passed
        data["meta"]["steps"]["total"] = len(steps)
        data["meta"]["steps"]["failed"] = len(steps) - step_passed
    else:
        data["steps_pass_rate"] = 100

    # ---- Screenshots list & orientation detection ----
    screenshots_dir = run_dir / "screenshots"
    screenshots: list[str] = []
    is_portrait = False  # Default: landscape/square
    if screenshots_dir.is_dir():
        screenshots = sorted(
            f.name for f in screenshots_dir.iterdir()
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        )
        # Detect orientation from first screenshot
        if screenshots:
            try:
                from PIL import Image
                first_img = Image.open(screenshots_dir / screenshots[0])
                w, h = first_img.size
                first_img.close()
                is_portrait = h > w * 1.2  # Portrait if height > 1.2x width
            except Exception:
                pass
    # Build screenshot -> step mapping for gallery captions
    step_by_num = {s.get("step"): s for s in steps}
    gallery_items: list[dict] = []
    for img_name in screenshots:
        item: dict[str, Any] = {"filename": img_name, "step_num": None, "step_action": None, "step_detail": None}
        # Parse step number from filename like "003_find_text.png"
        if "_" in img_name:
            try:
                sn = int(img_name.split("_", 1)[0])
                item["step_num"] = sn
                # Find matching step (by original step number before filtering)
                matched = step_by_num.get(sn)
                if matched:
                    item["step_action"] = matched.get("action", "")
                    item["step_detail"] = _extract_query(matched.get("detail", "")) or matched.get("detail", "")[:40]
            except (ValueError, IndexError):
                pass
        gallery_items.append(item)

    data["screenshots"] = screenshots
    data["gallery_items"] = gallery_items
    data["is_portrait"] = is_portrait

    # ---- Logcat files ----
    logcat_dir = run_dir / "logcat"
    logcat_files: list[dict] = []
    if logcat_dir.is_dir():
        for f in sorted(logcat_dir.iterdir()):
            if f.is_file():
                try:
                    text = f.read_text(encoding="utf-8", errors="replace")
                    lines = text.count("\n")
                    size_kb = f.stat().st_size // 1024
                except Exception:
                    lines = 0
                    size_kb = 0
                logcat_files.append({
                    "name": f.name,
                    "lines": lines,
                    "size_kb": size_kb,
                    "abs_path": str(f),
                    "abs_dir": str(f.parent),
                })
    data["logcat_files"] = logcat_files

    # ---- Console log tail ----
    console_path = run_dir / "console.log"
    console_tail = ""
    console_tail_lines = 50
    if console_path.is_file():
        try:
            all_lines = console_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = all_lines[-console_tail_lines:] if len(all_lines) > console_tail_lines else all_lines
            console_tail = "\n".join(tail)
        except Exception:
            pass
    data["console_tail"] = console_tail
    data["console_tail_lines"] = console_tail_lines

    # ---- Meminfo files (structured) ----
    data["memory"] = _load_memory_data(run_dir / "meminfo")
    # Legacy compat: keep meminfo_files non-empty flag for nav link
    data["meminfo_files"] = bool(data["memory"])

    # ---- Healing events ----
    healing_path = run_dir / "healing_events.json"
    healing_events: list[dict] = []
    if healing_path.is_file():
        try:
            hdata = json.loads(healing_path.read_text(encoding="utf-8"))
            healing_events = hdata.get("healing_events", [])
        except Exception:
            pass
    data["healing_events"] = healing_events

    # Healing success rate
    if healing_events:
        successes = sum(1 for e in healing_events if e.get("success"))
        data["healing_success_rate"] = round(successes / len(healing_events) * 100)
    else:
        data["healing_success_rate"] = 0

    # ---- Run directory absolute path ----
    data["run_dir_abs"] = str(run_dir)

    # Console log abs path
    if console_path.is_file():
        data["console_log_abs"] = str(console_path)
    else:
        data["console_log_abs"] = ""

    # ---- Generated timestamp ----
    data["generated_at"] = _dt.datetime.now().isoformat(timespec="seconds")

    return data


def _render_html(data: dict[str, Any]) -> str:
    """使用 Jinja2 渲染 HTML / Render HTML with Jinja2."""
    try:
        from jinja2 import Environment, PackageLoader
    except ImportError:
        raise ImportError(
            "jinja2 is required for HTML report generation. "
            "Install it with: pip install jinja2"
        )

    env = Environment(
        loader=PackageLoader("phone_pilot.core", "templates"),
        autoescape=True,
    )
    template = env.get_template("report.html.j2")
    return template.render(**data)


def _extract_query(detail: str) -> str:
    """从 step detail 提取查找的文本 / Extract query text from step detail.

    匹配格式：'xxx' @ (x, y) 或 'xxx' 未找到 (重试 N 次)
    """
    if not detail:
        return ""
    import re
    m = re.match(r"'([^']+)'", detail)
    return m.group(1) if m else ""


def _classify_retry_steps(steps: list[dict]) -> list[dict]:
    """处理重试步骤：隐藏失败的中间步骤，在成功步骤上标注重试次数。

    逻辑：
    1. 扫描全部步骤，收集每个 (action, query) 的成功和失败信息
    2. 对于失败步骤：若后续有相同 action+query 成功的，直接移除（不显示）
    3. 对于成功步骤：若之前有相同 action+query 失败的，标注重试信息
    """
    if not steps:
        return steps

    # Pass 1: collect success and failure counts per (action, query)
    query_fail_count: dict[tuple[str, str], int] = {}
    query_has_success: set[tuple[str, str]] = set()
    for s in steps:
        action = s.get("action", "")
        query = _extract_query(s.get("detail", ""))
        if not query:
            continue
        key = (action, query)
        if s.get("status") in ("ok", "passed"):
            query_has_success.add(key)
        else:
            query_fail_count[key] = query_fail_count.get(key, 0) + 1

    # Pass 2: filter and annotate
    result = []
    for s in steps:
        s = dict(s)  # shallow copy
        action = s.get("action", "")
        query = _extract_query(s.get("detail", ""))
        key = (action, query) if query else None

        if s.get("status") not in ("ok", "passed"):
            # Failed step: hide it if a later step succeeded with same query
            if key and key in query_has_success:
                continue  # skip — don't add to result
        else:
            # Successful step: annotate with retry count if there were prior failures
            if key and key in query_fail_count and query_fail_count[key] > 0:
                fail_n = query_fail_count[key]
                s["retry_info"] = f"第 {fail_n + 1} 次尝试成功"
        result.append(s)

    return result


def _load_memory_data(meminfo_dir: Path) -> dict[str, Any]:
    """加载并结构化预处理内存分析数据 / Load and preprocess memory analysis data.

    按文件名分类为 before/after/diff/leak_report/shark_analysis/timeline/perfetto 等，
    提取有价值的展示数据，生成告警标签。
    """
    mem: dict[str, Any] = {}
    if not meminfo_dir.is_dir():
        return mem

    # Read all JSON files
    raw_files: dict[str, dict] = {}
    for f in sorted(meminfo_dir.iterdir()):
        if f.suffix == ".json" and f.is_file():
            try:
                raw_files[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass

    if not raw_files:
        return mem

    # ---- before / after snapshots ----
    for key in ("before", "after"):
        raw = raw_files.get(key)
        if not raw or not isinstance(raw, dict):
            continue
        summary = raw.get("summary", {})
        objects = raw.get("objects", {})
        mem[key] = {
            "package": raw.get("package", ""),
            "captured_at": raw.get("captured_at", ""),
            "total_pss_mb": summary.get("total_pss_mb", 0),
            "native_heap_mb": summary.get("native_heap_mb", 0),
            "dalvik_heap_mb": summary.get("dalvik_heap_mb", 0),
            "java_heap_mb": summary.get("java_heap_mb", 0),
            "graphics_mb": summary.get("graphics_mb", 0),
            "gl_mtrack_mb": round(summary.get("gl_mtrack_kb", 0) / 1024, 2),
            "egl_mtrack_mb": round(summary.get("egl_mtrack_kb", 0) / 1024, 2),
            "objects": objects,
            # Bar chart segments (for stacked bar)
            "segments": _build_mem_segments(summary),
        }

    # ---- diff ----
    raw_diff = raw_files.get("diff")
    if raw_diff and isinstance(raw_diff, dict):
        diff_data = raw_diff.get("diff", {})
        before_d = raw_diff.get("before", {})
        after_d = raw_diff.get("after", {})
        alerts = []
        # Generate alerts based on thresholds
        pss_pct = diff_data.get("total_pss_percent", 0)
        if pss_pct > 20:
            alerts.append({"level": "error", "text": f"内存异常增长 +{pss_pct:.1f}%"})
        elif pss_pct > 5:
            alerts.append({"level": "warn", "text": f"内存增长关注 +{pss_pct:.1f}%"})

        native_pct = diff_data.get("native_heap_percent", 0)
        if native_pct > 20:
            alerts.append({"level": "error", "text": f"Native Heap 异常增长 +{native_pct:.1f}%"})

        dalvik_pct = diff_data.get("dalvik_heap_percent", 0)
        if dalvik_pct > 20:
            alerts.append({"level": "error", "text": f"Dalvik Heap 异常增长 +{dalvik_pct:.1f}%"})

        # Build comparison rows for the diff table
        diff_rows = []
        for label, field in [
            ("Total PSS", "total_pss"),
            ("Native Heap", "native_heap"),
            ("Dalvik Heap", "dalvik_heap"),
            ("Java Heap", "java_heap"),
            ("Graphics", "graphics"),
        ]:
            b_val = before_d.get(f"{field}_mb", 0)
            a_val = after_d.get(f"{field}_mb", 0)
            d_val = diff_data.get(f"{field}_mb", 0)
            d_pct = diff_data.get(f"{field}_percent", 0)
            level = "error" if d_pct > 20 else ("warn" if d_pct > 5 else "ok")
            diff_rows.append({
                "label": label, "before": b_val, "after": a_val,
                "delta": d_val, "percent": d_pct, "level": level,
            })

        mem["diff"] = {
            "before": before_d,
            "after": after_d,
            "diff": diff_data,
            "rows": diff_rows,
            "alerts": alerts,
        }

    # ---- leak_report ----
    raw_leak = raw_files.get("leak_report")
    if raw_leak and isinstance(raw_leak, dict):
        leaked = raw_leak.get("leaked", False)
        initial = raw_leak.get("initial", {})
        after_gc = raw_leak.get("after_gc", {})
        gc_info = raw_leak.get("gc", {})
        leak_info: dict[str, Any] = {
            "leaked": leaked,
            "verdict": raw_leak.get("verdict", ""),
            "package": raw_leak.get("package", ""),
            "activities_in_memory": initial.get("activities_in_memory", 0),
            "visible_count": initial.get("visible_count", 0),
            "visible_activities": initial.get("visible_activities", []),
        }
        if leaked:
            leak_info["leak_count"] = raw_leak.get("leak_count", 0)
            leak_info["leaked_activities"] = raw_leak.get("leaked_activities", [])
            leak_info["after_gc_activities"] = after_gc.get("activities_in_memory", 0)
            leak_info["gc_performed"] = bool(gc_info)
            leak_info["hprof_path"] = raw_leak.get("hprof", "")
        mem["leak_report"] = leak_info

    # ---- shark_analysis ----
    raw_shark = raw_files.get("shark_analysis")
    if raw_shark and isinstance(raw_shark, dict):
        mem["shark_analysis"] = raw_shark

    # ---- timeline (multi-sample) ----
    raw_timeline = raw_files.get("timeline")
    if raw_timeline and isinstance(raw_timeline, dict):
        samples = raw_timeline.get("samples", [])
        if samples:
            mem["timeline"] = {
                "samples": samples,
                "count": len(samples),
                # SVG chart data: normalize to percentage for rendering
                "chart_data": _build_timeline_chart(samples),
            }

    # ---- perfetto_analysis ----
    raw_perfetto = raw_files.get("perfetto_analysis")
    if raw_perfetto and isinstance(raw_perfetto, dict):
        mem["perfetto"] = raw_perfetto

    # ---- Objects diff (compare before/after) ----
    if "before" in mem and "after" in mem:
        b_obj = mem["before"].get("objects", {})
        a_obj = mem["after"].get("objects", {})
        obj_diff = []
        for key in ("Views", "Activities", "ViewRootImpl", "AppContexts", "WebViews"):
            bv = b_obj.get(key, 0)
            av = a_obj.get(key, 0)
            delta = av - bv
            obj_diff.append({"name": key, "before": bv, "after": av, "delta": delta})
        mem["objects_diff"] = obj_diff

    return mem


def _build_mem_segments(summary: dict) -> list[dict]:
    """构建内存分布堆叠柱状图数据 / Build memory distribution stacked bar data."""
    segments = []
    total = summary.get("total_pss_kb", 1) or 1
    items = [
        ("Native Heap", summary.get("native_heap_kb", 0), "#ef4444"),
        ("Dalvik Heap", summary.get("dalvik_heap_kb", 0), "#f59e0b"),
        ("GL mtrack", summary.get("gl_mtrack_kb", 0), "#8b5cf6"),
        ("EGL mtrack", summary.get("egl_mtrack_kb", 0), "#6366f1"),
        ("Graphics", summary.get("graphics_kb", 0), "#06b6d4"),
    ]
    used = 0
    for name, kb, color in items:
        if kb > 0:
            pct = round(kb / total * 100, 1)
            mb = round(kb / 1024, 1)
            segments.append({"name": name, "kb": kb, "mb": mb, "pct": pct, "color": color})
            used += kb
    others_kb = total - used
    if others_kb > 0:
        segments.append({
            "name": "Others",
            "kb": others_kb,
            "mb": round(others_kb / 1024, 1),
            "pct": round(others_kb / total * 100, 1),
            "color": "#94a3b8",
        })
    return segments


def _build_timeline_chart(samples: list[dict]) -> dict:
    """构建时间线折线图 SVG 数据 / Build timeline line chart SVG data."""
    if not samples:
        return {}
    # Extract data points
    points = []
    max_mb = 1.0
    for s in samples:
        ts = s.get("elapsed_s", 0)
        pss = s.get("total_pss_mb", 0)
        native = s.get("native_heap_mb", 0)
        dalvik = s.get("dalvik_heap_mb", 0)
        max_mb = max(max_mb, pss, native, dalvik)
        points.append({"ts": ts, "pss": pss, "native": native, "dalvik": dalvik})
    return {
        "points": points,
        "max_mb": max_mb,
        "max_ts": points[-1]["ts"] if points else 0,
    }


def _read_json(path: Path) -> Optional[dict]:
    """安全读取 JSON 文件 / Safely read JSON file."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
