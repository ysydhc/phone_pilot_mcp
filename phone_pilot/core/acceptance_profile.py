"""Pluggable Acceptance Profile engine for PhonePilot v4.0.

Decouples the universal mobile automation & report engine (`phone_pilot/core/*`)
from domain-specific business semantics (e.g., E-Commerce, Mini-Games, Ad SDKs).

When no `acceptance_profile.json` is configured in the project or run directory,
PhonePilot automatically uses `DEFAULT_GENERIC_PROFILE` (Universal Mobile App
Acceptance: Launch -> Navigation -> UI Action -> Network/Mock -> Assertion -> No Crash/ANR).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

PROFILE_SCHEMA = "phone_pilot.acceptance_profile.v1"

DEFAULT_GENERIC_PROFILE: dict[str, Any] = {
    "schema": PROFILE_SCHEMA,
    "profile_id": "generic_app",
    "profile_name": "通用移动应用验收视图 (Universal App Profile)",
    "lifecycle_stages": [
        {
            "id": "launch",
            "label": "应用启动",
            "patterns": [
                "launch",
                "am start",
                "Displayed",
                "ActivityTaskManager",
                "INIT_OK",
                "INIT_START",
                "PID",
            ],
            "default_on_pass": True,
        },
        {
            "id": "network",
            "label": "网络/Mock打桩",
            "patterns": [
                "MockServer",
                "reverse tcp:",
                "cmd=env",
                "HTTP",
                "200",
                "broadcast",
            ],
            "default_on_pass": False,
        },
        {
            "id": "interaction",
            "label": "UI 交互执行",
            "patterns": [
                "tap",
                "swipe",
                "input_text",
                "UIAction",
                "点击",
                "触发",
                "find_text",
            ],
            "default_on_pass": True,
        },
        {
            "id": "assertion",
            "label": "核心业务断言",
            "patterns": [
                "PASS",
                "SUCCESS",
                "ASSERT_OK",
                "VERIFY_OK",
                "loaded successfully",
                "ready",
            ],
            "default_on_pass": True,
        },
        {
            "id": "stability",
            "label": "无崩溃/ANR",
            "anti_patterns": [
                "FATAL EXCEPTION",
                "ANR in",
                "Process crashing",
                "Native crash",
            ],
            "default_on_pass": True,
        },
    ],
    "log_filter": {
        "include_tokens": [
            "ActivityTaskManager",
            "Displayed",
            "MockServer",
            "[PASS]",
            "[FAIL]",
            ">> ",
            ">>> ",
            "结果：",
            "FATAL EXCEPTION",
            "ANR in",
            "AndroidRuntime",
            "ASSERT_OK",
            "VERIFY_OK",
        ],
        "exclude_tokens": [
            "[cmd] adb",
            "logcat 等待中",
            "uiautomator dump",
            "screencap -p",
        ],
        "highlights": {
            "success": [
                "[PASS]",
                "✔ PASS",
                "SUCCESS",
                "ASSERT_OK",
                "VERIFY_OK",
            ],
            "metric": [
                "duration=",
                "elapsed=",
                "latency=",
                "cost:",
                "status=200",
            ],
            "network": [
                "MockServer",
                "HTTP",
                "am broadcast",
                "reverse tcp:",
            ],
            "danger": [
                "FAIL",
                "FAILED",
                "ERROR",
                "FATAL EXCEPTION",
                "ANR in",
                "Exception",
            ],
        },
    },
}


def load_acceptance_profile(run_dir: Path) -> dict[str, Any]:
    """Load project-specific acceptance_profile.json if available, else return DEFAULT_GENERIC_PROFILE."""
    run_dir = Path(run_dir).expanduser().resolve()
    candidates: list[Path] = []
    env_path = os.environ.get("PHONE_PILOT_ACCEPTANCE_PROFILE")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.extend(
        [
            run_dir / "acceptance_profile.json",
            run_dir.parent / "acceptance_profile.json",
            run_dir.parent.parent / "acceptance_profile.json",
            Path.cwd() / "acceptance_profile.json",
            Path.cwd() / "tests" / "e2e" / "acceptance_profile.json",
            Path.cwd() / ".recordings" / "acceptance_profile.json",
        ]
    )
    for cand in candidates:
        try:
            if cand.is_file():
                data = json.loads(cand.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("lifecycle_stages"):
                    merged = dict(DEFAULT_GENERIC_PROFILE)
                    merged.update(data)
                    if "log_filter" in data and isinstance(data["log_filter"], dict):
                        lf = dict(DEFAULT_GENERIC_PROFILE["log_filter"])
                        lf.update(data["log_filter"])
                        merged["log_filter"] = lf
                    return merged
        except Exception:
            continue
    return dict(DEFAULT_GENERIC_PROFILE)


def evaluate_lifecycle_pills(
    profile: dict[str, Any],
    log_lines: list[str],
    *,
    status: str = "PASS",
    actions: Optional[list[dict[str, Any]]] = None,
    detail_text: str = "",
) -> list[dict[str, Any]]:
    """Evaluate lifecycle stages against log lines and action metadata to produce visual pills."""
    stages = profile.get("lifecycle_stages") or DEFAULT_GENERIC_PROFILE["lifecycle_stages"]
    combined_lines = list(log_lines or [])
    if detail_text:
        combined_lines.append(detail_text)
    if actions:
        for act in actions:
            combined_lines.append(json.dumps(act, ensure_ascii=False))

    pills: list[dict[str, Any]] = []
    is_pass = str(status).upper() in ("PASS", "PASSED", "SUCCESS", "OK")

    for st in stages:
        st_id = st.get("id", "stage")
        label = st.get("label", st_id)
        patterns = st.get("patterns") or []
        anti_patterns = st.get("anti_patterns") or []
        default_on_pass = bool(st.get("default_on_pass", False))

        matched_line = None
        anti_matched = None

        if anti_patterns:
            for ln in combined_lines:
                if "no reason_anr" in ln.lower():
                    continue
                if any(
                    re.search(rf"(?<![A-Za-z0-9_]){re.escape(ap)}", ln, re.IGNORECASE)
                    for ap in anti_patterns
                    if ap
                ):
                    anti_matched = ln
                    break
            if anti_matched:
                pills.append(
                    {
                        "id": st_id,
                        "label": label,
                        "status": "FAIL",
                        "evidence": anti_matched[:180],
                    }
                )
            else:
                pills.append(
                    {
                        "id": st_id,
                        "label": label,
                        "status": "PASS",
                        "evidence": "全链路无 Crash / ANR 异常",
                    }
                )
            continue

        if st_id == "assertion" and not is_pass:
            fail_line = next(
                (
                    ln.strip()
                    for ln in reversed(combined_lines)
                    if any(tok in ln for tok in ("✖ FAIL", "[FAIL]", "超时", "失败", "ERROR", "Error", "TIMEOUT"))
                    and not ln.strip().startswith("{")
                ),
                detail_text.strip() or "核心业务断言未通过",
            )
            pills.append(
                {
                    "id": st_id,
                    "label": label,
                    "status": "FAIL",
                    "evidence": fail_line[:180],
                }
            )
            continue

        for ln in combined_lines:
            if any(p.lower() in ln.lower() for p in patterns):
                matched_line = ln
                break

        if matched_line:
            clean_ev = matched_line.strip()
            if clean_ev.startswith("{") and "type" in clean_ev:
                clean_ev = f"已配置/执行动作阶段: {label}"
            pills.append(
                {
                    "id": st_id,
                    "label": label,
                    "status": "PASS",
                    "evidence": clean_ev[:180],
                }
            )
        elif default_on_pass and is_pass:
            pills.append(
                {
                    "id": st_id,
                    "label": label,
                    "status": "PASS",
                    "evidence": f"{label} 验证通过",
                }
            )
        elif not is_pass and st.get("required_on_fail", False):
            pills.append(
                {
                    "id": st_id,
                    "label": label,
                    "status": "FAIL",
                    "evidence": f"未检测到 {label} 预期信号",
                }
            )
        else:
            pills.append(
                {
                    "id": st_id,
                    "label": label,
                    "status": "PASS" if (is_pass and st_id in ("env", "launch", "load", "show")) else "NA",
                    "evidence": f"{label} 已就绪" if is_pass else "当前场景不涉及或未触发",
                }
            )
    return pills


def extract_step_annotations(
    steps: list[dict[str, Any]],
    *,
    screen_w: int = 1080,
    screen_h: int = 2400,
) -> dict[str, dict[str, Any]]:
    """Extract universal tap coordinates and target labels for screenshots from steps.json."""
    annotations: dict[str, dict[str, Any]] = {}
    coord_re = re.compile(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)")
    quote_re = re.compile(r"['\"]([^'\"]+)['\"]")

    for st in steps:
        shot = st.get("screenshot")
        if not shot:
            continue
        shot_name = Path(str(shot)).name
        action = str(st.get("action", ""))
        detail = str(st.get("detail", ""))
        combined = f"{action} {detail}"

        m_coord = coord_re.search(combined)
        cx: Optional[int] = None
        cy: Optional[int] = None
        if m_coord:
            cx = int(m_coord.group(1))
            cy = int(m_coord.group(2))
        elif isinstance(st.get("x"), (int, float)) and isinstance(st.get("y"), (int, float)):
            cx = int(st["x"])
            cy = int(st["y"])

        label = action
        m_q = quote_re.search(detail)
        if m_q:
            label = f"{action}: {m_q.group(1)}"
        elif detail and len(detail) <= 36:
            label = f"{action} ({detail})"

        if cx is not None and cy is not None and screen_w > 0 and screen_h > 0:
            x_pct = round(max(2.0, min(98.0, (cx / screen_w) * 100.0)), 1)
            y_pct = round(max(2.0, min(98.0, (cy / screen_h) * 100.0)), 1)
            annotations[shot_name] = {
                "has_coords": True,
                "tap_x": cx,
                "tap_y": cy,
                "tap_x_pct": x_pct,
                "tap_y_pct": y_pct,
                "label": f"🎯 ({cx}, {cy}) {label[:32]}",
                "step_num": st.get("step"),
            }
        else:
            annotations[shot_name] = {
                "has_coords": False,
                "label": label[:40],
                "step_num": st.get("step"),
            }
    return annotations


def extract_scene_specific_logs(
    profile: dict[str, Any],
    run_dir: Path,
    scene: dict[str, Any],
    all_key_logs: list[str],
    gate_summary: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Extract logs specifically belonging to a single scene so the reviewer doesn't read unrelated logs."""
    scene_logs: list[str] = []
    title = str(scene.get("title", "")).strip()
    detail = str(scene.get("detail", "")).strip()

    # 1. Check gate_summary / suite_summary if available
    if isinstance(gate_summary, dict):
        # Match in gate_summary["suites"] or gate_summary["summary"]
        for st in gate_summary.get("suites") or []:
            if not isinstance(st, dict):
                continue
            s_name = str(st.get("suite") or st.get("key") or "")
            if title and (title in s_name or s_name in title):
                for d in st.get("details") or []:
                    if not isinstance(d, dict):
                        continue
                    d_label = d.get("label") or d.get("name") or ""
                    d_ok = "✔ PASS" if d.get("passed") else "✖ FAIL"
                    hits = d.get("hit_lines") or {}
                    if isinstance(hits, dict) and hits:
                        scene_logs.append(f"[{d_ok}] 子项「{d_label}」关键日志命中：")
                        for pat, hit in hits.items():
                            if hit:
                                scene_logs.append(f"  🎯 ({pat}) -> {hit}")
                    elif d.get("reason"):
                        scene_logs.append(f"[{d_ok}] 子项「{d_label}」: {d.get('reason')}")
        for item in gate_summary.get("summary") or []:
            if not isinstance(item, dict):
                continue
            s_name = str(item.get("suite") or "")
            if title and (title in s_name or s_name in title):
                ok_tag = "✔ PASS" if item.get("ok") else "✖ FAIL"
                scene_logs.append(
                    f"[{ok_tag}] 套件「{s_name}」({item.get('file', '')}) 耗时 {item.get('elapsed_s', 0)}s：{item.get('detail', '')}"
                )

    # 2. Search console.log for the section belonging to this suite/scene
    console_path = run_dir / "console.log"
    if console_path.is_file() and title:
        try:
            raw_console = console_path.read_text(encoding="utf-8", errors="replace")
            lines = raw_console.splitlines()
            in_section = False
            noise = tuple(
                (profile.get("log_filter") or {}).get("exclude_tokens")
                or DEFAULT_GENERIC_PROFILE["log_filter"]["exclude_tokens"]
            )
            for ln in lines:
                s = ln.strip()
                if not s:
                    continue
                if ">>> 套件：" in s or "=== [" in s:
                    if title in s or any(w in s for w in title.split() if len(w) >= 2):
                        in_section = True
                        scene_logs.append(s)
                        continue
                    elif in_section:
                        break
                if in_section:
                    if any(nt in s for nt in noise):
                        continue
                    scene_logs.append(s)
        except Exception:
            pass

    # 3. If single-scene or still empty, filter all_key_logs using profile highlights or status tokens
    if not scene_logs:
        if detail:
            scene_logs.append(f"[场景执行结论] {title} -> {detail}")
        hl = ((profile or {}).get("log_filter") or {}).get("highlights") or {}
        dyn_tokens = tuple(
            dict.fromkeys(
                list(hl.get("success") or [])
                + list(hl.get("metric") or [])
                + ["PASS", "FAIL"]
            )
        )
        matched = [
            ln
            for ln in all_key_logs
            if (title and title in ln)
            or any(tok in ln for tok in dyn_tokens)
        ]
        scene_logs.extend(matched[:25])

    # Deduplicate while preserving order
    deduped: list[str] = []
    seen: set[str] = set()
    for ln in scene_logs:
        if ln not in seen:
            seen.add(ln)
            deduped.append(ln)
    return deduped[:40]


