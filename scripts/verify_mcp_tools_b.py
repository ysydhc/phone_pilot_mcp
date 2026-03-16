#!/usr/bin/env python3
"""B 类 MCP 工具真机验证：按前置条件规划执行顺序，逐工具调用并 validate_response，对失败项做详细记录并写回检查表。

前置条件约定：
- 需「应用已安装」：先执行 install_app（由 A 类或本脚本 prep 完成），再执行 clear_data / force_stop / uninstall 等。
- phone_force_stop：与 phone_launch_app 配对，先 launch_app(package) 再 force_stop(package)。
- 多数 B 类在「设置首页」下验证：先 go_home、launch_app(com.android.settings)，再执行 tap/swipe/find_element 等。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKLIST_PATH = ROOT / "docs" / "verification" / "MCP_TOOLS_VERIFICATION.md"
RESOURCES_PATH = ROOT / "docs" / "verification" / "verification_resources.json"
FAILURES_PATH = ROOT / "docs" / "verification" / "b_class_failures.json"


def get_device_serial() -> str | None:
    serial = os.environ.get("PHONE_PILOT_DEVICE_SERIAL", "").strip()
    if serial:
        return serial
    if RESOURCES_PATH.exists():
        try:
            data = json.loads(RESOURCES_PATH.read_text(encoding="utf-8"))
            ds = data.get("device_serial")
            if ds and str(ds).strip().lower() != "auto":
                return str(ds).strip()
        except Exception:
            pass
    adb = os.environ.get("ANDROID_ADB_PATH", "adb")
    try:
        out = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=ROOT,
        )
    except Exception:
        return None
    for line in out.stdout.strip().splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def load_resources() -> dict | None:
    if not RESOURCES_PATH.exists():
        return None
    try:
        data = json.loads(RESOURCES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def call_tool(tool_name: str, args: dict, timeout: int = 45) -> str:
    cmd = [
        sys.executable,
        "-m",
        "phone_pilot.mcp_call",
        tool_name,
        "--args",
        json.dumps(args),
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=ROOT,
        )
        return r.stdout or ""
    except subprocess.TimeoutExpired:
        return json.dumps({"content": [{"text": json.dumps({"ok": False, "error": "timeout"})}]})
    except Exception as e:
        return json.dumps({"content": [{"text": json.dumps({"ok": False, "error": str(e)})}]})


def parse_tool_response(stdout: str) -> tuple[dict | None, str]:
    stdout = stdout.strip()
    if not stdout:
        return (None, "empty_stdout")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return (None, "stdout_not_json")
    content = data.get("content") or []
    if not content or not isinstance(content, list):
        return (None, "no_content")
    first = content[0]
    if not isinstance(first, dict):
        return (None, "content_not_dict")
    text = first.get("text")
    if not text or not isinstance(text, str):
        return (None, "no_text")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return (None, "tool_response_not_json")
    if not isinstance(obj, dict):
        return (None, "tool_response_not_dict")
    return (obj, "")


def get_b_tools_ordered(resources: dict | None) -> list[str]:
    """B 类工具按执行顺序返回：先无前置的，再需设置首页的，最后 launch+force_stop 配对。"""
    from phone_pilot.mcp.tool_contracts import get_all_contracts

    contracts = get_all_contracts()
    b_names = sorted(n for n, c in contracts.items() if c.category == "B")
    settings_pkg = (resources or {}).get("settings_package") or "com.android.settings"
    package = (resources or {}).get("package") or ""

    # 第一组：不依赖具体界面（或仅需桌面）
    first = [
        "phone_unlock",
        "phone_go_home",
        "phone_go_back",
        "phone_clear_background",
        "phone_screenshot",
        "phone_get_current_activity",
        "phone_get_device_info",
    ]
    # 第二组：需要设置首页（launch_app(settings) 已在 prep 执行）
    second = [
        "phone_launch_app",  # 先验证 launch 设置
        "phone_get_page_state",
        "phone_tap",
        "phone_swipe",
        "phone_long_press",
        "phone_find_element",
        "phone_keyevent",
        "phone_scroll_to_find",
        "phone_wait_for_element",
        "phone_smart_find",
        "phone_launch_from_home",
        "phone_checkpoint_save",
        "phone_checkpoint_diff",
        "phone_compare_screenshot",
        "phone_dismiss_popup",
        "phone_open_deeplink",
        "phone_input_text",
        "phone_find_image",
        "phone_ocr_find",
        "phone_tap_element",
    ]
    # 第三组：先 launch 再 force_stop（成对）
    third = [
        "phone_force_stop",
    ]
    # 第四组：录制等
    fourth = [
        "phone_start_recording",
        "phone_stop_recording",
        "phone_replay_recording",
        "phone_push_and_run_monkey",
    ]

    seen = set()
    ordered = []
    for name in first + second + third + fourth:
        if name in b_names and name not in seen:
            seen.add(name)
            ordered.append(name)
    for name in b_names:
        if name not in seen:
            ordered.append(name)
    return ordered


def build_b_tool_args(
    tool_name: str,
    device_serial: str,
    resources: dict | None,
    screen_center: tuple[int, int] | None,
    resource_key: str,
) -> dict:
    args: dict = {"device_serial": device_serial}
    r = resources or {}
    pkg = r.get("package") or ""
    settings_pkg = r.get("settings_package") or "com.android.settings"

    if tool_name == "phone_launch_app":
        args["package"] = settings_pkg
    elif tool_name == "phone_force_stop" and pkg:
        args["package"] = pkg
    elif tool_name in ("phone_tap", "phone_long_press") and screen_center:
        x, y = screen_center
        args["x"] = x
        args["y"] = y
    elif tool_name == "phone_swipe" and screen_center:
        x, y = screen_center
        args["x1"] = x
        args["y1"] = y
        args["x2"] = x
        args["y2"] = max(0, y - 200)
    elif tool_name == "phone_find_element":
        args["text_contains"] = "设置"
    elif tool_name == "phone_keyevent":
        args["keycode"] = "KEYCODE_BACK"
    elif tool_name == "phone_open_deeplink":
        args["uri"] = "https://www.example.com"
    elif tool_name == "phone_input_text":
        args["text"] = "test"
    elif tool_name == "phone_find_image" and resource_key:
        args["template_path"] = f"@res:{resource_key}"
    elif tool_name == "phone_ocr_find":
        args["query"] = "设置"
    elif tool_name == "phone_scroll_to_find":
        args["text"] = "设置"
    elif tool_name == "phone_wait_for_element":
        args["text"] = "设置"
    elif tool_name == "phone_smart_find":
        args["text"] = "设置"
    elif tool_name == "phone_launch_from_home":
        args["query"] = "设置"
    elif tool_name == "phone_checkpoint_save":
        args["name"] = "b_verify"
    elif tool_name == "phone_checkpoint_diff":
        args["name"] = "b_verify"
    elif tool_name == "phone_compare_screenshot":
        # 需基准图；资源清单可选 baseline_path
        baseline = (resources or {}).get("baseline_path") or ""
        if baseline:
            args["baseline_path"] = str((ROOT / baseline).resolve()) if not Path(baseline).is_absolute() else baseline
        else:
            args["baseline_path"] = ""  # 缺则服务端报错，记入失败详情
    elif tool_name == "phone_tap_element":
        args["index"] = 0
    return args


def ensure_settings_home(device_serial: str, resources: dict | None, timeout: int) -> None:
    r = resources or {}
    settings_pkg = r.get("settings_package") or "com.android.settings"
    call_tool("phone_go_home", {"device_serial": device_serial}, timeout)
    call_tool(
        "phone_launch_app",
        {"device_serial": device_serial, "package": settings_pkg},
        timeout,
    )


def main() -> int:
    import argparse

    from phone_pilot.mcp.tool_contracts import validate_response

    p = argparse.ArgumentParser(description="Verify B-class MCP tools on device.")
    p.add_argument("--device", default="", help="Device serial")
    p.add_argument("--write-checklist", action="store_true", help="Update MCP_TOOLS_VERIFICATION.md")
    p.add_argument("--write-failures", action="store_true", default=True, help="Write b_class_failures.json (default True)")
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirm")
    ns = p.parse_args()

    device_serial = (ns.device or "").strip() or get_device_serial()
    if not device_serial:
        print("ERROR: no device.", file=sys.stderr)
        return 1

    resources = load_resources()
    print("=== B 类验证（按前置条件规划顺序）===")
    print(f"Device: {device_serial}")
    if not ns.yes:
        try:
            if input("继续？ [y/N]: ").strip().lower() not in ("y", "yes"):
                return 0
        except EOFError:
            pass

    # 前置：确保有已安装应用 + 进入设置首页
    print("\n[Prep] install_app(demo), go_home, launch_app(settings)...")
    apk_path = (resources or {}).get("apk_path")
    package = (resources or {}).get("package") or ""
    settings_pkg = (resources or {}).get("settings_package") or "com.android.settings"
    if apk_path and package:
        path_abs = str((ROOT / apk_path).resolve())
        if Path(path_abs).exists():
            call_tool(
                "phone_install_app",
                {"device_serial": device_serial, "apk_path": path_abs},
                ns.timeout,
            )
    call_tool("phone_go_home", {"device_serial": device_serial}, ns.timeout)
    call_tool(
        "phone_launch_app",
        {"device_serial": device_serial, "package": settings_pkg},
        ns.timeout,
    )

    # 缓存屏幕中心（用于 tap/swipe/long_press）
    out = call_tool("phone_get_screen_size", {"device_serial": device_serial}, ns.timeout)
    resp, _ = parse_tool_response(out)
    screen_center: tuple[int, int] | None = None
    if resp and resp.get("ok") and "width" in resp and "height" in resp:
        w, h = int(resp.get("width", 0)), int(resp.get("height", 0))
        if w > 0 and h > 0:
            screen_center = (w // 2, h // 2)
    if not screen_center:
        screen_center = (540, 960)

    resource_key = (resources or {}).get("resource_key") or ""

    b_tools = get_b_tools_ordered(resources)
    print(f"\nB-class tools (ordered): {len(b_tools)}\n")

    results: list[tuple[str, str, str]] = []
    failures_detail: list[dict] = []

    for i, tool_name in enumerate(b_tools, 1):
        # force_stop 前先 launch_app(package)，再 force_stop(package)
        if tool_name == "phone_force_stop" and package:
            ensure_settings_home(device_serial, resources, ns.timeout)
            call_tool(
                "phone_launch_app",
                {"device_serial": device_serial, "package": package},
                ns.timeout,
            )
        elif tool_name in (
            "phone_tap",
            "phone_swipe",
            "phone_long_press",
            "phone_find_element",
            "phone_get_page_state",
            "phone_keyevent",
            "phone_scroll_to_find",
            "phone_wait_for_element",
            "phone_smart_find",
            "phone_checkpoint_save",
            "phone_checkpoint_diff",
            "phone_tap_element",
        ):
            ensure_settings_home(device_serial, resources, ns.timeout)

        args = build_b_tool_args(
            tool_name,
            device_serial,
            resources,
            screen_center,
            resource_key,
        )
        stdout = call_tool(tool_name, args, ns.timeout)
        response, parse_note = parse_tool_response(stdout)

        if response is None:
            passed = False
            note = parse_note or "parse_failed"
            detail = stdout[:500] if stdout else ""
        else:
            passed, missing = validate_response(tool_name, response)
            note = ",".join(missing) if missing else (response.get("error", "") if not passed else "")
            detail = json.dumps(response, ensure_ascii=False)[:800] if not passed else ""

        result = "通过" if passed else "失败"
        results.append((tool_name, result, note))
        if not passed:
            failures_detail.append({
                "tool_name": tool_name,
                "result": result,
                "note": note,
                "detail": detail,
            })
        print(f"[{i}/{len(b_tools)}] {tool_name}: {result}" + (f" ({note})" if note else ""))

    passed_count = sum(1 for _, r, _ in results if r == "通过")
    print()
    print(f"Summary: {passed_count}/{len(b_tools)} passed")

    if ns.write_failures and failures_detail:
        FAILURES_PATH.parent.mkdir(parents=True, exist_ok=True)
        FAILURES_PATH.write_text(
            json.dumps({"failures": failures_detail}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Detailed failures: {FAILURES_PATH}")

    if ns.write_checklist and CHECKLIST_PATH.exists():
        _update_checklist(results)

    return 0 if passed_count == len(b_tools) else 2


def _update_checklist(results: list[tuple[str, str, str]]) -> None:
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    result_map = {name: (res, note) for name, res, note in results}
    new_lines = []
    for line in lines:
        m = re.match(r"^\|\s*(phone_[a-z0-9_]+)\s*\|\s*B\s*\|", line)
        if m:
            name = m.group(1)
            if name in result_map:
                res, note = result_map[name]
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 6:
                    parts[4] = res
                    parts[5] = note
                    line = "| " + " | ".join(parts[1:6]) + " |"
        new_lines.append(line)
    CHECKLIST_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"Updated {CHECKLIST_PATH}")


if __name__ == "__main__":
    sys.exit(main())
