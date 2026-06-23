"""Advanced script/interaction MCP tools.

Covers scroll-to-find, wait-for-element, dismiss-popup, smart-find,
launch-from-home, install/uninstall app, memory analysis, and file
transfer (pull/push).
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import (
    get_driver,
    _with_device,
    _resolve_device_serial,
    _make_script_context,
)
from phone_pilot.script_api.runner import scroll_to_find
from phone_pilot.script_api.popup_guard import PopupGuard
from phone_pilot.script_api.find import find_text
from phone_pilot.script_api.actions import launch_from_home
from phone_pilot.script_api.context import RetryExhausted


# =============================================================================
# Page Interaction Tools (scroll / wait / dismiss)
# =============================================================================


def _phone_scroll_to_find_sync(
    device_serial: str | None,
    text: str,
    direction: str,
    max_count: int,
    use_ocr: bool,
    exact: bool,
    platform: str,
) -> dict:
    """Sync body for phone_scroll_to_find."""
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
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
        center = (
            elem.center() if hasattr(elem, "center") and callable(elem.center) else None
        )
        bounds = (
            elem.bounds() if hasattr(elem, "bounds") and callable(elem.bounds) else None
        )
        display_text = getattr(elem, "display_text", None) or getattr(
            elem, "text", str(elem)
        )
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
    return await asyncio.to_thread(
        _phone_scroll_to_find_sync,
        device_serial or None,
        text,
        direction,
        max_count,
        use_ocr,
        exact,
        platform,
    )


def _phone_wait_for_element_sync(
    device_serial: str | None,
    text: str,
    resource_id: str,
    timeout_s: float,
    platform: str,
    require_clickable: bool = False,
    require_enabled: bool = False,
) -> dict:
    """Sync body for phone_wait_for_element."""
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    def _node_ready(node) -> bool:
        # When a state is required, the node must explicitly satisfy it.
        if require_clickable and not getattr(node, "clickable", False):
            return False
        if require_enabled and not getattr(node, "enabled", False):
            return False
        return True

    def _node_payload(node, node_text, node_desc, node_rid) -> dict:
        bounds = (
            node.bounds_tuple()
            if hasattr(node, "bounds_tuple") and callable(node.bounds_tuple)
            else None
        )
        center = (
            node.center() if hasattr(node, "center") and callable(node.center) else None
        )
        cx, cy = center if center else (0, 0)
        return {
            "text": node_text,
            "content_desc": node_desc,
            "resource_id": node_rid,
            "center": [cx, cy],
            "bounds": list(bounds) if bounds else None,
            "clickable": bool(getattr(node, "clickable", False)),
            "enabled": bool(getattr(node, "enabled", False)),
        }

    try:
        driver = get_driver(resolved, plat)
        deadline = time.monotonic() + timeout_s
        seen_not_ready = False
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
                matched = (text and (text in node_text or text in node_desc)) or (
                    resource_id and resource_id in node_rid
                )
                if not matched:
                    continue
                if not _node_ready(node):
                    seen_not_ready = True
                    continue
                return {
                    "ok": True,
                    "found": True,
                    "element": _node_payload(node, node_text, node_desc, node_rid),
                    "device_serial": resolved,
                    "platform": plat,
                }
            time.sleep(0.8)
        return {
            "ok": True,
            "found": False,
            "present_but_not_ready": seen_not_ready,
            "device_serial": resolved,
            "platform": plat,
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
    require_clickable: bool = False,
    require_enabled: bool = False,
) -> dict:
    """等待元素出现在页面上。
    Wait for an element to appear on the page.

    Parameters / 参数:
        text: 等待出现的文本 / Text to wait for
        resource_id: 等待出现的资源 ID / Resource ID to wait for
        timeout_s: 超时秒数，默认 10 / Timeout in seconds, default 10
        device_serial: 设备序列号，空则自动检测 / Device serial, auto-detect if empty
        platform: 平台类型 / Platform type
        require_clickable: 必须可点击才算找到 / Element must be clickable to count
        require_enabled: 必须 enabled 才算找到 / Element must be enabled to count

    Returns / 返回值:
        dict: {"ok": True, "found": True, "element": {...}} 如果找到
        dict: {"ok": True, "found": False, "present_but_not_ready": bool} 如果超时
        dict: {"ok": False, "error": str} 如果出错

    Notes / 说明:
        - require_clickable/require_enabled 解决"元素已出现但尚不可点"的时序问题。
          超时时 present_but_not_ready=True 表示元素在但状态未就绪。
    """
    return await asyncio.to_thread(
        _phone_wait_for_element_sync,
        device_serial or None,
        text,
        resource_id,
        timeout_s,
        platform,
        require_clickable,
        require_enabled,
    )


def _phone_dismiss_popup_sync(
    device_serial: str | None,
    platform: str,
) -> dict:
    """Sync body for phone_dismiss_popup."""
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
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
    return await asyncio.to_thread(
        _phone_dismiss_popup_sync,
        device_serial or None,
        platform,
    )


def _phone_smart_find_sync(
    device_serial: str | None,
    text: str,
    use_ocr: bool,
    exact: bool,
    try_dismiss_popup: bool,
    platform: str,
) -> dict:
    """Sync body for phone_smart_find."""
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
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
            center = (
                elem.center()
                if hasattr(elem, "center") and callable(elem.center)
                else None
            )
            bounds = (
                elem.bounds()
                if hasattr(elem, "bounds") and callable(elem.bounds)
                else None
            )
            display_text = getattr(elem, "display_text", None) or getattr(
                elem, "text", str(elem)
            )
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
    return await asyncio.to_thread(
        _phone_smart_find_sync,
        device_serial or None,
        text,
        use_ocr,
        exact,
        try_dismiss_popup,
        platform,
    )


def _phone_launch_from_home_sync(
    device_serial: str | None,
    query: str,
    platform: str,
) -> dict:
    """Sync body for phone_launch_from_home."""
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err
    try:
        ctx = _make_script_context(resolved, plat)
        return launch_from_home(ctx, query)
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
    return await asyncio.to_thread(
        _phone_launch_from_home_sync,
        device_serial or None,
        query,
        platform,
    )


def _phone_install_app_sync(
    device_serial: str | None,
    platform: str,
    apk_path: str,
) -> dict:
    """Sync body for phone_install_app."""
    apk = Path(apk_path).expanduser().resolve()
    if not apk.exists():
        return {"ok": False, "error": f"apk not found: {apk_path}"}
    if apk.suffix.lower() not in (".apk", ".xapk", ".apks"):
        return {
            "ok": False,
            "error": f"invalid file type: {apk.suffix} (expected .apk/.xapk/.apks)",
        }
    resolved, plat, err = _resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err
    # 仅支持 Android / Android only
    if plat != "android":
        return {
            "ok": False,
            "error": "install_app only supports Android",
            "platform": plat,
        }
    try:
        from phone_pilot.android.touch.agent import install_apk

        install_apk(resolved, apk)
        return {"ok": True, "apk_path": str(apk), "device_serial": resolved}
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
    return await asyncio.to_thread(
        _phone_install_app_sync,
        device_serial or None,
        platform,
        apk_path,
    )


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
    if not package or not package.strip():
        return {"ok": False, "error": "package is required"}
    return await asyncio.to_thread(
        _with_device,
        lambda s, p: (lambda r: {**r, "package": package})(
            get_driver(s, p).app.uninstall(package, keep_data=keep_data)
        ),
        device_serial or None,
        platform,
    )


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

    def _do(s: str, p: str) -> dict:
        if p != "android":
            return {
                "ok": False,
                "error": "memory_snapshot only supports Android",
                "platform": p,
            }
        from phone_pilot.memory_analyze.meminfo import capture_meminfo

        return capture_meminfo(s, package)

    return await asyncio.to_thread(_with_device, _do, device_serial or None, platform)


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

    def _do(s: str, p: str) -> dict:
        if p != "android":
            return {
                "ok": False,
                "error": "memory_check_leak only supports Android",
                "platform": p,
            }
        from phone_pilot.memory_analyze.meminfo import check_activity_leak

        hprof_dir = str(Path("./.recordings").expanduser().resolve() / "meminfo")
        return check_activity_leak(
            s,
            package,
            gc_wait_s=gc_wait_s,
            save_hprof=save_hprof,
            hprof_out_dir=hprof_dir,
        )

    return await asyncio.to_thread(_with_device, _do, device_serial or None, platform)


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

    def _do(s: str, p: str) -> dict:
        if local_path:
            local = Path(local_path).expanduser().resolve()
        else:
            local = (
                Path(out_dir).expanduser().resolve() / "pulled" / Path(remote_path).name
            )
        local.parent.mkdir(parents=True, exist_ok=True)
        result = get_driver(s, p).pull_file(remote_path, str(local))
        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("stderr") or result.get("error", "pull_failed"),
            }
        return {
            "ok": True,
            "local_path": str(local),
            "remote_path": remote_path,
        }

    return await asyncio.to_thread(_with_device, _do, device_serial or None, platform)


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

    def _do(s: str, p: str) -> dict:
        lp = Path(local_path).expanduser().resolve()
        if not lp.exists():
            return {"ok": False, "error": f"local file not found: {local_path}"}
        result = get_driver(s, p).push_file(str(lp), remote_path)
        result["local_path"] = str(lp)
        result["remote_path"] = remote_path
        return result

    return await asyncio.to_thread(_with_device, _do, device_serial or None, platform)
