"""Device Management Tools for phone_pilot MCP server.

Provides MCP tool implementations for:
- phone_list_devices: List connected Android and HarmonyOS devices
- phone_build_device_profile: Build and persist device profile (JSON + SQLite)
- phone_export_device_info_md: Export device info to a Markdown file
- phone_screenshot: Take a screenshot of the device screen
- phone_get_screen_size: Get screen dimensions
"""

from __future__ import annotations

import asyncio
import base64
import pathlib
from typing import Any, Optional

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import (
    get_driver,
    _resolve_device_serial,
    _resolve_driver_and_platform,
)


def _phone_list_devices_sync() -> dict:
    """Sync body for phone_list_devices (run in thread to avoid blocking event loop)."""
    from phone_pilot.android.adb.utils import ensure_adb_env, adb_executable
    from phone_pilot.android.adb.runner import CommandRunner
    from phone_pilot.android.adb.parsers import parse_adb_devices

    ensure_adb_env()
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

    # iOS devices (booted simulators) - best effort
    try:
        from phone_pilot.ios.system import list_ios_devices

        result.extend(list_ios_devices())
    except Exception:
        pass

    # Attach concurrency lock status for each device
    try:
        from phone_pilot.core.device_lease import get_device_lock_status

        for entry in result:
            s = entry.get("serial")
            if s:
                st = get_device_lock_status(s)
                entry["locked"] = bool(st.get("locked"))
                if st.get("locked"):
                    entry["lock_info"] = st.get("lock_info")
    except Exception:
        pass

    return {"ok": True, "devices": result}


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
    return await asyncio.to_thread(_phone_list_devices_sync)


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


