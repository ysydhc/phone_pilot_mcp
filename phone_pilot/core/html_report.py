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
import os
import io
from phone_pilot.core.safe_io import safe_directory
from pathlib import Path
from typing import Any, Optional


def _write_launcher_script(run_dir: Path) -> Path:
    """Write a double-clickable macOS .command / bash launcher next to report.html."""
    import shlex
    import sys

    py_bin = shlex.quote(sys.executable)
    run_name = run_dir.name
    script = f"""#!/usr/bin/env bash
set -euo pipefail
RUN_DIR="$(cd "$(dirname "$0")" && pwd)"
REC_DIR="$(cd "$RUN_DIR/../.." && pwd)"
RUN_NAME={shlex.quote(run_name)}
BASE_PORT="${{PP_WEB_PORT:-8686}}"
PORT=""

for p in $(seq "$BASE_PORT" $((BASE_PORT + 15))); do
  CAND_URL="http://127.0.0.1:${{p}}/run/${{RUN_NAME}}/report.html"
  if lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
    CODE="$(curl -s -o /dev/null -w "%{{http_code}}" "$CAND_URL" 2>/dev/null || echo 000)"
    if [ "$CODE" = "200" ]; then
      echo "[pp] 服务已在 :$p 运行且匹配当前报告，直接打开: $CAND_URL"
      open "$CAND_URL" >/dev/null 2>&1 || true
      exit 0
    fi
    continue
  fi
  PORT="$p"
  break
done

if [ -z "$PORT" ]; then
  echo "[pp] 错误: 端口 ${{BASE_PORT}}..$((BASE_PORT + 15)) 均被其他目录占用" >&2
  exit 1
fi

REPORT_URL="http://127.0.0.1:${{PORT}}/run/${{RUN_NAME}}/report.html"
echo "[pp] 正在启动 phone_pilot Web 服务 (recordings=$REC_DIR, port=$PORT)..."
(
  for _ in $(seq 1 30); do
    if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
      open "$REPORT_URL" >/dev/null 2>&1 || true
      break
    fi
    sleep 0.2
  done
) &

exec {py_bin} -m phone_pilot.web --recordings-dir "$REC_DIR" --port "$PORT" --no-browser --auto-approve
"""
    with safe_directory(run_dir, writable=True) as folder:
        folder.write_bytes("open_pp_server.command", script.encode("utf-8"), replace=True)
    launcher = run_dir / "open_pp_server.command"
    try:
        os.chmod(launcher, 0o755)
    except OSError:
        pass
    return launcher


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
    with safe_directory(run_dir, writable=True) as folder:
        folder.write_bytes("report.html", html.encode("utf-8"), replace=True)
    _write_launcher_script(run_dir)
    return report_path


def render_html_report(run_dir: str | Path) -> str:
    """Render a trusted template in memory; viewing archives never writes back."""
    directory = Path(run_dir).expanduser().resolve()
    with safe_directory(directory):
        return _render_html(_load_run_data(directory))


def _safe_files(directory):
    try:
        with safe_directory(directory) as folder:
            return sorted(
                name for name in os.listdir(folder.fd) if _regular(folder, name)
            )
    except (ValueError, OSError):
        return []


def _regular(folder, name):
    try:
        folder.stat(name)
        return True
    except (ValueError, OSError):
        return False


