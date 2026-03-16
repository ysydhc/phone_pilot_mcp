#!/usr/bin/env python3
"""A 类 MCP 工具真机验证：从契约获取 A 类工具列表，逐工具调用 phone-pilot-call，解析返回并 validate_response，输出结果并可写回检查表。

运行前：通过 adb 获取当前连接设备作为 device_serial；需 apk_path、package、资源文件等由 docs/verification/verification_resources.json 提供，执行前会提示确认。

约定：工具列表来源为 get_all_contracts() 且 category=="A"；多设备时须在清单中指定 device_serial 或设 PHONE_PILOT_DEVICE_SERIAL。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 项目根 = 脚本所在目录的上级
ROOT = Path(__file__).resolve().parent.parent
CHECKLIST_PATH = ROOT / "docs" / "verification" / "MCP_TOOLS_VERIFICATION.md"
RESOURCES_PATH = ROOT / "docs" / "verification" / "verification_resources.json"


def get_device_serial() -> str | None:
    """从环境变量或 adb devices 取第一台 device 的 serial。"""
    serial = os.environ.get("PHONE_PILOT_DEVICE_SERIAL", "").strip()
    if serial:
        return serial
    adb = os.environ.get("ANDROID_ADB_PATH", "adb")
    try:
        out = subprocess.run(
            [adb, "devices"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=ROOT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    for line in out.stdout.strip().splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def load_resources() -> dict | None:
    """加载验证资源清单；不存在则返回 None。"""
    if not RESOURCES_PATH.exists():
        return None
    try:
        data = json.loads(RESOURCES_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def build_tool_args(
    tool_name: str,
    device_serial: str,
    resources: dict | None,
) -> dict:
    """根据工具名与资源清单构建调用参数（含 device_serial）。"""
    args: dict = {"device_serial": device_serial}
    if not resources:
        return args

    def _abs(p: str) -> str:
        if not p:
            return p
        return str((ROOT / p).resolve())

    apk_path = resources.get("apk_path")
    package = resources.get("package")
    resource_file = resources.get("resource_file")
    resource_key = resources.get("resource_key")
    push_file = resources.get("push_file") or {}
    pull_file = resources.get("pull_file") or {}

    if tool_name == "phone_install_app" and apk_path:
        args["apk_path"] = _abs(apk_path)
    elif tool_name in ("phone_clear_data", "phone_force_stop", "phone_uninstall_app", "phone_memory_snapshot") and package:
        args["package"] = package
    elif tool_name == "phone_res_add" and resource_file:
        args["path"] = _abs(resource_file)
        if resource_key:
            args["key"] = resource_key
    elif tool_name in ("phone_res_get", "phone_res_update", "phone_res_delete") and resource_key:
        args["key"] = resource_key
    elif tool_name == "phone_res_resolve" and resource_key:
        args["value"] = f"@res:{resource_key}"
    elif tool_name == "phone_push_file":
        lp = push_file.get("local_path")
        rp = push_file.get("remote_path")
        if lp is not None:
            args["local_path"] = _abs(lp)
        if rp:
            args["remote_path"] = rp
    elif tool_name == "phone_pull_file":
        rp = pull_file.get("remote_path")
        lp = pull_file.get("local_path")
        if rp:
            args["remote_path"] = rp
        if lp:
            args["local_path"] = _abs(lp)
    return args


def get_a_tools() -> list[str]:
    """从契约获取 category 为 A 的工具名列表（与生成清单一致）。"""
    from phone_pilot.mcp.tool_contracts import get_all_contracts

    contracts = get_all_contracts()
    return sorted(
        name for name, c in contracts.items() if c.category == "A"
    )


def call_tool(tool_name: str, args: dict, timeout: int = 60) -> str:
    """调用 phone-pilot-call，返回 stdout。"""
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
        return json.dumps({"ok": False, "error": "timeout"})
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def parse_tool_response(stdout: str) -> tuple[dict | None, str]:
    """从 MCP 调用 stdout 解析出工具返回的 dict（content[0].text 的 JSON）。"""
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


def run_preparation(
    device_serial: str,
    resources: dict,
    timeout: int,
) -> None:
    """运行前准备：push_file（供 pull_file）、res_add（供 res_get 等）、install_app（供 clear_data 等）。"""
    push_file = resources.get("push_file") or {}
    local_path = push_file.get("local_path")
    remote_path = push_file.get("remote_path")
    if local_path and remote_path:
        path_abs = str((ROOT / local_path).resolve())
        if Path(path_abs).exists():
            call_tool(
                "phone_push_file",
                {"device_serial": device_serial, "local_path": path_abs, "remote_path": remote_path},
                timeout=timeout,
            )
    rf = resources.get("resource_file")
    rk = resources.get("resource_key")
    if rf:
        path_abs = str((ROOT / rf).resolve())
        if Path(path_abs).exists():
            a = {"device_serial": device_serial, "path": path_abs}
            if rk:
                a["key"] = rk
            call_tool("phone_res_add", a, timeout=timeout)
    apk_path = resources.get("apk_path")
    if apk_path:
        path_abs = str((ROOT / apk_path).resolve())
        if Path(path_abs).exists():
            call_tool(
                "phone_install_app",
                {"device_serial": device_serial, "apk_path": path_abs},
                timeout=timeout,
            )


def main() -> int:
    import argparse

    from phone_pilot.mcp.tool_contracts import validate_response

    p = argparse.ArgumentParser(description="Verify A-class MCP tools on device.")
    p.add_argument(
        "--device",
        default="",
        help="Device serial (override; else from resources or adb)",
    )
    p.add_argument(
        "--resources",
        default="",
        help=f"Path to resources JSON (default: {RESOURCES_PATH.relative_to(ROOT)})",
    )
    p.add_argument(
        "--write-checklist",
        action="store_true",
        help="Update MCP_TOOLS_VERIFICATION.md 执行结果/备注 for A tools",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Per-tool timeout in seconds (default 60)",
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip pre-run confirmation prompt",
    )
    ns = p.parse_args()

    resources = load_resources()
    if ns.resources:
        rp = Path(ns.resources).expanduser().resolve()
        if rp.exists():
            resources = json.loads(rp.read_text(encoding="utf-8"))

    device_serial = (ns.device or "").strip()
    if not device_serial and resources:
        ds = resources.get("device_serial")
        if ds and str(ds).strip().lower() != "auto":
            device_serial = str(ds).strip()
    if not device_serial:
        device_serial = os.environ.get("PHONE_PILOT_DEVICE_SERIAL", "").strip()
    if not device_serial:
        device_serial = get_device_serial() or ""

    if not device_serial:
        print("ERROR: no device. Connect one device, set PHONE_PILOT_DEVICE_SERIAL, or set device_serial in verification_resources.json.", file=sys.stderr)
        return 1

    # 运行前提示
    print("=== 运行前资源确认 ===")
    print(f"  device_serial: {device_serial} (来自 adb 或清单/环境)")
    if resources:
        apk_path = resources.get("apk_path", "")
        pkg = resources.get("package", "")
        rf = resources.get("resource_file", "")
        apk_abs = (ROOT / apk_path).resolve() if apk_path else None
        rf_abs = (ROOT / rf).resolve() if rf else None
        print(f"  apk_path: {apk_path}" + (" [存在]" if (apk_abs and apk_abs.exists()) else " [不存在，需 apk_path 的工具将失败]"))
        print(f"  package: {pkg} (用于 phone_clear_data / phone_force_stop / phone_memory_snapshot / phone_uninstall_app)")
        print(f"  resource_file: {rf}" + (" [存在]" if (rf_abs and rf_abs.exists()) else " [不存在，phone_res_* 将失败]"))
        print(f"  resource_key: {resources.get('resource_key', '')}")
    else:
        print("  未加载 verification_resources.json，仅带 device_serial 调用；需 apk/package/res 的工具可能失败。")
    print()
    print("需要 apk_path 的工具将使用清单中的 apk_path；phone_clear_data 将使用清单中的 package 清除应用数据。")
    if not ns.yes:
        try:
            ans = input("确认并继续？ [y/N]: ").strip().lower()
            if ans != "y" and ans != "yes":
                print("已取消。")
                return 0
        except EOFError:
            print("非交互模式且未使用 --yes，已取消。")
            return 0

    if resources:
        run_preparation(device_serial, resources, ns.timeout)

    a_tools = get_a_tools()
    print()
    print(f"A-class tools (from contracts): {len(a_tools)}")
    print(f"Device: {device_serial}")
    print()

    results: list[tuple[str, str, str]] = []
    for i, tool_name in enumerate(a_tools, 1):
        args = build_tool_args(tool_name, device_serial, resources)
        stdout = call_tool(tool_name, args, timeout=ns.timeout)
        response, parse_note = parse_tool_response(stdout)
        if response is None:
            passed = False
            note = parse_note or "parse_failed"
        else:
            passed, missing = validate_response(tool_name, response)
            note = ",".join(missing) if missing else ""
        result = "通过" if passed else "失败"
        results.append((tool_name, result, note))
        print(f"[{i}/{len(a_tools)}] {tool_name}: {result}" + (f" ({note})" if note else ""))

    passed_count = sum(1 for _, r, _ in results if r == "通过")
    print()
    print(f"Summary: {passed_count}/{len(a_tools)} passed")

    if ns.write_checklist and CHECKLIST_PATH.exists():
        _update_checklist(results)

    return 0 if passed_count == len(a_tools) else 2


def _update_checklist(results: list[tuple[str, str, str]]) -> None:
    """在检查表中更新 A 类工具的执行结果与备注。"""
    text = CHECKLIST_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    result_map = {name: (res, note) for name, res, note in results}
    new_lines: list[str] = []
    for line in lines:
        m = re.match(r"^\|\s*(phone_[a-z0-9_]+)\s*\|\s*A\s*\|", line)
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