def _phone_screenshot_sync(
    device_serial: str,
    platform: str,
    save_path: Optional[str],
) -> dict:
    """Sync body for phone_screenshot (run in thread to avoid blocking event loop)."""
    try:
        driver, resolved_serial, resolved_plat = _resolve_driver_and_platform(
            device_serial, platform, get_driver
        )
        png_bytes = driver.screen.screenshot()
        if not png_bytes:
            return {
                "ok": False,
                "error": "screenshot_failed",
                "device_serial": resolved_serial,
                "platform": resolved_plat,
            }

        if save_path:
            path = pathlib.Path(save_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(png_bytes)
            return {
                "ok": True,
                "device_serial": resolved_serial,
                "platform": resolved_plat,
                "screenshot_path": str(path),
            }
        else:
            return {
                "ok": True,
                "device_serial": resolved_serial,
                "platform": resolved_plat,
                "screenshot_base64": base64.b64encode(png_bytes).decode("ascii"),
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_screenshot(
    device_serial: str,
    platform: str = "auto",
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
    return await asyncio.to_thread(
        _phone_screenshot_sync, device_serial, platform, save_path
    )


def _phone_get_screen_size_sync(
    device_serial: str,
    platform: str,
) -> dict:
    """Sync body for phone_get_screen_size (run in thread to avoid blocking event loop)."""
    try:
        driver, resolved_serial, resolved_plat = _resolve_driver_and_platform(
            device_serial, platform, get_driver
        )
        width, height = driver.screen.get_screen_size()
        return {
            "ok": True,
            "device_serial": resolved_serial,
            "platform": resolved_plat,
            "width": width,
            "height": height,
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "device_serial": device_serial}


@mcp.tool()
async def phone_get_screen_size(
    device_serial: str,
    platform: str = "auto",
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
    return await asyncio.to_thread(_phone_get_screen_size_sync, device_serial, platform)


# ---------------------------------------------------------------------------
# Device Concurrency / Lease Tools (Multi-Agent & Multi-Project Coordination)
# ---------------------------------------------------------------------------


def _phone_device_lock_status_sync(
    device_serial: Optional[str] = None, platform: str = "auto"
) -> dict:
    resolved, plat, err = _resolve_device_serial(device_serial, platform)
    if err:
        return err

    from phone_pilot.core.device_lease import get_device_lock_status

    st = get_device_lock_status(resolved)
    st["device_serial"] = resolved
    st["platform"] = plat
    return st


@mcp.tool()
async def phone_device_lock_status(
    device_serial: Optional[str] = None,
    platform: str = "auto",
) -> dict:
    """查询指定移动设备的并发独占锁状态（用于多 Agent/多项目共享设备协同）。
    Check the concurrency lease/lock status of a mobile device.

    Parameters / 参数:
        device_serial: 设备序列号（缺省时自动解析或读取 PHONE_PILOT_DEVICE_SERIAL 环境变量）
        platform: 平台类型（android / harmony / ios / auto）

    Returns / 返回值:
        dict: {
            "ok": True,
            "device_serial": "xxx",
            "locked": bool,
            "lock_info": {
                "session_id": "...",
                "holder_name": "...",
                "purpose": "...",
                "remaining_s": 95.5,
                "expires_at": ...
            }
        }
    """
    return await asyncio.to_thread(
        _phone_device_lock_status_sync, device_serial, platform
    )


def _phone_device_acquire_lock_sync(
    device_serial: Optional[str] = None,
    session_id: Optional[str] = None,
    purpose: str = "",
    ttl_s: float = 120.0,
    wait_timeout_s: float = 10.0,
    platform: str = "auto",
) -> dict:
    resolved, plat, err = _resolve_device_serial(device_serial, platform)
    if err:
        return err

    from phone_pilot.core.device_lease import acquire_device_lock

    res = acquire_device_lock(
        serial=resolved,
        session_id=session_id,
        purpose=purpose,
        ttl_s=ttl_s,
        wait_timeout_s=wait_timeout_s,
    )
    res["platform"] = plat
    return res


@mcp.tool()
async def phone_device_acquire_lock(
    device_serial: Optional[str] = None,
    session_id: Optional[str] = None,
    purpose: str = "",
    ttl_s: float = 120.0,
    wait_timeout_s: float = 10.0,
    platform: str = "auto",
) -> dict:
    """申请移动设备的独占排他锁（推荐在多 Agent 或多项目端到端验证前调用）。
    Acquire exclusive lease lock on a mobile device before running verification.

    Parameters / 参数:
        device_serial: 设备序列号（缺省时自动解析或读取环境变量）
        session_id: 会话标识（若省略则自动生成并在返回值中携带）
        purpose: 本次验证的业务目标或项目说明（例如 'order-checkout E2E verification'）
        ttl_s: 租约超时时间（秒，默认 120s，超时后自动释放以防 Agent 崩溃死锁）
        wait_timeout_s: 锁被占用时的排队等待超时时间（秒，默认 10s，0 表示非阻塞立即返回）
        platform: 平台类型（android / harmony / ios / auto）

    Returns / 返回值:
        dict: 成功时 {"ok": True, "acquired": True, "device_serial": ..., "session_id": ..., "lock_info": ...}
              被占用排队超时 {"ok": False, "error": "device_busy", "current_holder": ...}
    """
    return await asyncio.to_thread(
        _phone_device_acquire_lock_sync,
        device_serial,
        session_id,
        purpose,
        ttl_s,
        wait_timeout_s,
        platform,
    )


def _phone_device_release_lock_sync(
    device_serial: Optional[str] = None,
    session_id: Optional[str] = None,
    force: bool = False,
    platform: str = "auto",
) -> dict:
    resolved, plat, err = _resolve_device_serial(device_serial, platform)
    if err:
        return err

    from phone_pilot.core.device_lease import release_device_lock

    res = release_device_lock(
        serial=resolved,
        session_id=session_id,
        force=force,
    )
    res["platform"] = plat
    return res


@mcp.tool()
async def phone_device_release_lock(
    device_serial: Optional[str] = None,
    session_id: Optional[str] = None,
    force: bool = False,
    platform: str = "auto",
) -> dict:
    """释放移动设备的独占排他锁。
    Release exclusive lease lock on a mobile device.

    Parameters / 参数:
        device_serial: 设备序列号（缺省时自动解析或读取环境变量）
        session_id: 获取锁时返回的 session_id（非本人锁无法释放，除非 force=True）
        force: 是否强制释放（管理员或排障接管时使用，默认 False）
        platform: 平台类型（android / harmony / ios / auto）

    Returns / 返回值:
        dict: {"ok": True, "released": True, "device_serial": ..., ...}
    """
    return await asyncio.to_thread(
        _phone_device_release_lock_sync,
        device_serial,
        session_id,
        force,
        platform,
    )


def _phone_device_renew_lock_sync(
    device_serial: Optional[str] = None,
    session_id: str = "",
    ttl_s: float = 120.0,
    platform: str = "auto",
) -> dict:
    resolved, plat, err = _resolve_device_serial(device_serial, platform)
    if err:
        return err

    from phone_pilot.core.device_lease import renew_device_lock

    res = renew_device_lock(
        serial=resolved,
        session_id=session_id,
        ttl_s=ttl_s,
    )
    res["platform"] = plat
    return res


@mcp.tool()
async def phone_device_renew_lock(
    session_id: str,
    device_serial: Optional[str] = None,
    ttl_s: float = 120.0,
    platform: str = "auto",
) -> dict:
    """为当前持有的设备独占锁续期（防止耗时长的测试过程中租约过期）。
    Renew/extend lease duration of an actively held device lock.

    Parameters / 参数:
        session_id: 获取锁时的 session_id
        device_serial: 设备序列号
        ttl_s: 延长有效时间（秒，默认 120s）
        platform: 平台类型（android / harmony / ios / auto）

    Returns / 返回值:
        dict: {"ok": True, "renewed": True, "lock_info": ...}
    """
    return await asyncio.to_thread(
        _phone_device_renew_lock_sync,
        device_serial,
        session_id,
        ttl_s,
        platform,
    )