def _read_bytes(path):
    with safe_directory(path.parent) as folder:
        return folder.read_bytes(path.name)


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
    meta.setdefault(
        "artifacts",
        {
            "screenshots": 0,
            "screen_recordings": 0,
            "logcat_dumps": 0,
            "meminfo_snapshots": 0,
            "console_log_lines": 0,
        },
    )
    data["meta"] = meta

    # ---- task_context.json (需求/Bugfix 上下文说明) ----
    task_ctx = _read_json(run_dir / "task_context.json") or meta.get("task_context") or {}
    data["task_context"] = task_ctx if isinstance(task_ctx, dict) else {}

    # ---- freshness_gate.json & visual_judgments.json (P1 门禁与 VLM 裁判) ----
    freshness = _read_json(run_dir / "freshness_gate.json")
    data["freshness_gate"] = freshness if isinstance(freshness, dict) else {}
    vlm_raw = _read_json(run_dir / "visual_judgments.json")
    data["visual_judgments"] = vlm_raw if isinstance(vlm_raw, list) else []

    from phone_pilot.core.report_evidence import business_projection
    from phone_pilot.core.scene_restore import inspect_scene

    business = {}
    data["business_error"] = ""
    if (run_dir / "business_report.json").is_file():
        from phone_pilot.core.business_report import load_business_report

        try:
            business = load_business_report(run_dir)
            if not business.get("cases"):
                business = {}  # Uninstrumented sessions retain legacy tool rendering.
        except (ValueError, OSError, TypeError) as error:
            data["business_error"] = str(error)
            business = {}
    data["business"] = business_projection(run_dir, business) if business else {}
    from phone_pilot.core.manifest_generator import ensure_scene_manifest, get_online_devices_detailed
    from phone_pilot.core.scene_restore import _action_label
    try:
        ensure_scene_manifest(run_dir)
    except Exception:
        pass
    scene_info = inspect_scene(run_dir)
    raw_actions = scene_info.get("actions", [])
    planned_steps = [
        {"index": 1, "title": "启动 Mock 服务与设备预检", "detail": "锁定目标真机并加载本地 Mock 规则 (:8090)", "status": "PENDING"}
    ]
    for idx, act in enumerate(raw_actions, start=2):
        t, d = _action_label(act)
        planned_steps.append({"index": idx, "title": t, "detail": d, "status": "PENDING"})
    planned_steps.append(
        {"index": len(planned_steps) + 1, "title": "真机终态核验与截屏留证", "detail": "采集真机最终画面、UI 树与日志证据", "status": "PENDING"}
    )
    scene_info["planned_steps"] = planned_steps
    scenes_list = scene_info.get("scenes") or []
    default_sid = scene_info.get("default_scene_id")
    if scenes_list and default_sid:
        for sc in scenes_list:
            if sc.get("scene_id") == default_sid and sc.get("planned_steps"):
                scene_info["planned_steps"] = sc["planned_steps"]
                break
    try:
        pkg_name = (scene_info.get("package") or {}).get("name", "cc.admaster.android.demo")
        scene_info["online_devices"] = get_online_devices_detailed(target_package=pkg_name)
    except Exception:
        scene_info["online_devices"] = []
    data["scene"] = scene_info
    data["run_name"] = run_dir.name

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
    # New business records retain every tool attempt; legacy classification only
    # affects the old tool visualization, never the independent business verdict.
    if not business:
        steps = _classify_retry_steps(steps)
    data["steps"] = steps
    if data["business"]:
        for card in data["business"]["steps"]:
            for attempt in card["attempts"]:
                attempt["tools"] = [
                    tool
                    for tool in steps
                    if tool.get("parent_case_id") == attempt.get("case_id")
                    and tool.get("parent_step_id") == attempt.get("business_step_id")
                    and tool.get("parent_attempt_id") == attempt.get("attempt_id")
                ]

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
            name
            for name in _safe_files(screenshots_dir)
            if Path(name).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        )
        # Detect orientation from first screenshot
        if screenshots:
            try:
                from PIL import Image

                first_img = Image.open(
                    io.BytesIO(_read_bytes(screenshots_dir / screenshots[0]))
                )
                w, h = first_img.size
                first_img.close()
                is_portrait = h > w * 1.2  # Portrait if height > 1.2x width
            except Exception:
                pass
    # Build screenshot -> step mapping for gallery captions & tap bullseye annotations
    from phone_pilot.core.acceptance_profile import (
        load_acceptance_profile,
        evaluate_lifecycle_pills,
        extract_step_annotations,
        extract_scene_specific_logs,
        build_markdown_report_summary,
    )

    step_annots = extract_step_annotations(steps)
    step_by_screenshot = {s["screenshot"]: s for s in steps if s.get("screenshot")}
    gallery_items: list[dict] = []
    for img_name in screenshots:
        item: dict[str, Any] = {
            "filename": img_name,
            "step_num": None,
            "step_action": None,
            "step_detail": None,
            "annotation": step_annots.get(img_name),
        }
        # Explicit artifact identity, never a numeric filename-prefix guess:
        # repeated tool counters and actions retain independent observations.
        matched = step_by_screenshot.get(img_name)
        if matched:
            item["step_num"] = matched.get("step")
            item["step_action"] = matched.get("action", "")
            item["step_detail"] = (
                _extract_query(matched.get("detail", ""))
                or matched.get("detail", "")[:40]
            )
        gallery_items.append(item)

    data["screenshots"] = screenshots
    data["gallery_items"] = gallery_items
    data["is_portrait"] = is_portrait

    # ---- Screen recordings ----
    recordings_dir = run_dir / "recordings"
    recordings: list[dict[str, Any]] = []
    if recordings_dir.is_dir():
        for name in _safe_files(recordings_dir):
            if Path(name).suffix.lower() in (".mp4", ".webm", ".mkv"):
                f = recordings_dir / name
                size_mb = 0.0
                try:
                    with safe_directory(recordings_dir) as folder:
                        size_mb = round(folder.stat(name).st_size / (1024 * 1024), 2)
                except Exception:
                    pass
                recordings.append({
                    "filename": name,
                    "rel_path": f"recordings/{name}",
                    "size_mb": size_mb,
                    "abs_path": str(f),
                })
    data["recordings"] = recordings
    data["meta"]["artifacts"]["screen_recordings"] = len(recordings)

    # ---- Logcat files ----
    logcat_dir = run_dir / "logcat"
    logcat_files: list[dict] = []
    if logcat_dir.is_dir():
        for name in _safe_files(logcat_dir):
            f = logcat_dir / name
            if f.is_file():
                try:
                    with safe_directory(logcat_dir) as folder:
                        lines = sum(
                            block.count(b"\n") for block in folder.iter_chunks(f.name)
                        )
                        size_kb = folder.stat(f.name).st_size // 1024
                except Exception:
                    lines = 0
                    size_kb = 0
                logcat_files.append(
                    {
                        "name": f.name,
                        "rel_path": f"logcat/{f.name}",
                        "lines": lines,
                        "size_kb": size_kb,
                        "abs_path": str(f),
                        "abs_dir": str(f.parent),
                    }
                )
    data["logcat_files"] = logcat_files

    # ---- Console log tail ----
    console_path = run_dir / "console.log"
    console_tail = ""
    console_tail_lines = 50
    if console_path.is_file():
        try:
            with safe_directory(run_dir) as folder:
                with folder.open("console.log") as stream:
                    # Keep only a bounded tail, even for a multi-GB single line.
                    tail_bytes = b""
                    for block in iter(lambda: stream.read(65536), b""):
                        tail_bytes = (tail_bytes + block)[-65536:]
                    console_tail = "\n".join(
                        tail_bytes.decode("utf-8", errors="replace").splitlines()[
                            -console_tail_lines:
                        ]
                    )
        except Exception:
            pass
    data["console_tail"] = console_tail
    data["console_tail_lines"] = console_tail_lines

    # ---- Key Business Acceptance Logs & Pluggable Acceptance Profile ----
    profile = load_acceptance_profile(run_dir)
    data["acceptance_profile"] = profile
    key_log_lines = _extract_key_acceptance_logs(run_dir, console_path, logcat_files, profile=profile)
    data["key_acceptance_logs"] = "\n".join(key_log_lines)
    data["key_acceptance_log_count"] = len(key_log_lines)

    # Enrich scenes with lifecycle_pills & scene_logs if missing from older cached scene_catalog.json
    gate_summary = _read_json(run_dir / "release_gate_summary.json")
    for sc in scene_info.get("scenes") or []:
        if not sc.get("scene_logs"):
            sc["scene_logs"] = extract_scene_specific_logs(
                profile, run_dir, sc, key_log_lines, gate_summary=gate_summary
            )
        if not sc.get("lifecycle_pills"):
            sc["lifecycle_pills"] = evaluate_lifecycle_pills(
                profile,
                sc.get("scene_logs") or key_log_lines,
                status=sc.get("status", "PASS"),
                actions=sc.get("actions", []),
                detail_text=f"{sc.get('title', '')} {sc.get('detail', '')}",
            )
        if "video_highlight_s" not in sc:
            offset_s = int(sc.get("video_offset_s", 0))
            el_s = float(sc.get("elapsed_s", 0.0))
            hl_s = offset_s + max(0, int(el_s - 6.0)) if el_s >= 9.0 else offset_s
            sc["video_highlight_s"] = hl_s
            sc["video_highlight_label"] = f"{hl_s // 60:02d}:{hl_s % 60:02d}"
        if sc.get("screenshot") and not sc.get("tap_annotation"):
            sc["tap_annotation"] = step_annots.get(Path(str(sc["screenshot"])).name)

    overall_status = (
        "PASS"
        if (scene_info.get("catalog") or {}).get("gate_pass", meta.get("exit_code") == 0)
        else "FAIL"
    )
    data["global_lifecycle_pills"] = evaluate_lifecycle_pills(
        profile,
        key_log_lines,
        status=overall_status,
        actions=raw_actions,
        detail_text=str(meta.get("script_name", "")),
    )
    data["markdown_summary"] = build_markdown_report_summary(
        run_dir.name,
        meta,
        data.get("business") or {},
        scene_info.get("catalog") or {"scenes": scene_info.get("scenes") or []},
        key_log_lines,
        profile,
    )

    # ---- Meminfo files (structured) ----
    data["memory"] = _load_memory_data(run_dir / "meminfo")
    # Legacy compat: keep meminfo_files non-empty flag for nav link
    data["meminfo_files"] = bool(data["memory"])

    # ---- Healing events ----
    healing_path = run_dir / "healing_events.json"
    healing_events: list[dict] = []
    if healing_path.is_file():
        try:
            hdata = json.loads(_read_bytes(healing_path))
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
    for name in _safe_files(meminfo_dir):
        f = meminfo_dir / name
        if f.suffix == ".json" and f.is_file():
            try:
                raw_files[f.stem] = json.loads(_read_bytes(f))
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
            alerts.append(
                {"level": "error", "text": f"Native Heap 异常增长 +{native_pct:.1f}%"}
            )

        dalvik_pct = diff_data.get("dalvik_heap_percent", 0)
        if dalvik_pct > 20:
            alerts.append(
                {"level": "error", "text": f"Dalvik Heap 异常增长 +{dalvik_pct:.1f}%"}
            )

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
            diff_rows.append(
                {
                    "label": label,
                    "before": b_val,
                    "after": a_val,
                    "delta": d_val,
                    "percent": d_pct,
                    "level": level,
                }
            )

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
            segments.append(
                {"name": name, "kb": kb, "mb": mb, "pct": pct, "color": color}
            )
            used += kb
    others_kb = total - used
    if others_kb > 0:
        segments.append(
            {
                "name": "Others",
                "kb": others_kb,
                "mb": round(others_kb / 1024, 1),
                "pct": round(others_kb / total * 100, 1),
                "color": "#94a3b8",
            }
        )
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
        return json.loads(_read_bytes(path))
    except Exception:
        return None