def build_markdown_report_summary(
    run_name: str,
    meta: dict[str, Any],
    business: dict[str, Any],
    catalog: dict[str, Any],
    key_logs: list[str],
    profile: dict[str, Any],
) -> str:
    """Build a human-friendly Markdown acceptance summary for one-click pasting to iCafe / PR / IM."""
    scenes = catalog.get("scenes") or []
    total_scenes = catalog.get("total_scenes") or len(scenes) or 1
    passed_scenes = catalog.get("passed_scenes") or sum(
        1 for s in scenes if s.get("status") == "PASS"
    )
    all_pass = passed_scenes == total_scenes
    verdict_icon = "🟢 PASS" if all_pass else f"🔴 FAIL ({passed_scenes}/{total_scenes} 通过)"

    dev = meta.get("device") or {}
    serial = dev.get("serial") or "-"
    platform = dev.get("platform") or "android"
    dur_s = float(meta.get("duration_s") or 0)
    dur_str = f"{int(dur_s // 60)}分{int(dur_s % 60)}秒" if dur_s >= 60 else f"{dur_s:.1f}秒"
    git_info = meta.get("git") or {}
    git_str = (
        f"{git_info.get('branch', 'master')} @ {git_info.get('commit', '-')}"
        if git_info.get("commit")
        else "本地工作区"
    )

    lines = [
        f"### {'✅' if all_pass else '⚠️'} [PhonePilot 真机自动化验收报告] `{run_name}`",
        f"- **验收结论**：**{verdict_icon}**（场景通过率 `{passed_scenes}/{total_scenes}` · 工具步通过率 `{meta.get('steps', {}).get('passed', 0)}/{meta.get('steps', {}).get('total', 0)}`）",
        f"- **测试设备**：`{serial}` ({platform}) | **执行耗时**：`{dur_str}` | **代码版本**：`{git_str}`",
        f"- **验收配置**：`{profile.get('profile_name', 'Universal App Profile')}`",
    ]

    if scenes:
        lines.append("- **核心场景核验明细**：")
        for sc in scenes[:20]:
            st_icon = "✔" if sc.get("status") == "PASS" else "✖"
            lines.append(
                f"  - `{st_icon}` **#{sc.get('index')} [{sc.get('group_label', '场景')}] {sc.get('title')}**：{sc.get('detail') or sc.get('status')} (`{sc.get('elapsed_s', 0)}s`)"
            )

    hl_summary = ((profile or {}).get("log_filter") or {}).get("highlights") or {}
    summary_tokens = tuple(
        dict.fromkeys(
            list(hl_summary.get("success") or [])
            + list(hl_summary.get("metric") or [])
            + ["[PASS]", "[FAIL]"]
        )
    )
    sample_logs = [
        ln
        for ln in key_logs
        if any(k in ln for k in summary_tokens)
    ][:8]
    if sample_logs:
        lines.append("- **关键业务判定日志抽样**：")
        for sl in sample_logs:
            lines.append(f"  - `{sl[:140]}`")

    return "\n".join(lines)
