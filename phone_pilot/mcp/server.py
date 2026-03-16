#!/usr/bin/env python3
"""
Unified MCP Server for Multi-Platform Mobile Automation.
统一 MCP 服务器 - 多平台移动自动化。

This module provides MCP tools with unified `phone_*` prefix,
supporting Android (and future iOS/HarmonyOS) through the Driver architecture.

Tool Naming Convention:
- phone_*: New unified tools (recommended)
- android_*: Legacy tools (deprecated, for backward compatibility)

Tool Categories:
- Page State: phone_get_page_state (LLM-optimized, recommended first call)
- Device: phone_list_devices, phone_screenshot, etc.
- Input: phone_tap, phone_swipe, phone_input_text, etc.
- UI: phone_find_element, phone_get_current_activity, etc.
- Image: phone_find_image
- OCR: phone_ocr_find
- App: phone_launch_app, phone_force_stop, etc.
- Verification: phone_verify, phone_checkpoint_save/diff, phone_run_script

Response contract (响应约定):
- 所有 phone_* 工具返回 dict。成功时必含 "ok": True；失败时必含 "ok": False 与 "error": str。
- All phone_* tools return a dict. Success: "ok": True; Failure: "ok": False and "error": str.
- 凡使用设备的工具，成功时建议包含 device_serial。
- Tools using a device should include device_serial on success.
- 契约与 validate_response 的校验对象均为该 dict（工具函数返回值），而非 MCP 协议层。
- Contracts and validate_response validate this tool-returned dict, not MCP wire format.
- 该 dict 在 MCP 中的序列化约定：JSON 形式放入 CallToolResult 的 content[0].text，由客户端反序列化得到同一 dict。
- MCP serialization: JSON in CallToolResult content[0].text, deserialized by client to the same dict.
"""

from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import re
import time
from pathlib import Path
from typing import Any, Optional

import sys


def _import_fastmcp():
    """
    Import FastMCP from the external `mcp` package.

    Note: This project has a local package `phone_pilot.mcp`, which can shadow
    the external `mcp` package on sys.path. We guard against that here.
    """
    try:
        from mcp.server import FastMCP  # type: ignore
        import mcp.server as _mcp_server  # type: ignore

        mod_path = getattr(_mcp_server, "__file__", "") or ""
        # If it resolves to our local package, fall back to site-packages search.
        if "phone_pilot/mcp" not in mod_path.replace("\\", "/"):
            return FastMCP
    except Exception:
        pass

    # Fallback: temporarily remove project paths to avoid shadowing.
    project_root = pathlib.Path(__file__).resolve().parents[1]
    original_sys_path = list(sys.path)
    try:
        cleaned = []
        for p in sys.path:
            try:
                rp = pathlib.Path(p).resolve()
            except Exception:
                cleaned.append(p)
                continue
            if rp == project_root or project_root in rp.parents:
                continue
            cleaned.append(p)
        sys.path = cleaned

        mod = sys.modules.get("mcp")
        if mod and "phone_pilot/mcp" in (getattr(mod, "__file__", "") or "").replace(
            "\\", "/"
        ):
            del sys.modules["mcp"]

        from mcp.server import FastMCP as ExternalFastMCP  # type: ignore

        return ExternalFastMCP
    finally:
        sys.path = original_sys_path


FastMCP = _import_fastmcp()

from phone_pilot.core.protocols import DeviceDriver  # noqa: E402
from phone_pilot.core.skills import DeviceSkills  # noqa: E402
from phone_pilot.core.resource import (  # noqa: E402
    res_add,
    res_update,
    res_delete,
    res_get,
    res_list,
    res_resolve,
    resolve_or_cache_path,
)
from phone_pilot.android.driver import AndroidDriver  # noqa: E402
from phone_pilot.android.device.utils import (  # noqa: E402
    execute_shell,
    get_clipboard_text,
    get_device_model_and_version,
    get_notifications,
    set_airplane_mode,
    set_wifi_enabled,
)
from phone_pilot.harmony.driver import HarmonyDriver  # noqa: E402
from phone_pilot.script_api.context import ScriptContext, RetryExhausted  # noqa: E402
from phone_pilot.script_api.runner import scroll_to_find  # noqa: E402
from phone_pilot.script_api.popup_guard import PopupGuard  # noqa: E402
from phone_pilot.script_api.find import find_text  # noqa: E402
from phone_pilot.script_api.actions import launch_from_home  # noqa: E402

# Create MCP server instance
mcp = FastMCP("phone_pilot")

# 按设备保存录屏远程/本地路径，stop 时拉取并清理
# Per-device recording paths for pull and cleanup on stop
_recording_state: dict[str, dict] = {}

# 按设备保存当前 logcat 输出路径 / Per-device logcat output path
_logcat_state: dict[str, str] = {}


# =============================================================================
# Driver Factory
# =============================================================================


def get_driver(device_serial: str, platform: str = "android") -> DeviceDriver:
    """
    Get a device driver instance based on platform.

    Args:
        device_serial: Device serial/identifier
        platform: Platform type ("android", "ios", "harmony")

    Returns:
        DeviceDriver instance
    """
    platform = (platform or "android").strip().lower()

    if platform == "android":
        return AndroidDriver(device_serial)
    if platform == "ios":
        raise NotImplementedError("iOS support is planned for a future release")
    if platform == "harmony":
        return HarmonyDriver(device_serial)
    raise ValueError(f"Unknown platform: {platform}")


def get_skills(device_serial: str, platform: str = "android") -> DeviceSkills:
    """Get a DeviceSkills instance for the given device."""
    driver = get_driver(device_serial, platform)
    return DeviceSkills(driver)


def _make_script_context(device_serial: str, platform: str) -> ScriptContext:
    """构造仅含 device 的 ScriptContext，不创建 RunSession。
    Build minimal ScriptContext for MCP reuse without RunSession.

    仅设置 device_serial 和 platform，其他观测/自愈均关闭，
    适用于 MCP 单次调用场景。
    Only sets device_serial and platform, disables observations,
    suitable for stateless MCP single-call usage.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        ScriptContext: 最小化上下文 / Minimal context
    """
    return ScriptContext(
        device_serial=device_serial,
        platform=platform,
        auto_log=False,
        auto_screenshot=False,
        auto_dump_hprof=False,
        auto_meminfo=False,
        auto_report=False,
        popup_guard=False,
    )


def _resolve_device_serial(
    device_serial: Optional[str], platform: str = "auto"
) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """
    Resolve device_serial and platform from adb/hdc.
    Returns: (resolved_serial, resolved_platform, error_dict_or_none)
    """
    platform = (platform or "auto").strip().lower()
    android_serials: list[str] = []
    harmony_serials: list[str] = []

    # Scan Android devices (adb)
    if platform in ("android", "auto"):
        try:
            from phone_pilot.android.adb.utils import adb_executable
            from phone_pilot.android.adb.runner import CommandRunner
            from phone_pilot.android.adb.parsers import parse_adb_devices

            proc = CommandRunner.run([adb_executable(), "devices", "-l"], check=False)
            devices = parse_adb_devices(proc.stdout or "")
            for d in devices:
                if not d:
                    continue
                state = d.get("status") or d.get("state")
                if state == "device":
                    serial = d.get("serial")
                    if serial:
                        android_serials.append(serial)
        except Exception:
            pass

    # Scan Harmony devices (hdc)
    if platform in ("harmony", "auto"):
        try:
            from phone_pilot.harmony.hdc.utils import hdc_executable
            from phone_pilot.harmony.hdc.runner import HdcCommandRunner

            proc_h = HdcCommandRunner.run(
                [hdc_executable(), "list", "targets"], check=False, log_output=False
            )
            for raw in (proc_h.stdout or "").splitlines():
                line = raw.strip()
                if not line or "empty" in line.lower():
                    continue
                serial = line.split()[0]
                if serial:
                    harmony_serials.append(serial)
        except Exception:
            pass

    if device_serial:
        # Check if it's an Android device
        if device_serial in android_serials:
            return device_serial, "android", None
        # Check if it's a Harmony device
        if device_serial in harmony_serials:
            return device_serial, "harmony", None
        return (
            None,
            None,
            {
                "ok": False,
                "error": "device_not_found",
                "device_serial": device_serial,
                "note": "设备未找到。请确认设备已连接且授权调试。",
                "available_android": android_serials,
                "available_harmony": harmony_serials,
            },
        )

    # Auto-select: prefer the specified platform, then any available
    if platform == "android" and android_serials:
        return android_serials[0], "android", None
    if platform == "harmony" and harmony_serials:
        return harmony_serials[0], "harmony", None
    if android_serials:
        return android_serials[0], "android", None
    if harmony_serials:
        return harmony_serials[0], "harmony", None

    return (
        None,
        None,
        {
            "ok": False,
            "error": "no_device_connected",
            "note": "未发现可用设备。请连接 Android 或 HarmonyOS 设备。",
        },
    )


# =============================================================================
# Device Management Tools
# =============================================================================