def _extract_key_acceptance_logs(
    run_dir: Path,
    console_path: Path,
    logcat_files: list[dict],
    *,
    profile: Optional[dict] = None,
) -> list[str]:
    """提取人眼验收最关心的关键业务判定日志（由 AcceptanceProfile 驱动，剔除轮询刷屏噪音）。"""
    out: list[str] = []
    seen: set[str] = set()
    log_filter = (profile or {}).get("log_filter") or {}
    custom_includes = tuple(log_filter.get("include_tokens") or ())
    custom_excludes = tuple(log_filter.get("exclude_tokens") or ())

    def _add(line: str) -> None:
        s = line.strip()
        if not s or s in seen:
            return
        seen.add(s)
        out.append(s[:260] + ("..." if len(s) > 260 else ""))

    # 1. Extract verified logcat hit_lines from release_gate_summary.json / suite_summary.json
    gate_summary = _read_json(run_dir / "release_gate_summary.json") or _read_json(
        run_dir / "suite_summary.json"
    )
    if isinstance(gate_summary, dict):
        suites = gate_summary.get("suites") or []
        for st in suites:
            if not isinstance(st, dict):
                continue
            s_name = st.get("suite") or st.get("key") or "Suite"
            s_status = "PASS" if st.get("passed") else "FAIL"
            _add(f"━━━ [门禁套件: {s_name}] 判定结果: {s_status} ({st.get('pass_count', 0)}/{st.get('total', 0)} 项通过) ━━━")
            for detail in st.get("details") or []:
                if not isinstance(detail, dict):
                    continue
                d_label = detail.get("label") or detail.get("name") or ""
                d_ok = "✔ PASS" if detail.get("passed") else "✖ FAIL"
                hit_lines = detail.get("hit_lines") or {}
                if isinstance(hit_lines, dict) and hit_lines:
                    _add(f"  [{d_ok}] 场景「{d_label}」关键日志命中：")
                    for pat, hit in hit_lines.items():
                        if hit:
                            _add(f"    🎯 ({pat}) -> {hit}")
                elif detail.get("reason"):
                    _add(f"  [{d_ok}] 场景「{d_label}」：{detail.get('reason')}")

    # 2. Extract high-signal milestones from console.log (excluding [cmd] adb & polling noise)
    if console_path.is_file():
        try:
            raw_console = _read_bytes(console_path).decode("utf-8", errors="replace")
            _noise_tokens = custom_excludes or (
                "[cmd] adb",
                "logcat 等待中",
                "uiautomator dump",
                "screencap -p",
            )
            _milestone_tokens = custom_includes or (
                ">> logcat 发现",
                ">> am broadcast",
                ">>> 套件：",
                "结果：",
                "[PASS]",
                "[FAIL]",
                "RELEASE GATE:",
                "=== [",
                "MockServer",
                "onAdLoaded",
                "LOAD_SUCCESS",
                "SHOW_OK",
                "IMPRESSION",
                "CLICK_OK",
                "AD_CLOSE",
                "app_ad_rev",
                "s_vld=",
            )
            console_hits: list[str] = []
            for line in raw_console.splitlines():
                s = line.strip()
                if not s or any(nt in s for nt in _noise_tokens):
                    continue
                if any(mt in s for mt in _milestone_tokens):
                    console_hits.append(s)
            if console_hits:
                if out:
                    _add("━━━ [测试执行关键判定流水 (已自动过滤 ADB 轮询噪音)] ━━━")
                for ch in console_hits[-80:]:
                    _add(ch)
        except Exception:
            pass

    # 3. Extract lifecycle callbacks from logcat/*.log or latest scene_restores/*/logcat.txt
    _sdk_tokens = custom_includes or (
        "ActivityTaskManager",
        "Displayed",
        "AdTest",
        "[AdMaster/",
        "AdDebugPanel",
        "Adm-AdRevReporter",
        "AdMasterApplication",
        "SVldManualTest",
        "onAdLoaded",
        "onAdShow",
        "onAdClick",
        "onAdClose",
        "bidding success",
        "ready to show",
        "FATAL EXCEPTION",
        "ANR in",
    )
    candidate_logs: list[Path] = []
    for lf in logcat_files:
        p = Path(lf.get("abs_path", ""))
        if p.is_file():
            candidate_logs.append(p)
    restores_dir = run_dir / "scene_restores"
    if restores_dir.is_dir():
        try:
            restore_logs = sorted(
                restores_dir.glob("*/logcat.txt"),
                key=lambda x: x.stat().st_mtime,
                reverse=True,
            )
            if restore_logs:
                candidate_logs.append(restore_logs[0])
        except Exception:
            pass

    for log_path in candidate_logs[:3]:
        try:
            raw_lc = _read_bytes(log_path).decode("utf-8", errors="replace")
            sdk_hits = [
                ln.strip()
                for ln in raw_lc.splitlines()
                if ln.strip() and any(tok in ln for tok in _sdk_tokens)
            ]
            if sdk_hits:
                src_label = (
                    "最新真机复原进程 Logcat"
                    if "scene_restores" in str(log_path)
                    else f"Logcat ({log_path.name})"
                )
                _add(f"━━━ [{src_label} SDK 关键回调摘录] ━━━")
                for sh in sdk_hits[-45:]:
                    _add(sh)
        except Exception:
            pass

    return out[:160]