@mcp.tool()
async def phone_list_devices() -> dict:
    """列出已连接的设备。
    List connected devices.

    Parameters / 参数:
        (无)

    Returns / 返回值:
        dict: {"ok": True, "devices": [{"serial", "status", "platform", "description"}]}
        devices 包含 Android 和 HarmonyOS 设备 / devices includes Android and HarmonyOS
    """
    from phone_pilot.android.adb.utils import adb_executable
    from phone_pilot.android.adb.runner import CommandRunner
    from phone_pilot.android.adb.parsers import parse_adb_devices

    result = []

    # Android devices (adb)
    proc = CommandRunner.run([adb_executable(), "devices", "-l"], check=False)
    devices = parse_adb_devices(proc.stdout or "")
    for d in devices:
        result.append(
            {
                "serial": d.get("serial"),
                "status": d.get("status"),
                "platform": "android",
                "description": d.get("description"),
            }
        )

    # HarmonyOS devices (hdc) - best effort
    try:
        from phone_pilot.harmony.hdc.utils import hdc_executable
        from phone_pilot.harmony.hdc.runner import HdcCommandRunner

        proc_h = HdcCommandRunner.run(
            [hdc_executable(), "list", "targets"], check=False, log_output=False
        )
        for raw in (proc_h.stdout or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if "List" in line or "target" in line.lower():
                continue
            if line.strip().lower() == "[empty]" or "empty" in line.lower():
                continue
            serial = line.split()[0]
            if serial:
                # Try to get richer device info via hmdriver2
                device_info = None
                try:
                    from phone_pilot.harmony.hmdriver_bridge import get_hmdriver

                    hm = get_hmdriver(serial)
                    info = hm.device_info
                    device_info = {
                        "model": info.model,
                        "productName": info.productName,
                        "sdkVersion": info.sdkVersion,
                    }
                except Exception:
                    pass

                entry = {
                    "serial": serial,
                    "status": "device",
                    "platform": "harmony",
                    "description": device_info.get("model") if device_info else None,
                }
                if device_info:
                    entry["device_info"] = device_info
                result.append(entry)
    except Exception:
        pass

    return {"ok": True, "devices": result}


@mcp.tool()
async def phone_build_device_profile(
    device_serial: str,
    platform: str = "auto",
    out_dir: str = "./.recordings",
    keep_device: Optional[str] = None,
    include_system: bool = True,
) -> dict:
    """构建设备信息并持久化（JSON + SQLite）。
    Build and persist device profile (JSON + SQLite).

    Parameters / 参数:
        device_serial: 设备序列号，可空时自动检测 / Device serial, auto-detect if empty
        platform: "android" | "harmony" | "auto" / 平台类型
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings
        keep_device: (Android) 保留指定设备信息 / Keep specified device info
        include_system: (Android) 是否包含系统应用 / Include system apps

    Returns / 返回值:
        dict: {"ok": True, ...} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 设备信息保存到 out_dir/devices/ 目录
        - Android 支持 keep_device、include_system；HarmonyOS 不支持
    """
    if not device_serial:
        return {"ok": False, "error": "device_serial is required"}

    # Detect platform if auto
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial, platform
    )
    if err:
        return err
    plat = resolved_platform or "android"

    try:
        if plat == "harmony":
            from phone_pilot.harmony.device.store import capture_device_profile

            return capture_device_profile(resolved_serial, out_dir=out_dir)
        else:
            from phone_pilot.android.device.store import capture_device_profile

            return capture_device_profile(
                resolved_serial,
                out_dir=out_dir,
                keep_device=keep_device,
                include_system=include_system,
            )
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


def _md_escape(text: Optional[str]) -> str:
    s = "" if text is None else str(text)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def _fmt_value(val: Optional[object]) -> str:
    if val is None:
        return "N/A"
    s = str(val).strip()
    return s if s else "N/A"


def _short_err(val: Optional[object], max_len: int = 140) -> str:
    s = _fmt_value(val)
    if s == "N/A":
        return s
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip() + "..."


def _app_field(app: dict, *keys: str) -> Optional[object]:
    for k in keys:
        if k in app:
            return app.get(k)
    return None


def _render_device_markdown(
    device: dict,
    apps: list[dict],
    launch_profiles: list[dict],
    *,
    apps_limit: int = 0,
) -> str:
    did = device.get("device_id")
    serial = device.get("device_serial")
    lines: list[str] = []
    lines.append("# Device Info")
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Device ID: `{_fmt_value(did)}`")
    lines.append(f"- Device Serial: `{_fmt_value(serial)}`")
    lines.append(f"- Display Name: {_fmt_value(device.get('display_name'))}")
    lines.append(f"- Model: {_fmt_value(device.get('model'))}")
    lines.append(f"- Brand: {_fmt_value(device.get('brand'))}")
    lines.append(f"- Manufacturer: {_fmt_value(device.get('manufacturer'))}")
    lines.append(f"- Device: {_fmt_value(device.get('device'))}")
    lines.append(f"- Product: {_fmt_value(device.get('product'))}")
    lines.append(f"- Android Release: {_fmt_value(device.get('android_release'))}")
    lines.append(f"- SDK Int: {_fmt_value(device.get('sdk_int'))}")
    lines.append(f"- Security Patch: {_fmt_value(device.get('security_patch'))}")
    lines.append(f"- Captured At: {_fmt_value(device.get('captured_at'))}")
    lines.append(f"- Updated At: {_fmt_value(device.get('updated_at'))}")
    lines.append("")
    lines.append("## Screen & Touch")
    lines.append(
        f"- Screen: {_fmt_value(device.get('screen_w'))}x{_fmt_value(device.get('screen_h'))}"
    )
    lines.append(f"- Density DPI: {_fmt_value(device.get('density_dpi'))}")
    lines.append(
        f"- Touch Abs Max: {_fmt_value(device.get('abs_max_x'))} x {_fmt_value(device.get('abs_max_y'))}"
    )
    lines.append("")
    lines.append("## Launcher")
    lines.append(f"- Package: `{_fmt_value(device.get('launcher_package'))}`")
    lines.append(f"- Version Name: {_fmt_value(device.get('launcher_version_name'))}")
    lines.append(f"- Version Code: {_fmt_value(device.get('launcher_version_code'))}")
    lines.append("")
    lines.append(f"## Apps ({len(apps)})")
    if apps:
        lines.append(
            "| AppName | Package | VersionCode | VersionName | IconPath | IconSource | IconError |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        subset = apps if apps_limit <= 0 else apps[: int(apps_limit)]
        for app in subset:
            name = _fmt_value(_app_field(app, "app_name", "AppName"))
            pkg = _fmt_value(_app_field(app, "package_name", "PackageName"))
            vcode = _fmt_value(_app_field(app, "version_code", "VersionCode"))
            vname = _fmt_value(_app_field(app, "version_name", "VersionName"))
            icon = _fmt_value(_app_field(app, "icon_path", "IconPath"))
            icon_src = _fmt_value(_app_field(app, "icon_source", "IconSource"))
            icon_err = _short_err(_app_field(app, "icon_error", "IconError"))
            lines.append(
                f"| {_md_escape(name)} | `{_md_escape(pkg)}` | {_md_escape(str(vcode))} | {_md_escape(str(vname))} | `{_md_escape(icon)}` | {_md_escape(str(icon_src))} | {_md_escape(str(icon_err))} |"
            )
        if apps_limit > 0 and len(apps) > apps_limit:
            lines.append("")
            lines.append(f"_Only first {apps_limit} apps shown._")
    else:
        lines.append("_No apps found._")
    lines.append("")
    lines.append(f"## Launch Profiles ({len(launch_profiles)})")
    if launch_profiles:
        lines.append("| Package | Activity | Component | ADB Cmd | Source |")
        lines.append("| --- | --- | --- | --- | --- |")
        for lp in launch_profiles:
            pkg = _fmt_value(lp.get("package_name") or lp.get("package"))
            act = _fmt_value(lp.get("activity"))
            comp = _fmt_value(lp.get("component"))
            cmd = _fmt_value(lp.get("adb_cmd"))
            src = _fmt_value(lp.get("source"))
            lines.append(
                f"| `{_md_escape(pkg)}` | {_md_escape(act)} | `{_md_escape(comp)}` | `{_md_escape(cmd)}` | {_md_escape(src)} |"
            )
    else:
        lines.append("_No launch profiles found._")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


@mcp.tool()
async def phone_export_device_info_md(
    device_id: Optional[str] = None,
    device_serial: Optional[str] = None,
    out_dir: str = "./.recordings",
    md_path: Optional[str] = None,
    apps_limit: int = 0,
) -> dict:
    """将设备信息导出为 Markdown 文件。
    Export device info to a Markdown file.

    Parameters / 参数:
        device_id: 设备 ID（与 device_serial 二选一） / Device id (one of device_id/device_serial)
        device_serial: 设备序列号 / Device serial
        out_dir: 录制根目录，默认 ./.recordings / Recordings root, default ./.recordings
        md_path: 输出路径，未指定时用 out_dir/devices/device_info_{id}.md
        apps_limit: 应用列表数量限制，0=全部 / App list limit, 0=all

    Returns / 返回值:
        dict: {"ok": True, "md_path": str, "device_id": str, "device_serial": str}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 需先调用 phone_build_device_profile 生成设备快照
        - 输出到 out_dir/devices/ 目录
    """
    if not device_id and not device_serial:
        return {"ok": False, "error": "device_id or device_serial is required"}

    from phone_pilot.extensions.device_db import load_device_snapshot
    from phone_pilot.android.device.store import get_device_profile_from_store

    from phone_pilot.core.storage import recordings_root

    out_root = recordings_root(out_dir)
    snapshot = load_device_snapshot(
        str(out_root), device_id=device_id, device_serial=device_serial
    )

    if snapshot.get("ok"):
        device = snapshot.get("device") or {}
        apps = list(snapshot.get("apps") or [])
        launch_profiles = list(snapshot.get("launch_profiles") or [])
    else:
        prof = get_device_profile_from_store(
            device_id or device_serial or "", out_dir=str(out_root)
        )
        if not (isinstance(prof, dict) and prof.get("ok")):
            return {"ok": False, "error": "device_not_found", "detail": snapshot}
        p = prof.get("profile") or {}
        os_info = (p.get("os") or {}) if isinstance(p.get("os"), dict) else {}
        screen = (p.get("screen") or {}) if isinstance(p.get("screen"), dict) else {}
        touch = (p.get("touch") or {}) if isinstance(p.get("touch"), dict) else {}
        launcher = (
            (p.get("launcher") or {}) if isinstance(p.get("launcher"), dict) else {}
        )
        device = {
            "device_id": p.get("device_id"),
            "device_serial": p.get("device_serial"),
            "display_name": p.get("display_name"),
            "market_name": p.get("market_name"),
            "model": p.get("model"),
            "brand": p.get("brand"),
            "manufacturer": p.get("manufacturer"),
            "device": p.get("device"),
            "product": p.get("product"),
            "adb": p.get("adb"),
            "android_release": os_info.get("android_release"),
            "sdk_int": os_info.get("sdk_int"),
            "security_patch": os_info.get("security_patch"),
            "screen_w": screen.get("w"),
            "screen_h": screen.get("h"),
            "density_dpi": screen.get("density_dpi"),
            "abs_max_x": touch.get("abs_max_x"),
            "abs_max_y": touch.get("abs_max_y"),
            "launcher_package": launcher.get("package"),
            "launcher_version_name": launcher.get("version_name"),
            "launcher_version_code": launcher.get("version_code"),
            "captured_at": p.get("captured_at"),
            "updated_at": p.get("updated_at"),
            "ts": p.get("ts"),
        }
        apps = list(
            ((p.get("apps") or {}) if isinstance(p.get("apps"), dict) else {}).get(
                "apps"
            )
            or []
        )
        launch_profiles = _load_launch_profiles_from_json(
            out_root, device_serial or device_id or ""
        )

    md = _render_device_markdown(
        device, apps, launch_profiles, apps_limit=int(apps_limit or 0)
    )
    if not md_path:
        did = device.get("device_id") or device_id or device_serial or "device"
        md_path = str(out_root / "devices" / f"device_info_{did}.md")
    md_file = pathlib.Path(md_path).expanduser().resolve()
    legacy = (
        (out_root / f"device_info_{device_serial or device_id}.md")
        if (device_serial or device_id)
        else None
    )
    if legacy and legacy.exists() and not md_file.exists():
        try:
            md_file.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(md_file)
        except Exception:
            pass
    md_file.parent.mkdir(parents=True, exist_ok=True)
    md_file.write_text(md, encoding="utf-8")
    return {
        "ok": True,
        "md_path": str(md_file),
        "device_id": device.get("device_id"),
        "device_serial": device.get("device_serial"),
    }


def _load_launch_profiles_from_json(
    out_root: pathlib.Path, device_serial: str
) -> list[dict[str, Any]]:
    idx = out_root / "launch_profiles" / "index.json"
    legacy = out_root / "launch_profiles" / "index.jsonl"
    from phone_pilot.core.storage import migrate_jsonl_to_json, read_json

    migrate_jsonl_to_json(idx, legacy)
    if not idx.exists():
        return []
    latest: dict[str, dict] = {}
    try:
        data = read_json(idx)
    except Exception:
        return []
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        records = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("device_serial") != device_serial:
            continue
        pkg = rec.get("package")
        if not pkg:
            continue
        prev = latest.get(pkg, {})
        if (rec.get("ts") or 0) >= (prev.get("ts") or 0):
            latest[pkg] = {
                "package": pkg,
                "activity": rec.get("activity"),
                "component": rec.get("component"),
                "adb_cmd": rec.get("adb_cmd"),
                "source": "launch_profiles_index",
            }
    return list(latest.values())


@mcp.tool()
async def phone_screenshot(
    device_serial: str,
    platform: str = "android",
    save_path: Optional[str] = None,
) -> dict:
    """截取设备屏幕截图。
    Take a screenshot of the device screen.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial number
        platform: 平台类型 "android" | "harmony" / Platform type
        save_path: 可选保存路径；不指定则返回 base64 编码 / Optional save path; else returns base64

    Returns / 返回值:
        dict: save_path 有值时 {"ok", "screenshot_path"}；无值时 {"ok", "screenshot_base64"}
        / Returns path when save_path given, base64 otherwise

    Notes / 特殊逻辑:
        - 指定 save_path 时截图写入该路径，目录不存在会自动创建
        - 未指定时返回 base64 字符串供 Agent 直接使用
    """
    try:
        driver = get_driver(device_serial, platform)
        png_bytes = driver.screen.screenshot()

        if save_path:
            path = pathlib.Path(save_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png_bytes)
            return {
                "ok": True,
                "device_serial": device_serial,
                "platform": platform,
                "screenshot_path": str(path),
            }
        else:
            return {
                "ok": True,
                "device_serial": device_serial,
                "platform": platform,
                "screenshot_base64": base64.b64encode(png_bytes).decode("ascii"),
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_get_screen_size(
    device_serial: str,
    platform: str = "android",
) -> dict:
    """获取屏幕尺寸。
    Get screen dimensions.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "width": int, "height": int, "device_serial", "platform"}
        或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        width, height = driver.screen.get_screen_size()
        return {
            "ok": True,
            "device_serial": device_serial,
            "platform": platform,
            "width": width,
            "height": height,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_start_recording(
    device_serial: str = "",
    name: str = "rec",
    out_dir: str = "./.recordings",
    platform: str = "auto",
) -> dict:
    """开始录屏。
    Start screen recording on device.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        name: 录屏文件名前缀 / Recording file name prefix
        out_dir: 输出根目录 / Output root directory
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "local_path": str, "device_serial": str}
        或 {"ok": False, "error": "already_recording"} / {"ok": False, "error": str}
    """
    try:
        resolved_serial, resolved_platform, err = _resolve_device_serial(
            device_serial or None, platform
        )
        if err:
            return err

        if resolved_serial in _recording_state:
            return {"ok": False, "error": "already_recording"}

        ts = int(time.time())
        local_path = Path(out_dir) / "recordings" / f"{name}_{ts}.mp4"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        remote_filename = f"phone_pilot_rec_{ts}.mp4"
        driver = get_driver(resolved_serial, resolved_platform or "android")
        result = driver.screen.start_screenrecord(path=remote_filename)

        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error", "start_screenrecord_failed"),
            }

        # Android 内部拼 /sdcard/{path}；Harmony 返回 remote_path
        remote_path = result.get("remote_path") or f"/sdcard/{remote_filename}"
        _recording_state[resolved_serial] = {
            "remote_path": remote_path,
            "local_path": str(local_path),
        }

        return {
            "ok": True,
            "local_path": str(local_path),
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_stop_recording(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """停止录屏并拉取文件到本地。
    Stop screen recording and pull file to local.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "local_path": str}
        或 {"ok": False, "error": "not_recording"} / {"ok": False, "error": str}
    """
    try:
        resolved_serial, resolved_platform, err = _resolve_device_serial(
            device_serial or None, platform
        )
        if err:
            return err

        state = _recording_state.pop(resolved_serial, None)
        if not state:
            return {"ok": False, "error": "not_recording"}

        driver = get_driver(resolved_serial, resolved_platform or "android")
        driver.screen.stop_screenrecord()
        driver.pull_file(state["remote_path"], state["local_path"])
        driver.remove_remote_file(state["remote_path"])

        return {
            "ok": True,
            "local_path": state["local_path"],
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Navigation & Device Management Tools
# =============================================================================


@mcp.tool()
async def phone_go_home(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """返回桌面。
    Navigate to home screen.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        # 调用协议方法返回桌面 / Call protocol method to go home
        result = driver.go_home()
        result["platform"] = plat
        result["device_serial"] = resolved
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_go_back(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """返回上一页。
    Press back button.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        # 调用协议方法返回上一页 / Call protocol method to go back
        result = driver.go_back()
        result["platform"] = plat
        result["device_serial"] = resolved
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_unlock(
    device_serial: str = "",
    pin: str = "",
    platform: str = "auto",
) -> dict:
    """解锁设备屏幕。
    Wake and unlock device screen.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        pin: 解锁 PIN 码，空则仅唤醒并滑动解锁 / PIN code, empty for swipe unlock only
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        # 唤醒并解锁 / Wake and unlock
        result = driver.unlock(pin=pin or None)
        result["platform"] = plat
        result["device_serial"] = resolved
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_clear_background(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """清除后台应用进程。
    Clear background apps.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "platform": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        # 清除后台应用 / Clear background apps
        result = driver.clear_background()
        result["platform"] = plat
        result["device_serial"] = resolved
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_clear_data(
    package: str,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """清除应用数据。
    Clear application data.

    Parameters / 参数:
        package: 应用包名 / Package name
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "package": str, "platform": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        # 清除指定应用数据 / Clear data for the specified app
        result = driver.app.clear_data(package)
        result["platform"] = plat
        result["device_serial"] = resolved
        result["package"] = package
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_open_deeplink(
    uri: str,
    package: str = "",
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """通过深链接打开页面。
    Open a page via deeplink/scheme URI.

    Parameters / 参数:
        uri: 深链接地址 / Deeplink URI (e.g. "myapp://page/detail")
        package: 可选目标应用包名 / Optional target package name
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "uri": str, "platform": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        # 通过协议方法打开深链接 / Open deeplink via protocol method
        result = driver.open_deeplink(uri, package=package or None)
        result["platform"] = plat
        result["device_serial"] = resolved
        result["uri"] = uri
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Input Tools
# =============================================================================


@mcp.tool()
async def phone_tap(
    device_serial: str,
    x: int,
    y: int,
    platform: str = "android",
    wait_s: float = 0.15,
) -> dict:
    """点击屏幕坐标。
    Tap at screen coordinates.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        x: X 坐标（像素） / X coordinate in pixels
        y: Y 坐标（像素） / Y coordinate in pixels
        platform: 平台类型 / Platform type
        wait_s: 点击后等待秒数，默认 0.15 / Wait seconds after tap, default 0.15

    Returns / 返回值:
        dict: {"ok": True, "x", "y", "platform"} 或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.input.tap(x, y, wait_s=wait_s)
        result["platform"] = platform
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_swipe(
    device_serial: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    platform: str = "android",
    duration_ms: int = 300,
) -> dict:
    """从 (x1, y1) 滑动到 (x2, y2)。
    Swipe from start coordinates to end coordinates.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        x1, y1: 起始坐标（像素） / Start coordinates in pixels
        x2, y2: 结束坐标（像素） / End coordinates in pixels
        platform: 平台类型 / Platform type
        duration_ms: 滑动持续时间（毫秒），默认 300 / Swipe duration ms, default 300

    Returns / 返回值:
        dict: {"ok": True, "x1", "y1", "x2", "y2", "platform"} 或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.input.swipe(x1, y1, x2, y2, duration_ms=duration_ms)
        result["platform"] = platform
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_long_press(
    device_serial: str,
    x: int,
    y: int,
    platform: str = "android",
    duration_ms: int = 500,
) -> dict:
    """在指定坐标长按。
    Long press at coordinates.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        x, y: 坐标（像素） / Coordinates in pixels
        platform: 平台类型 / Platform type
        duration_ms: 按压持续时间（毫秒），默认 500 / Press duration ms, default 500

    Returns / 返回值:
        dict: {"ok": True, "x", "y", "platform"} 或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.input.long_press(x, y, duration_ms=duration_ms)
        result["platform"] = platform
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_input_text(
    device_serial: str,
    text: str,
    platform: str = "android",
    enter: bool = False,
) -> dict:
    """在焦点输入框中输入文本。
    Input text into the focused input field.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        text: 要输入的文本 / Text to input
        platform: 平台类型 / Platform type
        enter: 输入后是否按回车，默认 False / Press Enter after input, default False

    Returns / 返回值:
        dict: {"ok": True, "text", "platform"} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 需先使目标输入框获得焦点（如先点击）
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.input.input_text(text, enter=enter)
        result["platform"] = platform
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_keyevent(
    device_serial: str,
    keycode: str,
    platform: str = "android",
) -> dict:
    """发送按键事件。
    Send a key event.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        keycode: 按键代码，如 KEYCODE_HOME / Key code (e.g. KEYCODE_HOME)
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "key", "platform"} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 常用: KEYCODE_HOME(主页), KEYCODE_BACK(返回), KEYCODE_ENTER(确认),
          KEYCODE_VOLUME_UP/DOWN(音量)
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.input.keyevent(keycode)
        result["platform"] = platform
        result["key"] = result.get("key") or result.get("keycode") or keycode
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# UI Tools
# =============================================================================


@mcp.tool()
async def phone_find_element(
    device_serial: str,
    platform: str = "android",
    text: Optional[str] = None,
    text_contains: Optional[str] = None,
    desc: Optional[str] = None,
    desc_contains: Optional[str] = None,
    resource_id: Optional[str] = None,
    class_name: Optional[str] = None,
    clickable: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> dict:
    """查找匹配条件的 UI 元素。
    Find UI elements matching UIAutomator criteria.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type
        text: 精确文本匹配 / Exact text match
        text_contains: 包含文本匹配 / Partial text match
        desc: content-desc 精确匹配 / Content description exact match
        desc_contains: content-desc 包含匹配 / Content description partial match
        resource_id: 资源 ID 匹配 / Resource ID match
        class_name: 类名匹配 / Class name match
        clickable: 是否可点击 / Is clickable
        enabled: 是否启用 / Is enabled

    Returns / 返回值:
        dict: {"ok": True, "count": int, "elements": [{text, bounds, center_x, center_y, ...}]}
        或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)

        selector = {}
        if text is not None:
            selector["text"] = text
        if text_contains is not None:
            selector["text_contains"] = text_contains
        if desc is not None:
            selector["desc"] = desc
        if desc_contains is not None:
            selector["desc_contains"] = desc_contains
        if resource_id is not None:
            selector["resource_id"] = resource_id
        if class_name is not None:
            selector["class_name"] = class_name
        if clickable is not None:
            selector["clickable"] = clickable
        if enabled is not None:
            selector["enabled"] = enabled

        elements = driver.ui.find_elements(selector)

        return {
            "ok": True,
            "device_serial": device_serial,
            "platform": platform,
            "count": len(elements),
            "elements": elements,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_get_current_activity(
    device_serial: str,
    platform: str = "android",
) -> dict:
    """获取当前前台 Activity 信息。
    Get current foreground activity info.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "package", "activity", "device_serial", "platform"}
        或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.ui.get_current_activity()
        return {
            "ok": True,
            "device_serial": device_serial,
            "platform": platform,
            **result,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Image Tools
# =============================================================================


@mcp.tool()
async def phone_find_image(
    device_serial: str,
    template_path: str,
    platform: str = "android",
    threshold: float = 0.85,
    grayscale: bool = True,
    method: str = "auto",
    max_results: int = 5,
) -> dict:
    """在屏幕上通过图像匹配查找模板位置。
    Find template image on screen via template matching.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        template_path: 模板图片路径，支持 @res:key 引用 / Template path or @res:key
        platform: 平台类型 / Platform type
        threshold: 匹配阈值 0.0-1.0，默认 0.85 / Match threshold, default 0.85
        grayscale: 是否转灰度，默认 True / Convert to grayscale, default True
        method: 匹配方法 auto/ccoeff_normed/edge 等 / Matching method
        max_results: 最大返回结果数，默认 5 / Max matches returned, default 5

    Returns / 返回值:
        dict: {"ok": True, "count", "matches": [{x, y, w, h, score, center_x, center_y}]}
        或 {"ok": False, "error": str}；resource_not_found 表示模板路径无效

    Notes / 特殊逻辑:
        - template_path 可为本地路径或 @res:key（需先用 phone_res_add 缓存）
    """
    try:
        try:
            template_path = resolve_or_cache_path(template_path)
        except Exception as e:
            return {
                "ok": False,
                "error": "resource_not_found",
                "detail": str(e),
                "template_path": template_path,
            }
        skills = get_skills(device_serial, platform)
        result = skills.find_image_on_screen(
            template_path,
            threshold=threshold,
            grayscale=grayscale,
            method=method,
            max_results=max_results,
        )
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["template_path"] = template_path
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# OCR Tools
# =============================================================================


@mcp.tool()
async def phone_ocr_find(
    device_serial: str,
    query: str,
    platform: str = "android",
    lang: str = "eng",
    exact: bool = False,
    case_sensitive: bool = False,
    limit: int = 10,
) -> dict:
    """使用 OCR 在屏幕上查找文本。
    Find text on screen using OCR (Tesseract).

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        query: 要搜索的文本 / Text to search for
        platform: 平台类型 / Platform type
        lang: Tesseract 语言代码，如 eng、chi_sim / Tesseract lang code
        exact: 是否精确匹配 / Require exact match
        case_sensitive: 是否区分大小写 / Case-sensitive matching
        limit: 最大返回匹配数，默认 10 / Max matches, default 10

    Returns / 返回值:
        dict: {"ok": True, "boxes_count", "matches_count", "matches": [{text, x, y, w, h, center_x, center_y}]}
        或 {"ok": False, "error": str}
    """
    try:
        skills = get_skills(device_serial, platform)
        result = skills.find_text_on_screen(
            query,
            lang=lang,
            exact=exact,
            case_sensitive=case_sensitive,
            limit=limit,
        )
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["query"] = query
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# App Management Tools
# =============================================================================


@mcp.tool()
async def phone_launch_app(
    device_serial: str,
    package: str,
    platform: str = "android",
    activity: Optional[str] = None,
) -> dict:
    """启动应用程序。
    Launch an application.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        package: 应用包名 / Package name
        platform: 平台类型 / Platform type
        activity: 可选，指定启动 Activity / Optional Activity to launch

    Returns / 返回值:
        dict: {"ok": True, "package", "device_serial", "platform"} 或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.app.launch_app(package, activity)
        result["device_serial"] = device_serial
        result["platform"] = platform
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_force_stop(
    device_serial: str,
    package: str,
    platform: str = "android",
) -> dict:
    """强制停止应用程序。
    Force stop an application.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        package: 应用包名 / Package name
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "package", "device_serial", "platform"} 或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        result = driver.app.force_stop(package)
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["package"] = result.get("package") or result.get("PackageName") or package
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_list_packages(
    device_serial: str,
    platform: str = "android",
    include_system: bool = False,
) -> dict:
    """列出已安装的应用包。
    List installed packages.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type
        include_system: 是否包含系统应用，默认 False / Include system packages, default False

    Returns / 返回值:
        dict: {"ok": True, "count", "packages": [str], "device_serial", "platform"}
        或 {"ok": False, "error": str}
    """
    try:
        driver = get_driver(device_serial, platform)
        packages = driver.app.list_packages(include_system=include_system)
        return {
            "ok": True,
            "device_serial": device_serial,
            "platform": platform,
            "count": len(packages),
            "packages": packages,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Screenshot Comparison Tools
# =============================================================================


@mcp.tool()
async def phone_compare_screenshot(
    device_serial: str,
    baseline_path: str,
    platform: str = "android",
    threshold: float = 0.95,
) -> dict:
    """将当前屏幕与基准截图进行相似度比较。
    Compare current screen with a baseline screenshot.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        baseline_path: 基准图片路径，支持 @res:key / Baseline path or @res:key
        platform: 平台类型 / Platform type
        threshold: 相似度阈值 0.0-1.0，默认 0.95 / Similarity threshold, default 0.95

    Returns / 返回值:
        dict: {"ok": True, "match": bool, "similarity": float, "threshold"}
        或 {"ok": False, "error": str}
    """
    try:
        try:
            baseline_path = resolve_or_cache_path(baseline_path)
        except Exception as e:
            return {
                "ok": False,
                "error": "resource_not_found",
                "detail": str(e),
                "baseline_path": baseline_path,
            }
        skills = get_skills(device_serial, platform)
        baseline_bytes = pathlib.Path(baseline_path).read_bytes()

        result = skills.compare_screenshot(
            baseline_bytes,
            threshold=threshold,
        )
        result["device_serial"] = device_serial
        result["platform"] = platform
        result["baseline_path"] = baseline_path
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# =============================================================================
# Resource Cache Tools
# =============================================================================


@mcp.tool()
async def phone_res_add(
    path: str,
    key: Optional[str] = None,
    out_dir: str = "./.recordings",
    meta: Optional[dict] = None,
) -> dict:
    """添加资源到缓存与索引。
    Add a resource (image) to cache and index.

    Parameters / 参数:
        path: 本地文件路径 / Local file path
        key: 缓存键，未指定则用文件名蛇形命名 / Cache key, auto from filename if omitted
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings
        meta: 可选元数据 / Optional metadata dict

    Returns / 返回值:
        dict: {"ok": True, "record": {key, origin_path, cached_path, ...}}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 资源复制到 out_dir/cache/pic/ 目录
        - 可通过 @res:key 在其他工具中引用
    """
    try:
        return res_add(path, key=key, out_dir=out_dir, meta=meta)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_update(
    key: str,
    path: Optional[str] = None,
    new_key: Optional[str] = None,
    out_dir: str = "./.recordings",
    meta: Optional[dict] = None,
) -> dict:
    """更新缓存与索引中的资源。
    Update a cached resource.

    Parameters / 参数:
        key: 已有资源的缓存键 / Existing resource key
        path: 可选，新源文件路径以替换 / Optional new source path to replace
        new_key: 可选，重命名键 / Optional new key name
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings
        meta: 可选，更新元数据 / Optional metadata

    Returns / 返回值:
        dict: {"ok": True, "record": {...}} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 资源仍位于 out_dir/cache/pic/ 目录
    """
    try:
        return res_update(key, path=path, new_key=new_key, out_dir=out_dir, meta=meta)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_delete(
    key: str,
    out_dir: str = "./.recordings",
    delete_file: bool = True,
) -> dict:
    """从缓存与索引删除资源。
    Delete a resource from cache and index.

    Parameters / 参数:
        key: 缓存键 / Cache key
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings
        delete_file: 是否删除缓存文件，默认 True / Delete cached file, default True

    Returns / 返回值:
        dict: {"ok": True, "deleted": {...}} 或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 索引位于 out_dir/cache/pic/index.json，文件在 out_dir/cache/pic/
    """
    try:
        return res_delete(key, out_dir=out_dir, delete_file=delete_file)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_get(
    key: str,
    out_dir: str = "./.recordings",
) -> dict:
    """查询缓存资源记录。
    Get a cached resource record.

    Parameters / 参数:
        key: 缓存键 / Cache key
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings

    Returns / 返回值:
        dict: {"ok": True, "record": {key, origin_path, cached_path, ...}}
        或 {"ok": False, "error": "resource_not_found", "key": str}
    """
    try:
        return res_get(key, out_dir=out_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_list(
    out_dir: str = "./.recordings",
) -> dict:
    """列出缓存资源。
    List cached resources.

    Parameters / 参数:
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings

    Returns / 返回值:
        dict: {"ok": True, "records": [...], "count": int}

    Notes / 特殊逻辑:
        - 从 out_dir/cache/pic/index.json 读取索引
    """
    try:
        return res_list(out_dir=out_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_res_resolve(
    value: str,
    out_dir: str = "./.recordings",
) -> dict:
    """解析 @res:key 到缓存文件路径。
    Resolve @res:key to cached file path.

    Parameters / 参数:
        value: 原始值，可为 @res:key 或普通路径 / Value (e.g. @res:my_image)
        out_dir: 输出根目录，默认 ./.recordings / Output root, default ./.recordings

    Returns / 返回值:
        dict: {"ok": True, "value", "resolved": str, "cached": bool}
        非 @res: 引用时 resolved=value, cached=False
    """
    try:
        return res_resolve(value, out_dir=out_dir)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_recordings_list(
    out_dir: str = "./.recordings",
    limit: int = 50,
) -> dict:
    """
    List recorded workflows and touch recordings.
    列出录制的工作流和触摸录制。
    """
    from phone_pilot.core.storage import recordings_root

    out_root = recordings_root(out_dir)

    recordings = []

    # List workflow recordings
    workflows_dir = out_root / "workflows"
    if workflows_dir.exists():
        for d in sorted(workflows_dir.iterdir(), reverse=True):
            if d.is_dir():
                meta_path = d / "meta.json"
                recordings.append(
                    {
                        "type": "workflow",
                        "name": d.name,
                        "path": str(d),
                        "has_meta": meta_path.exists(),
                    }
                )
                if len(recordings) >= limit:
                    break

    return {
        "ok": True,
        "recordings": recordings[:limit],
        "count": len(recordings),
        "out_dir": str(out_root),
    }


@mcp.tool()
async def phone_recordings_get(
    recording_key: str,
    out_dir: str = "./.recordings",
) -> dict:
    """
    Get recording details by key.
    根据键获取录制详情。
    """
    from phone_pilot.core.storage import load_recording_by_key

    return await asyncio.to_thread(
        load_recording_by_key,
        recording_key,
        out_dir,
    )


@mcp.tool()
async def phone_replay_recording(
    recording_key: str,
    device_serial: Optional[str] = None,
    out_dir: str = "./.recordings",
    speed: float = 1.0,
) -> dict:
    """
    Replay a recorded touch sequence.
    重放录制的触摸序列。

    Args:
        recording_key: Recording key/name / 录制键/名称
        device_serial: Device serial / 设备序列号
        out_dir: Recordings directory / 录制目录
        speed: Playback speed multiplier / 播放速度倍数
    """
    from phone_pilot.core.storage import load_recording_by_key
    from phone_pilot.android.recording.touch import push_script
    from phone_pilot.android.touch.monkey import MonkeyRunner

    rec = load_recording_by_key(recording_key, out_dir)
    if not rec.get("ok"):
        return rec

    mks_data = rec.get("mks_data")
    if not mks_data:
        return {"ok": False, "error": "no_mks_data", "recording_key": recording_key}

    if not device_serial:
        return {"ok": False, "error": "device_serial_required"}

    # Scale timing by speed
    if speed != 1.0:
        for event in mks_data:
            if "t" in event:
                event["t"] = int(event["t"] / speed)

    # Push and run
    result = push_script(device_serial, mks_data)
    if not result.get("ok"):
        return result

    remote_path = result.get("remote_path")
    runner = MonkeyRunner(device_serial)
    run_result = runner.run_script(remote_path)

    return {
        "ok": run_result.get("ok", False),
        "device_serial": device_serial,
        "recording_key": recording_key,
        "speed": speed,
        "run_result": run_result,
    }


@mcp.tool()
async def phone_push_and_run_monkey(
    device_serial: str,
    script_path: str,
) -> dict:
    """
    Push and run a Monkey script on device.
    推送并运行 Monkey 脚本。
    """
    from phone_pilot.android.touch.monkey import MonkeyRunner

    p = pathlib.Path(script_path).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": "script_not_found", "script_path": str(p)}

    runner = MonkeyRunner(device_serial)
    result = runner.push_and_run(str(p))

    return {
        "ok": result.get("ok", False),
        "device_serial": device_serial,
        "script_path": str(p),
        **result,
    }


# =============================================================================
# Verification & Pipeline Tools (验证管道工具)
# =============================================================================


@mcp.tool()
async def phone_run_script(
    path: str,
    device_serial: str = "",
    platform: str = "auto",
    timeout_s: float = 120.0,
    repo_path: str = "",
    change_description: str = "",
) -> dict:
    """
    Execute a Python workflow script that uses script_api + chain.
    执行一个 Python 工作流脚本（使用 script_api + chain），返回结构化结果。

    The script must define a `main(ctx: ScriptContext) -> dict | None` function.
    脚本需定义 `main(ctx: ScriptContext) -> dict | None` 函数。

    Args:
        path: Python script file path. 脚本文件路径。
        device_serial: Device serial (empty = auto-detect). 设备序列号。
        platform: Platform ("android"|"harmony"|"auto"). 平台。
        timeout_s: Execution timeout in seconds. 执行超时秒数。
        repo_path: Git repo path for code change tracking. 被测项目 git 仓库路径。
        change_description: Optional change description from agent. Agent 变更说明。

    Returns:
        {ok, result, stdout, elapsed_ms, code_changes?, error?}
    """
    import importlib.util
    import io
    import time as _time
    import contextlib

    from phone_pilot.script_api import ScriptContext

    script_path = pathlib.Path(path).expanduser().resolve()
    if not script_path.exists():
        return {"ok": False, "error": f"script not found: {path}"}

    # Resolve device
    resolved = _resolve_device_serial(device_serial or None, platform)
    serial, plat, err = resolved
    if err:
        return {"ok": False, "error": err}

    ctx = ScriptContext(
        device_serial=serial or "",
        platform=plat or "auto",
    )

    # Dynamic import
    spec = importlib.util.spec_from_file_location("_workflow_script", str(script_path))
    if spec is None or spec.loader is None:
        return {"ok": False, "error": f"cannot load script: {path}"}

    module = importlib.util.module_from_spec(spec)

    # Capture stdout
    stdout_buf = io.StringIO()
    result = None
    error_msg = None

    t0 = _time.monotonic()
    try:
        spec.loader.exec_module(module)

        main_fn = getattr(module, "main", None)
        if main_fn is None:
            return {"ok": False, "error": "script has no main(ctx) function"}

        with contextlib.redirect_stdout(stdout_buf):
            result = main_fn(ctx)

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
    finally:
        elapsed_ms = (_time.monotonic() - t0) * 1000

    # Code changes tracking
    code_changes = None
    if repo_path:
        from phone_pilot.core.code_changes import capture_code_changes

        code_changes = capture_code_changes(
            repo_path=repo_path,
            description=change_description,
        )

    response: dict = {
        "ok": error_msg is None,
        "result": result,
        "stdout": stdout_buf.getvalue(),
        "elapsed_ms": round(elapsed_ms, 1),
    }
    if error_msg:
        response["error"] = error_msg
    if code_changes:
        response["code_changes"] = code_changes

    return response


@mcp.tool()
async def phone_verify(
    device_serial: str,
    assertions: str,
    platform: str = "auto",
    evidence_dir: str = "",
    repo_path: str = "",
    change_description: str = "",
) -> dict:
    """在当前屏幕上批量执行多维度验证断言。
    Run batch verification assertions on the current screen.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        assertions: 断言列表的 JSON 字符串 / JSON array of assertions
        platform: "android" | "harmony" | "auto" / Platform type
        evidence_dir: 证据保存目录，默认 recordings / Evidence output dir
        repo_path: Git 仓库路径，用于 code_changes 追踪 / Git repo for change tracking
        change_description: Agent 变更说明 / Change description

    Returns / 返回值:
        dict: {"ok", "passed", "failed", "total", "results": [...], "evidence_dir", "code_changes"?}

    Notes / 特殊逻辑:
        - 断言类型: element_exists, element_not_exists, text_equals, text_contains,
          screen_contains_text, screenshot_match; activity_equals, app_foreground;
          logcat_contains, logcat_not_contains; memory_snapshot, memory_no_growth; elapsed_under
        - 文本参数支持 re: 前缀表示正则
    """
    # Parse assertions from JSON string
    try:
        assertion_list = json.loads(assertions)
        if not isinstance(assertion_list, list):
            return {"ok": False, "error": "assertions must be a JSON array"}
    except (json.JSONDecodeError, TypeError) as e:
        return {"ok": False, "error": f"invalid assertions JSON: {e}"}

    resolved = _resolve_device_serial(device_serial or None, platform)
    serial, plat, err = resolved
    if err:
        return {"ok": False, "error": err}

    driver = get_driver(serial, plat or "android")

    from phone_pilot.core.verify import run_assertions

    result = run_assertions(
        driver,
        assertion_list,
        evidence_dir=evidence_dir or None,
    )

    # Code changes tracking
    if repo_path:
        from phone_pilot.core.code_changes import capture_code_changes

        ev_dir = result.get("evidence_dir", "")
        diff_save = str(pathlib.Path(ev_dir) / "code_changes.diff") if ev_dir else None
        result["code_changes"] = capture_code_changes(
            repo_path=repo_path,
            description=change_description,
            save_diff_to=diff_save,
        )

    return result


@mcp.tool()
async def phone_checkpoint_save(
    device_serial: str,
    name: str,
    platform: str = "auto",
) -> dict:
    """保存当前屏幕完整状态作为命名检查点。
    Save current screen state as a named checkpoint.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        name: 检查点名称，用于后续 diff / Checkpoint name for later diff
        platform: "android" | "harmony" | "auto" / Platform type

    Returns / 返回值:
        dict: {"ok": True, "name", "path", "screenshot_path", "ui_texts": [], "activity"}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 保存内容：截图、UI 可见文本、当前 Activity、元信息
        - 存储到 .recordings/checkpoints/ 目录
    """
    resolved = _resolve_device_serial(device_serial or None, platform)
    serial, plat, err = resolved
    if err:
        return {"ok": False, "error": err}

    driver = get_driver(serial, plat or "android")

    from phone_pilot.core.checkpoint import save_checkpoint

    return save_checkpoint(driver, name)


@mcp.tool()
async def phone_checkpoint_diff(
    device_serial: str,
    name: str,
    platform: str = "auto",
) -> dict:
    """对比当前屏幕与已保存检查点的差异。
    Compare current screen with a previously saved checkpoint.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        name: 要对比的检查点名称 / Checkpoint name to compare against
        platform: "android" | "harmony" | "auto" / Platform type

    Returns / 返回值:
        dict: {"ok": True, "similarity", "text_added": [], "text_removed": [],
               "activity_changed", "diff_screenshot_path"}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 检查点由 phone_checkpoint_save 保存，位于 .recordings/checkpoints/
    """
    resolved = _resolve_device_serial(device_serial or None, platform)
    serial, plat, err = resolved
    if err:
        return {"ok": False, "error": err}

    driver = get_driver(serial, plat or "android")

    from phone_pilot.core.checkpoint import diff_checkpoint

    return diff_checkpoint(driver, name)


# =============================================================================
# Logcat Tools
# =============================================================================


@mcp.tool()
async def phone_start_logcat(
    device_serial: str = "",
    output_path: str = "",
    tags: str = "",
    exclude_tags: str = "",
    level: str = "",
    process: str = "",
    out_dir: str = "./.recordings",
    platform: str = "auto",
) -> dict:
    """开始后台日志采集。
    Start background logcat/hilog capture.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        output_path: 日志输出路径，空则自动生成 / Log output path, auto-generate if empty
        tags: 逗号分隔的 tag 过滤 / Comma-separated tag filters
        exclude_tags: 排除的 tag / Tags to exclude
        level: 最低日志级别 (D/I/W/E/F) / Minimum log level
        process: 按进程名或 PID 过滤 / Filter by process name or PID
        out_dir: 输出根目录 / Output root directory
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "path": str, "pid": int}
        或 {"ok": False, "error": "already_capturing"} / {"ok": False, "error": str}
    """
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial or None, platform
    )
    if err:
        return err

    # 检查是否已在采集 / Check if already capturing
    if resolved_serial in _logcat_state:
        return {
            "ok": False,
            "error": "already_capturing",
            "device_serial": resolved_serial,
        }

    if not output_path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_root = Path(out_dir).expanduser().resolve()
        output_path = str(out_root / "logcat" / f"{ts}_{resolved_serial}.log")
        out_root.joinpath("logcat").mkdir(parents=True, exist_ok=True)

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)
        res = driver.start_log_capture(
            output_path,
            tags=tags,
            exclude_tags=exclude_tags,
            level=level,
            process=process,
        )
        if not res.get("ok"):
            return {
                "ok": False,
                "error": res.get("error", "unknown"),
                "device_serial": resolved_serial,
            }

        _logcat_state[resolved_serial] = output_path
        return {
            "ok": True,
            "path": output_path,
            "pid": res.get("pid"),
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


@mcp.tool()
async def phone_stop_logcat(
    device_serial: str = "",
    output_path: str = "",
    platform: str = "auto",
) -> dict:
    """停止日志采集。
    Stop logcat/hilog capture.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        output_path: 日志输出路径，空则从状态获取 / Log path, auto-retrieve from state if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "lines": int, ...}
        或 {"ok": False, "error": "no_logcat_capture"} / {"ok": False, "error": str}
    """
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial or None, platform
    )
    if err:
        return err

    path = output_path or _logcat_state.pop(resolved_serial, "")
    if not path:
        return {
            "ok": False,
            "error": "no_logcat_capture",
            "device_serial": resolved_serial,
        }

    # 若通过 output_path 指定，也需从状态中移除 / Remove from state when output_path provided
    _logcat_state.pop(resolved_serial, None)

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)
        result = driver.stop_log_capture(path)
        result["device_serial"] = resolved_serial
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


@mcp.tool()
async def phone_search_logcat(
    device_serial: str = "",
    pattern: str = "",
    lines: int = 5000,
    regex: bool = False,
    platform: str = "auto",
) -> dict:
    """搜索设备日志。
    Search device logcat for pattern.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        pattern: 搜索关键词或正则 / Search keyword or regex pattern
        lines: 读取最近行数 / Number of recent lines to read
        regex: 是否使用正则匹配 / Use regex matching
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "matches": [str], "count": int}
    """
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial or None, platform
    )
    if err:
        return err

    try:
        plat = resolved_platform or "android"
        driver = get_driver(resolved_serial, plat)
        log_text = driver.read_log(lines=lines)

        matches: list[str] = []
        for line in log_text.splitlines():
            if regex:
                if re.search(pattern, line):
                    matches.append(line)
            else:
                if pattern in line:
                    matches.append(line)

        return {
            "ok": True,
            "matches": matches,
            "count": len(matches),
            "device_serial": resolved_serial,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved_serial}


# =============================================================================
# Page Interaction Tools (scroll / wait / dismiss)
# =============================================================================


@mcp.tool()
async def phone_scroll_to_find(
    text: str,
    device_serial: str = "",
    direction: str = "up_down",
    max_count: int = 10,
    use_ocr: bool = False,
    exact: bool = False,
    platform: str = "auto",
) -> dict:
    """滚动查找页面中的文本元素。
    Scroll to find a text element on the page.

    Parameters / 参数:
        text: 要查找的文本 / Text to find
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        direction: 滚动方向 "up_down"/"left_right" / Scroll direction
        max_count: 单方向最大滚动次数 / Max scrolls per direction
        use_ocr: 是否使用 OCR 识别 / Use OCR for text recognition
        exact: 精确匹配文本 / Exact text match
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "center": [x, y], "bounds": [...], "text": str}
        或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        ctx = _make_script_context(resolved, plat)
        elem = scroll_to_find(
            ctx,
            text,
            direction=direction,
            max_count=max_count,
            use_ocr=use_ocr,
            exact=exact,
        )
        if elem is None:
            return {
                "ok": False,
                "error": f"text '{text}' not found after scrolling",
                "device_serial": resolved,
            }
        center = elem.center() if hasattr(elem, "center") and callable(elem.center) else None
        bounds = elem.bounds() if hasattr(elem, "bounds") and callable(elem.bounds) else None
        display_text = getattr(elem, "display_text", None) or getattr(elem, "text", str(elem))
        return {
            "ok": True,
            "center": list(center) if center else None,
            "bounds": list(bounds) if bounds else None,
            "text": display_text,
            "device_serial": resolved,
            "platform": plat,
        }
    except RetryExhausted as e:
        return {
            "ok": False,
            "error": str(e),
            "device_serial": resolved,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_wait_for_element(
    text: str = "",
    resource_id: str = "",
    timeout_s: float = 10.0,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """等待元素出现在页面上。
    Wait for an element to appear on the page.

    Parameters / 参数:
        text: 等待出现的文本 / Text to wait for
        resource_id: 等待出现的资源 ID / Resource ID to wait for
        timeout_s: 超时秒数，默认 10 / Timeout in seconds, default 10
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "found": True, "element": {...}} 如果找到
        dict: {"ok": True, "found": False} 如果超时未找到
        dict: {"ok": False, "error": str} 如果出错
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                nodes = driver.ui.dump_ui_nodes()
            except Exception:
                time.sleep(0.8)
                continue
            for node in nodes:
                node_text = getattr(node, "text", "") or ""
                node_desc = getattr(node, "content_desc", "") or ""
                node_rid = getattr(node, "resource_id", "") or ""
                if text and (text in node_text or text in node_desc):
                    bounds = node.bounds_tuple() if hasattr(node, "bounds_tuple") and callable(node.bounds_tuple) else None
                    center = node.center() if hasattr(node, "center") and callable(node.center) else None
                    cx, cy = center if center else (0, 0)
                    return {
                        "ok": True,
                        "found": True,
                        "element": {
                            "text": node_text,
                            "content_desc": node_desc,
                            "resource_id": node_rid,
                            "center": [cx, cy],
                            "bounds": list(bounds) if bounds else None,
                        },
                        "device_serial": resolved,
                        "platform": plat,
                    }
                if resource_id and resource_id in node_rid:
                    bounds = node.bounds_tuple() if hasattr(node, "bounds_tuple") and callable(node.bounds_tuple) else None
                    center = node.center() if hasattr(node, "center") and callable(node.center) else None
                    cx, cy = center if center else (0, 0)
                    return {
                        "ok": True,
                        "found": True,
                        "element": {
                            "text": node_text,
                            "content_desc": node_desc,
                            "resource_id": node_rid,
                            "center": [cx, cy],
                            "bounds": list(bounds) if bounds else None,
                        },
                        "device_serial": resolved,
                        "platform": plat,
                    }
            time.sleep(0.8)
        return {"ok": True, "found": False, "device_serial": resolved, "platform": plat}
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_dismiss_popup(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """检测并关闭弹窗。
    Detect and dismiss popup dialogs.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "dismissed": True, "detail": {...}} 如果关闭了弹窗
        dict: {"ok": True, "dismissed": False} 如果无弹窗
        dict: {"ok": False, "error": str} 如果出错
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        ctx = _make_script_context(resolved, plat)
        guard = PopupGuard(ctx)
        detail = guard.check_and_dismiss()
        return {
            "ok": True,
            "dismissed": detail is not None,
            "detail": detail or {},
            "device_serial": resolved,
            "platform": plat,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_smart_find(
    text: str,
    device_serial: str = "",
    use_ocr: bool = False,
    exact: bool = False,
    try_dismiss_popup: bool = True,
    platform: str = "auto",
) -> dict:
    """智能查找：UIA + OCR + 弹窗重试。
    Smart find: UIA, OCR, and popup retry.

    Parameters / 参数:
        text: 要查找的文本 / Text to search for
        device_serial: 设备序列号 / Device serial
        use_ocr: 是否使用 OCR / Use OCR recognition
        exact: 精确匹配 / Exact match
        try_dismiss_popup: 首次未找到时尝试关闭弹窗再查找 / Dismiss popup and retry if not found
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "center": [x,y], "bounds": [...], "text": str}
        或 {"ok": False, "error": "not_found"}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        ctx = _make_script_context(resolved, plat)
        elem = find_text(ctx, text, use_ocr=use_ocr, exact=exact, retry_attempts=1)
        # 首次未找到且启用弹窗重试时，尝试关闭弹窗再查找 / Retry after dismiss if not found
        if elem is None and try_dismiss_popup:
            PopupGuard(ctx).check_and_dismiss()
            elem = find_text(ctx, text, use_ocr=use_ocr, exact=exact, retry_attempts=1)
        if elem:
            center = elem.center() if hasattr(elem, "center") and callable(elem.center) else None
            bounds = elem.bounds() if hasattr(elem, "bounds") and callable(elem.bounds) else None
            display_text = getattr(elem, "display_text", None) or getattr(elem, "text", str(elem))
            return {
                "ok": True,
                "center": list(center) if center else None,
                "bounds": list(bounds) if bounds else None,
                "text": display_text,
                "device_serial": resolved,
                "platform": plat,
            }
        return {"ok": False, "error": "not_found", "device_serial": resolved}
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_launch_from_home(
    query: str,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """从桌面搜索并启动应用。
    Search and launch app from home screen.

    Parameters / 参数:
        query: 应用名称关键词 / App name keyword to search
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, ...} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        ctx = _make_script_context(resolved, plat)
        return launch_from_home(ctx, query)
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_install_app(
    apk_path: str,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """安装 APK 到设备。
    Install APK on device.

    Parameters / 参数:
        apk_path: APK 文件本地路径 / Local APK file path
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "apk_path": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    # 仅支持 Android / Android only
    if plat != "android":
        return {"ok": False, "error": "install_app only supports Android", "platform": plat}
    apk = Path(apk_path).expanduser().resolve()
    if not apk.exists():
        return {"ok": False, "error": f"apk not found: {apk_path}"}
    try:
        from phone_pilot.android.touch.agent import install_apk

        install_apk(resolved, apk)
        return {"ok": True, "apk_path": str(apk), "device_serial": resolved}
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_uninstall_app(
    package: str,
    device_serial: str = "",
    keep_data: bool = False,
    platform: str = "auto",
) -> dict:
    """卸载应用。
    Uninstall an application.

    Parameters / 参数:
        package: 应用包名 / Package name
        device_serial: 设备序列号 / Device serial
        keep_data: 是否保留数据 / Keep app data
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "package": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat)
        result = driver.app.uninstall(package, keep_data=keep_data)
        result["device_serial"] = resolved
        result["package"] = package
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_memory_snapshot(
    package: str,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """获取应用内存快照。
    Capture application memory snapshot.

    Parameters / 参数:
        package: 应用包名 / Package name
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "summary": {...}, ...} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat != "android":
        return {"ok": False, "error": "memory_snapshot only supports Android", "platform": plat}
    try:
        from phone_pilot.memory_analyze.meminfo import capture_meminfo

        return capture_meminfo(resolved, package)
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_memory_check_leak(
    package: str,
    device_serial: str = "",
    gc_wait_s: float = 5.0,
    save_hprof: bool = False,
    platform: str = "auto",
) -> dict:
    """Activity 泄漏检测。
    Check for Activity memory leaks.

    Parameters / 参数:
        package: 应用包名 / Package name
        device_serial: 设备序列号 / Device serial
        gc_wait_s: GC 后等待时间秒 / Wait after GC in seconds
        save_hprof: 泄漏确认后是否 dump hprof / Dump hprof if leak confirmed
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "leaked": bool, "detail": {...}} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat != "android":
        return {"ok": False, "error": "memory_check_leak only supports Android", "platform": plat}
    try:
        from phone_pilot.memory_analyze.meminfo import check_activity_leak

        hprof_dir = str(Path("./.recordings").expanduser().resolve() / "meminfo")
        return check_activity_leak(
            resolved, package, gc_wait_s=gc_wait_s, save_hprof=save_hprof, hprof_out_dir=hprof_dir
        )
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_pull_file(
    remote_path: str,
    local_path: str = "",
    device_serial: str = "",
    out_dir: str = "./.recordings",
    platform: str = "auto",
) -> dict:
    """从设备拉取文件到本地。
    Pull a file from device to local.

    Parameters / 参数:
        remote_path: 设备上文件路径 / Remote file path on device
        local_path: 本地保存路径，空则自动生成 / Local save path, auto-generate if empty
        device_serial: 设备序列号 / Device serial
        out_dir: 输出根目录 / Output root directory
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "local_path": str, "remote_path": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        if local_path:
            local = Path(local_path).expanduser().resolve()
        else:
            local = Path(out_dir).expanduser().resolve() / "pulled" / Path(remote_path).name
        local.parent.mkdir(parents=True, exist_ok=True)
        driver = get_driver(resolved, plat)
        result = driver.pull_file(remote_path, str(local))
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("stderr") or result.get("error", "pull_failed"),
                "device_serial": resolved,
            }
        return {
            "ok": True,
            "local_path": str(local),
            "remote_path": remote_path,
            "device_serial": resolved,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


@mcp.tool()
async def phone_push_file(
    local_path: str,
    remote_path: str,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """推送本地文件到设备。
    Push a local file to device.

    Parameters / 参数:
        local_path: 本地文件路径 / Local file path
        remote_path: 设备目标路径 / Remote destination path on device
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "local_path": str, "remote_path": str} 或 {"ok": False, "error": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        local = Path(local_path).expanduser().resolve()
        if not local.exists():
            return {"ok": False, "error": f"local file not found: {local_path}", "device_serial": resolved}
        driver = get_driver(resolved, plat)
        result = driver.push_file(str(local), remote_path)
        result["device_serial"] = resolved
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


# =============================================================================
# P2 MCP Tools - clipboard, notifications, wifi, airplane, shell, device info
# =============================================================================


@mcp.tool()
async def phone_read_clipboard(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """读取设备剪贴板文本。
    Read device clipboard text.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "text": str} 或 {"ok": False, "error": str}
    仅支持 Android / Android only.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat != "android":
        return {"ok": False, "error": "phone_read_clipboard only supports Android", "platform": plat}
    result = await asyncio.to_thread(get_clipboard_text, resolved)
    result["device_serial"] = resolved
    return result


@mcp.tool()
async def phone_get_notifications(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """获取通知栏通知列表。
    Get notification list from notification bar.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "notifications": [{"package": str, "title": str, "text": str}]}
    仅支持 Android / Android only.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat != "android":
        return {"ok": False, "error": "phone_get_notifications only supports Android", "platform": plat}
    result = await asyncio.to_thread(get_notifications, resolved)
    result["device_serial"] = resolved
    return result


@mcp.tool()
async def phone_toggle_wifi(
    enabled: bool = True,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """开关 WiFi。
    Toggle WiFi on/off.

    Parameters / 参数:
        enabled: True 开启 / False 关闭 / True to enable, False to disable
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "wifi_on": bool} 或 {"ok": False, "error": str}
    仅支持 Android / Android only.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat != "android":
        return {"ok": False, "error": "phone_toggle_wifi only supports Android", "platform": plat}
    result = await asyncio.to_thread(set_wifi_enabled, resolved, enabled)
    result["device_serial"] = resolved
    return result


@mcp.tool()
async def phone_toggle_airplane(
    enabled: bool = True,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """开关飞行模式。
    Toggle airplane mode.

    Parameters / 参数:
        enabled: True 开启 / False 关闭 / True to enable, False to disable
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "airplane_on": bool} 或 {"ok": False, "error": str}
    仅支持 Android / Android only.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat != "android":
        return {"ok": False, "error": "phone_toggle_airplane only supports Android", "platform": plat}
    result = await asyncio.to_thread(set_airplane_mode, resolved, enabled)
    result["device_serial"] = resolved
    return result


@mcp.tool()
async def phone_execute_shell(
    command: str,
    device_serial: str = "",
    timeout_s: float = 30.0,
    platform: str = "auto",
) -> dict:
    """执行受限 shell 命令（白名单限制）。
    Execute restricted shell command (whitelist limited).

    Parameters / 参数:
        command: shell 命令 / Shell command
        device_serial: 设备序列号 / Device serial
        timeout_s: 超时秒数 / Timeout in seconds
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "stdout": str, "stderr": str, "returncode": int}
        或 {"ok": False, "error": str}
    仅支持 Android / Android only.
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    if plat != "android":
        return {"ok": False, "error": "phone_execute_shell only supports Android", "platform": plat}
    result = await asyncio.to_thread(execute_shell, resolved, command, timeout_s)
    result["device_serial"] = resolved
    return result


@mcp.tool()
async def phone_get_device_info(
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """获取轻量设备信息。
    Get lightweight device info.

    Parameters / 参数:
        device_serial: 设备序列号 / Device serial
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "screen": {"width": int, "height": int}, "activity": {...}, "model": str, "os_version": str}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err
    try:
        driver = get_driver(resolved, plat or "android")
        # 获取 screen_size、current_activity / Get screen_size, current_activity
        try:
            width, height = driver.screen.get_screen_size()
            screen = {"width": width, "height": height}
        except Exception:
            screen = {"width": 0, "height": 0}
        try:
            activity = driver.ui.get_current_activity()
        except Exception:
            activity = {"package": None, "activity": None}
        # 从 getprop 获取 model、os_version / Get model, os_version from getprop (Android)
        model = ""
        os_version = ""
        if plat == "android":
            model, os_version = await asyncio.to_thread(get_device_model_and_version, resolved)
        return {
            "ok": True,
            "screen": screen,
            "activity": activity,
            "model": model,
            "os_version": os_version,
            "device_serial": resolved,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": resolved}


# =============================================================================
# Page State Tool (LLM-Optimized)
# =============================================================================


def _classify_element(class_name: str | None) -> str:
    """Classify a UI element into a human-readable type for LLM consumption."""
    cn = (class_name or "").lower()
    if "edittext" in cn or "textfield" in cn:
        return "input"
    if "checkbox" in cn or "switch" in cn or "togglebutton" in cn:
        return "toggle"
    if "button" in cn or "imagebutton" in cn:
        return "button"
    if "radiobutton" in cn:
        return "radio"
    if "imageview" in cn:
        return "image"
    if "textview" in cn:
        return "text"
    if "recyclerview" in cn or "listview" in cn or "gridview" in cn:
        return "list"
    if "scrollview" in cn or "horizontalscrollview" in cn:
        return "scroll_container"
    if "viewpager" in cn:
        return "pager"
    if "webview" in cn:
        return "webview"
    if "tabwidget" in cn or "tablayout" in cn:
        return "tab"
    if "progressbar" in cn or "seekbar" in cn:
        return "progress"
    if "spinner" in cn:
        return "dropdown"
    return "view"


def _build_element_label(node) -> str:
    """Build a concise display label for a UI node."""
    parts = []
    if node.text:
        parts.append(node.text)
    if node.content_desc and node.content_desc != node.text:
        parts.append(f"[{node.content_desc}]")
    if node.hint and node.hint != node.text:
        parts.append(f"(hint: {node.hint})")
    return " ".join(parts) if parts else ""


def _collect_page_elements(
    driver: DeviceDriver,
    *,
    include_invisible: bool = False,
) -> tuple[list[dict], list[dict], list[str]]:
    """从 UI 层级中收集交互元素、可滚动区域和全部可见文本。
    Collect interactive elements, scrollable areas and all visible texts from UI hierarchy.

    提取自 phone_get_page_state 的核心逻辑，供多个 MCP 工具复用
    （phone_get_page_state、phone_tap_element 等）。
    Core logic extracted from phone_get_page_state for reuse across MCP tools.

    Args:
        driver: DeviceDriver 实例 / DeviceDriver instance
        include_invisible: 包含不可见元素 / Include invisible elements

    Returns:
        (elements, scrollable_areas, all_texts)
        - elements: 已按视觉位置排序并重新编号的元素列表
        - scrollable_areas: 可滚动区域
        - all_texts: 页面全部可见文本
    """
    # Dump UI hierarchy
    nodes = driver.ui.dump_ui_nodes()

    # Collect all visible texts
    from phone_pilot.core.ui_node import collect_ui_texts

    all_texts = collect_ui_texts(nodes)

    # Build interactive elements list
    elements: list[dict] = []
    scrollable_areas: list[dict] = []

    for node in nodes:
        # Detect scrollable areas
        if node.scrollable:
            b = node.bounds_tuple()
            if b:
                x1, y1, x2, y2 = b
                w_b = max(1, x2 - x1)
                h_b = max(1, y2 - y1)
                direction = "horizontal" if w_b > h_b * 1.2 else "vertical"
                scrollable_areas.append(
                    {
                        "bounds": list(b),
                        "direction": direction,
                        "class": node.class_name or "",
                    }
                )

        # Filter elements: keep those with text/desc, or interactive
        has_text = bool(node.text or node.content_desc or node.hint)
        is_interactive = bool(
            node.clickable
            or node.long_clickable
            or node.checkable
            or node.focusable
            or node.scrollable
        )
        is_input = "edittext" in (node.class_name or "").lower()

        if (
            not include_invisible
            and not has_text
            and not is_interactive
            and not is_input
        ):
            continue

        # Skip pure container nodes with no useful info
        if not has_text and not is_interactive and not is_input:
            cn = (node.class_name or "").lower()
            if any(
                skip in cn
                for skip in [
                    "framelayout",
                    "linearlayout",
                    "relativelayout",
                    "constraintlayout",
                    "coordinatorlayout",
                    "viewgroup",
                ]
            ):
                continue

        center = node.center()
        bounds = node.bounds_tuple()
        if not center:
            continue  # Skip nodes without position

        # Simplify resource_id (remove package prefix)
        rid = node.resource_id or ""
        if ":id/" in rid:
            rid = rid.split(":id/", 1)[1]

        label = _build_element_label(node)
        elem_type = _classify_element(node.class_name)

        elements.append(
            {
                "index": len(elements),
                "type": elem_type,
                "label": label,
                "center": list(center),
                "bounds": list(bounds) if bounds else None,
                "clickable": bool(node.clickable),
                "scrollable": bool(node.scrollable),
                "resource_id": rid,
            }
        )

    # Sort elements by visual position (top-to-bottom, left-to-right)
    elements.sort(key=lambda e: (e["center"][1], e["center"][0]))
    # Re-index after sorting
    for i, elem in enumerate(elements):
        elem["index"] = i

    return elements, scrollable_areas, all_texts


@mcp.tool()
async def phone_get_page_state(
    device_serial: str = "",
    platform: str = "auto",
    include_invisible: bool = False,
    include_screenshot: bool = False,
    annotate_elements: bool = False,
) -> dict:
    """获取当前页面完整状态快照（LLM 最优交互工具）。
    Get complete page state snapshot in one call — optimized for LLM decision-making.

    Parameters / 参数:
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: "android" | "harmony" | "auto" / Platform type
        include_invisible: 是否包含不可见元素，默认 False / Include invisible elements
        include_screenshot: 是否返回 base64 截图，默认 False / Include base64 screenshot
        annotate_elements: 是否返回元素标注截图，默认 False / Include annotated screenshot with element boxes and indices

    Returns / 返回值:
        dict: {
            "ok": True,
            "activity": {"package": str, "activity": str},
            "screen": {"width": int, "height": int},
            "elements": [
                {
                    "index": int,           # 元素编号（用于 phone_tap_element）
                    "type": str,            # 元素类型 (button/input/text/toggle/image/...)
                    "label": str,           # 显示文本/描述
                    "center": [x, y],       # 中心坐标
                    "bounds": [x1,y1,x2,y2],# 边界坐标
                    "clickable": bool,
                    "scrollable": bool,
                    "resource_id": str,     # 资源 ID（精简，去包名前缀）
                }
            ],
            "scrollable_areas": [           # 可滚动区域
                {"bounds": [x1,y1,x2,y2], "direction": "vertical"/"horizontal"}
            ],
            "element_count": int,           # 交互元素总数
            "all_texts": [str],             # 页面所有可见文本
            "screenshot_base64": str | None,          # 仅 include_screenshot=True 时返回
            "screenshot_annotated_base64": str | None # 仅 annotate_elements=True 时返回
        }

    Notes / 特殊逻辑:
        - 一次调用返回页面全部关键信息，避免 LLM 多次往返
        - elements 按视觉位置排序（从上到下、从左到右）
        - 使用 index 引用元素：如 "点击元素 3" → phone_tap_element(index=3)
        - resource_id 自动去除包名前缀（如 com.example:id/btn → btn）
        - 默认只返回可交互或有文本的元素，设置 include_invisible=True 返回全部
        - annotate_elements=True 时自动截图并在图上标注每个元素的编号和边框（自动隐含 screenshot）
    """
    # Resolve device
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial or None, platform
    )
    if err:
        return err

    serial = resolved_serial or ""
    plat = resolved_platform or "android"

    try:
        driver = get_driver(serial, plat)

        # 1. Get current activity
        activity_info = driver.ui.get_current_activity()

        # 2. Get screen size
        try:
            width, height = driver.screen.get_screen_size()
        except Exception:
            width, height = 0, 0

        # 3. Collect elements via shared helper
        elements, scrollable_areas, all_texts = _collect_page_elements(
            driver, include_invisible=include_invisible
        )

        # 4. Screenshot (raw and/or annotated)
        screenshot_b64 = None
        annotated_b64 = None
        need_screenshot = include_screenshot or annotate_elements

        png_bytes: bytes | None = None
        if need_screenshot:
            try:
                png_bytes = driver.screen.screenshot()
                screenshot_b64 = base64.b64encode(png_bytes).decode("ascii")
            except Exception:
                pass

        if annotate_elements and png_bytes:
            try:
                from phone_pilot.extensions.vision.annotate import (
                    annotate_elements_on_screenshot,
                )

                annotated_png = annotate_elements_on_screenshot(png_bytes, elements)
                annotated_b64 = base64.b64encode(annotated_png).decode("ascii")
            except Exception:
                pass

        return {
            "ok": True,
            "device_serial": serial,
            "platform": plat,
            "activity": activity_info,
            "screen": {"width": width, "height": height},
            "elements": elements,
            "element_count": len(elements),
            "scrollable_areas": scrollable_areas,
            "all_texts": all_texts,
            "screenshot_base64": screenshot_b64,
            "screenshot_annotated_base64": annotated_b64,
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": serial}


@mcp.tool()
async def phone_tap_element(
    index: int,
    device_serial: str = "",
    platform: str = "auto",
    wait_ms: int = 500,
) -> dict:
    """通过元素编号点击页面元素（配合 phone_get_page_state 使用）。
    Tap a page element by its index from phone_get_page_state.

    Parameters / 参数:
        index: 元素编号（来自 phone_get_page_state 返回的 elements[].index）/ Element index
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: "android" | "harmony" | "auto" / Platform type
        wait_ms: 点击后等待毫秒数，默认 500 / Wait after tap in ms, default 500

    Returns / 返回值:
        dict: {"ok": True, "index": int, "center": [x, y], "label": str, "type": str}
        或 {"ok": False, "error": str}

    Notes / 特殊逻辑:
        - 先 dump UI → 用与 phone_get_page_state 相同的过滤/排序逻辑构建 elements 列表
        - 取 elements[index] 的中心坐标进行点击
        - index 越界时返回 error="index_out_of_range"
    """
    resolved_serial, resolved_platform, err = _resolve_device_serial(
        device_serial or None, platform
    )
    if err:
        return err

    serial = resolved_serial or ""
    plat = resolved_platform or "android"

    try:
        driver = get_driver(serial, plat)

        # Collect elements with same logic as phone_get_page_state
        elements, _scrollable, _texts = _collect_page_elements(driver)

        if index < 0 or index >= len(elements):
            return {
                "ok": False,
                "error": "index_out_of_range",
                "index": index,
                "element_count": len(elements),
                "note": f"有效范围 0-{len(elements) - 1}"
                if elements
                else "页面无可交互元素",
            }

        elem = elements[index]
        cx, cy = elem["center"]

        # Tap
        import time as _time

        driver.input.tap(cx, cy)
        if wait_ms > 0:
            _time.sleep(wait_ms / 1000.0)

        return {
            "ok": True,
            "device_serial": serial,
            "platform": plat,
            "index": index,
            "center": [cx, cy],
            "label": elem.get("label", ""),
            "type": elem.get("type", "view"),
        }

    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": serial}


# =============================================================================
# Server Entry Point
# =============================================================================


def run_server(transport: str = "stdio"):
    """Run the MCP server."""
    mcp.run(transport)


if __name__ == "__main__":
    run_server()
