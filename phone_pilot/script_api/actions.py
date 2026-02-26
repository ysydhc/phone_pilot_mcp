"""输入/滑动/截图/APP 控制/设备操作 / Input, swipe, screenshot, app control, device actions.

脚本中用于操控设备的各类操作函数。
Device manipulation functions used in scripts.
"""
from __future__ import annotations

import pathlib
import time
from typing import Optional

from phone_pilot.core.ui import UIElement
from phone_pilot.core.log import _log as _runner_log

from .context import ScriptContext
from ._helpers import (
    _get_screen_size,
    _input_swipe,
    _input_tap,
    _input_keyevent,
    _input_text,
    _get_current_focus,
    _ensure_out_dir,
)


# ---------------------------------------------------------------------------
# App 控制
# ---------------------------------------------------------------------------

def clear_background(ctx: ScriptContext) -> dict:
    """清空后台应用 / Clear background apps.

    调用系统「最近任务」并清理。平台差异由 driver 封装。
    Invokes system recents and clears apps. Platform-specific behavior is handled by driver.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context

    Returns / 返回值:
        dict: driver 返回的结果 / Driver result
    """
    return ctx.driver.clear_background()


def reset_home_screen(ctx: ScriptContext, *, home_presses: int = 2) -> dict:
    """重置到桌面 / Reset to home screen.

    按 Home 键若干次后返回桌面，用于清理当前任务栈。
    Presses Home key multiple times to return to launcher, clearing task stack.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        home_presses: Home 键次数，默认 2 / Number of Home presses (default 2)

    Returns / 返回值:
        dict: {"ok": True, "platform": str} / Result with platform info
    """
    presses = max(1, int(home_presses))
    for _ in range(presses):
        ctx.driver.go_home()
        time.sleep(0.6)
    time.sleep(0.5)
    return {"ok": True, "platform": ctx.driver.platform}


def restart_app_pkg(ctx: ScriptContext, package: str) -> dict:
    """按包名重启应用 / Restart app by package name.

    先 force_stop 再 launch，用于确保应用处于冷启动状态。
    Force-stops then launches the app to ensure cold start state.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        package: 应用包名（如 com.example.app）/ App package name

    Returns / 返回值:
        dict: {"ok": bool, "package": str, "force_stop": dict, "launch": dict}
    """
    pkg = (package or "").strip()
    if not pkg:
        return {"ok": False, "error": "package is required"}
    stop_res = ctx.driver.app.force_stop(pkg)
    launch_res = ctx.driver.app.launch_app(pkg)
    ok = bool(stop_res.get("ok")) and bool(launch_res.get("ok"))
    return {"ok": ok, "package": pkg, "force_stop": stop_res, "launch": launch_res}


def launch_from_home(ctx: ScriptContext, query: str, **kwargs) -> dict:
    """从桌面启动应用 / Launch app from home screen.

    在桌面搜索 query 并点击打开，用于按应用名启动。
    Searches launcher for query and taps to open the app.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        query: 应用名或关键词 / App name or keyword
        **kwargs: 传给 driver.launch_from_home 的额外参数 / Extra args for driver

    Returns / 返回值:
        dict: driver 返回的结果 / Driver result
    """
    if not query:
        return {"ok": False, "error": "query is required"}
    kwargs.setdefault("out_dir", ctx.out_dir)
    return ctx.driver.launch_from_home(query, **kwargs)


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------

def screenshot(ctx: ScriptContext, name: str) -> dict:
    """手动截图并保存 / Take screenshot and save.

    存储位置 / Storage paths:
    - 脚本环境（RunSession 存在）→ session.run_dir/screenshots/
    - 非脚本环境（MCP 等）→ .recordings/screenshots/

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        name: 文件名（不含扩展名）/ Filename without extension

    Returns / 返回值:
        dict: {"ok": True, "path": str} 保存路径 / Saved file path
    """
    session = ctx.session
    if session and session.run_dir:
        screenshots_dir = session.run_dir / "screenshots"
    else:
        screenshots_dir = pathlib.Path(ctx.out_dir or "./.recordings").expanduser().resolve() / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    path = screenshots_dir / f"{name}.png"
    png_data = ctx.driver.screen.screenshot()
    path.write_bytes(png_data)
    _runner_log(f"  [截图] 已保存: {path}")
    return {"ok": True, "path": str(path)}


def screenshot_annotated(ctx: ScriptContext, name: str) -> dict:
    """截图 + UI 元素标注合一 / Screenshot with UI element annotations.

    截取当前屏幕，dump UI 层级后在截图上标注每个可交互元素的编号和边框，
    同时保存原始截图和标注截图。
    Takes a screenshot, dumps UI hierarchy, annotates each interactive element
    with its index and bounding box, then saves both raw and annotated images.

    存储位置 / Storage paths:
    - 脚本环境 → session.run_dir/screenshots/{name}.png + {name}_annotated.png
    - 非脚本环境 → .recordings/screenshots/

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        name: 文件名（不含扩展名）/ Filename without extension

    Returns / 返回值:
        dict: {
            "ok": True,
            "path": str,               # 原始截图路径
            "annotated_path": str,      # 标注截图路径
            "element_count": int,       # 标注的元素数量
        }
    """
    from phone_pilot.mcp.server import _collect_page_elements
    from phone_pilot.extensions.vision.annotate import annotate_elements_on_screenshot

    session = ctx.session
    if session and session.run_dir:
        screenshots_dir = session.run_dir / "screenshots"
    else:
        screenshots_dir = pathlib.Path(ctx.out_dir or "./.recordings").expanduser().resolve() / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    # 1. Screenshot
    png_data = ctx.driver.screen.screenshot()
    raw_path = screenshots_dir / f"{name}.png"
    raw_path.write_bytes(png_data)

    # 2. Collect elements
    elements, _scrollable, _texts = _collect_page_elements(ctx.driver)

    # 3. Annotate
    annotated_png = annotate_elements_on_screenshot(png_data, elements)
    annotated_path = screenshots_dir / f"{name}_annotated.png"
    annotated_path.write_bytes(annotated_png)

    _runner_log(f"  [截图+标注] 已保存: {annotated_path} ({len(elements)} 元素)")
    return {
        "ok": True,
        "path": str(raw_path),
        "annotated_path": str(annotated_path),
        "element_count": len(elements),
    }


# ---------------------------------------------------------------------------
# 滑动
# ---------------------------------------------------------------------------

def swipe(
    ctx: ScriptContext,
    *,
    x1_pct: Optional[float] = None,
    y1_pct: Optional[float] = None,
    x2_pct: Optional[float] = None,
    y2_pct: Optional[float] = None,
    x1: Optional[int] = None,
    y1: Optional[int] = None,
    x2: Optional[int] = None,
    y2: Optional[int] = None,
    duration_ms: int = 320,
    wait_s: float = 0.15,
) -> dict:
    """执行滑动手势 / Perform swipe gesture.

    支持百分比坐标（x1_pct/y1_pct 等）或绝对像素（x1/y1 等）。
    百分比以屏幕宽高为 1.0。未指定时默认从 (0.5, 0.8) 滑到 (0.5, 0.2)。
    Supports percent coords (x1_pct etc.) or absolute pixels (x1 etc.).
    Percent uses screen size as 1.0. Default: (0.5, 0.8) → (0.5, 0.2).

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        x1_pct, y1_pct, x2_pct, y2_pct: 起点/终点百分比 0–1 / Start/end percent
        x1, y1, x2, y2: 起点/终点绝对像素 / Absolute pixels
        duration_ms: 滑动持续时间毫秒 / Swipe duration in ms
        wait_s: 滑动后等待秒数 / Wait after swipe (seconds)

    Returns / 返回值:
        dict: driver.input.swipe 的结果 / Driver result
    """
    if any(v is not None for v in (x1_pct, y1_pct, x2_pct, y2_pct)):
        screen = _get_screen_size(ctx)
        if not screen:
            return {"ok": False, "error": "screen_size_unavailable"}
        w, h = screen
        x1 = int(round(float(x1_pct if x1_pct is not None else 0.5) * w))
        y1 = int(round(float(y1_pct if y1_pct is not None else 0.8) * h))
        x2 = int(round(float(x2_pct if x2_pct is not None else 0.5) * w))
        y2 = int(round(float(y2_pct if y2_pct is not None else 0.2) * h))
    if None in (x1, y1, x2, y2):
        return {"ok": False, "error": "swipe_coordinates_unavailable"}
    return _input_swipe(ctx, int(x1), int(y1), int(x2), int(y2), duration_ms=duration_ms, wait_s=wait_s)


def swipe_up(
    ctx: ScriptContext,
    *,
    x_pct: float = 0.5,
    y1_pct: float = 0.8,
    y2_pct: float = 0.2,
    duration_ms: int = 320,
    wait_s: float = 0.15,
) -> dict:
    """向上滑动 / Swipe upward.

    默认从屏幕 80% 高度滑到 20%，常用于列表上翻。
    Default: from 80% to 20% of screen height. Common for list scroll up.

    Parameters / 参数:
        x_pct: 水平中心百分比 / Horizontal center (default 0.5)
        y1_pct, y2_pct: 起点/终点纵坐标百分比 / Start/end Y percent
        duration_ms, wait_s: 同 swipe / Same as swipe
    """
    return swipe(
        ctx,
        x1_pct=float(x_pct),
        y1_pct=float(y1_pct),
        x2_pct=float(x_pct),
        y2_pct=float(y2_pct),
        duration_ms=int(duration_ms),
        wait_s=float(wait_s),
    )


def swipe_down(
    ctx: ScriptContext,
    *,
    x_pct: float = 0.5,
    y1_pct: float = 0.2,
    y2_pct: float = 0.8,
    duration_ms: int = 320,
    wait_s: float = 0.15,
) -> dict:
    """向下滑动 / Swipe downward.

    默认从 20% 滑到 80%，常用于列表下翻。
    Default: from 20% to 80%. Common for list scroll down.
    """
    return swipe(
        ctx,
        x1_pct=float(x_pct),
        y1_pct=float(y1_pct),
        x2_pct=float(x_pct),
        y2_pct=float(y2_pct),
        duration_ms=int(duration_ms),
        wait_s=float(wait_s),
    )


def swipe_left(
    ctx: ScriptContext,
    *,
    y_pct: float = 0.5,
    x1_pct: float = 0.8,
    x2_pct: float = 0.2,
    duration_ms: int = 320,
    wait_s: float = 0.15,
) -> dict:
    """向左滑动 / Swipe left.

    默认从屏幕 80% 宽度滑到 20%，常用于横向翻页。
    Default: from 80% to 20% of screen width. Common for horizontal paging.
    """
    return swipe(
        ctx,
        x1_pct=float(x1_pct),
        y1_pct=float(y_pct),
        x2_pct=float(x2_pct),
        y2_pct=float(y_pct),
        duration_ms=int(duration_ms),
        wait_s=float(wait_s),
    )


def swipe_right(
    ctx: ScriptContext,
    *,
    y_pct: float = 0.5,
    x1_pct: float = 0.2,
    x2_pct: float = 0.8,
    duration_ms: int = 320,
    wait_s: float = 0.15,
) -> dict:
    """向右滑动 / Swipe right.

    默认从 20% 滑到 80% 宽度，常用于横向翻页。
    Default: from 20% to 80% width. Common for horizontal paging.
    """
    return swipe(
        ctx,
        x1_pct=float(x1_pct),
        y1_pct=float(y_pct),
        x2_pct=float(x2_pct),
        y2_pct=float(y_pct),
        duration_ms=int(duration_ms),
        wait_s=float(wait_s),
    )


# ---------------------------------------------------------------------------
# 输入
# ---------------------------------------------------------------------------

def type_text(ctx: ScriptContext, text: str, *, enter: bool = False) -> dict:
    """输入文本 / Type text.

    向当前焦点输入框输入文本。enter=True 时在末尾发送回车。
    Types text into the focused input. enter=True sends Enter at the end.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        text: 要输入的字符串 / Text to type
        enter: 是否在末尾发送回车 / Send Enter after text

    Returns / 返回值:
        dict: driver.input.input_text 的结果 / Driver result
    """
    return _input_text(ctx, str(text or ""), enter=bool(enter))


def keyevent(ctx: ScriptContext, keycode: str) -> dict:
    """发送按键事件 / Send key event.

    支持 BACK/HOME/RECENTS/MENU/ENTER/DPAD_* 等 Android keycode。
    Supports Android keycodes: BACK, HOME, RECENTS, MENU, ENTER, DPAD_*, etc.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        keycode: 按键名（如 "BACK", "HOME"）/ Key name

    Returns / 返回值:
        dict: driver.input.keyevent 的结果 / Driver result
    """
    return _input_keyevent(ctx, str(keycode or ""))


def tap_xy(ctx: ScriptContext, x: int, y: int) -> dict:
    """按坐标点击 / Tap at coordinates.

    在指定像素坐标执行点击。通常用 find_text().tap() 代替以点击元素中心。
    Taps at the given pixel coordinates. Prefer find_text().tap() to tap element center.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        x, y: 点击坐标（像素）/ Tap coordinates in pixels

    Returns / 返回值:
        dict: driver.input.tap 的结果 / Driver result
    """
    return _input_tap(ctx, int(x), int(y))


# ---------------------------------------------------------------------------
# 设备 & 内存
# ---------------------------------------------------------------------------

def mem_snapshot(ctx: ScriptContext, name: str = "auto", *, package: Optional[str] = None) -> dict:
    """一键采集 meminfo 快照并存储到 RunSession / Capture meminfo snapshot and store in RunSession.

    采集 dumpsys meminfo 并保存为 JSON。脚本环境下保存到 session/meminfo/，
    非脚本环境保存到 .recordings/meminfo/。

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        name: 快照名（如 "before", "after"）/ Snapshot name
        package: 包名，为空时自动获取当前焦点应用 / Package name, auto-detect if None

    Returns / 返回值:
        dict: capture_meminfo 的返回结果 / capture_meminfo result
    """
    pkg = (package or "").strip()
    if not pkg:
        focus = _get_current_focus(ctx)
        if isinstance(focus, dict):
            pkg = str(focus.get("package") or "")
    if not pkg:
        return {"ok": False, "error": "package_required"}

    from phone_pilot.memory_analyze.meminfo import capture_meminfo
    result = capture_meminfo(ctx.device_serial, pkg)
    if not result.get("ok"):
        _runner_log(f"  [meminfo] {name} 采集失败: {result.get('error', '未知')}")
        return result

    # Save to session or fallback dir
    session = ctx.session
    if session:
        save_dir = session.meminfo_dir
    else:
        save_dir = pathlib.Path(ctx.out_dir or "./.recordings").expanduser().resolve() / "meminfo"
    save_dir.mkdir(parents=True, exist_ok=True)

    import json
    path = save_dir / f"{name}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = result.get("summary", {})
    _runner_log(
        f"  [meminfo] {name}: PSS={summary.get('total_pss_mb', '?')}MB "
        f"Native={summary.get('native_heap_mb', '?')}MB "
        f"Dalvik={summary.get('dalvik_heap_mb', '?')}MB"
    )
    return result


def mem_diff(ctx: ScriptContext, before_name: str = "before", after_name: str = "after") -> dict:
    """对比两次 meminfo 快照并保存 diff 结果 / Compare two meminfo snapshots and save diff.

    读取 session/meminfo/ 下的 before 和 after JSON，计算增量并保存 diff.json。

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        before_name: before 快照文件名（不含 .json）/ Before snapshot name
        after_name: after 快照文件名（不含 .json）/ After snapshot name

    Returns / 返回值:
        dict: diff_meminfo 的返回结果 / diff result
    """
    import json

    session = ctx.session
    if session:
        mem_dir = session.meminfo_dir
    else:
        mem_dir = pathlib.Path(ctx.out_dir or "./.recordings").expanduser().resolve() / "meminfo"

    before_path = mem_dir / f"{before_name}.json"
    after_path = mem_dir / f"{after_name}.json"

    if not before_path.is_file():
        return {"ok": False, "error": f"before snapshot not found: {before_path}"}
    if not after_path.is_file():
        return {"ok": False, "error": f"after snapshot not found: {after_path}"}

    try:
        before = json.loads(before_path.read_text(encoding="utf-8"))
        after = json.loads(after_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": f"read snapshot error: {e}"}

    from phone_pilot.memory_analyze.meminfo import diff_meminfo, format_diff
    result = diff_meminfo(before, after)

    # Save diff
    diff_path = mem_dir / "diff.json"
    diff_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    _runner_log(format_diff(result))
    return result


def mem_check_leak(ctx: ScriptContext, *, package: Optional[str] = None) -> dict:
    """Activity 泄漏检测 + 可选 Shark CLI 分析 / Activity leak detection + optional Shark analysis.

    检测流程:
    1. dumpsys meminfo 获取 Activity 实例数
    2. 对比可见 Activity 数
    3. 若可疑 → GC → 等 5s → 再验证
    4. 若有 Shark CLI → 自动深度分析 hprof

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        package: 包名，为空时自动获取 / Package name, auto-detect if None

    Returns / 返回值:
        dict: check_activity_leak 的返回结果 / Leak detection result
    """
    import json

    pkg = (package or "").strip()
    if not pkg:
        focus = _get_current_focus(ctx)
        if isinstance(focus, dict):
            pkg = str(focus.get("package") or "")
    if not pkg:
        return {"ok": False, "error": "package_required"}

    session = ctx.session
    if session:
        hprof_dir = str(session.meminfo_dir)
    else:
        hprof_dir = str(pathlib.Path(ctx.out_dir or "./.recordings").expanduser().resolve() / "meminfo")

    from phone_pilot.memory_analyze.meminfo import check_activity_leak, format_leak_report
    result = check_activity_leak(
        ctx.device_serial, pkg,
        save_hprof=True,
        hprof_out_dir=hprof_dir,
    )

    # Save leak report
    mem_dir = pathlib.Path(hprof_dir)
    mem_dir.mkdir(parents=True, exist_ok=True)
    report_path = mem_dir / "leak_report.json"
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    _runner_log(format_leak_report(result))

    # If leak detected and Shark CLI is available, run deep analysis
    if result.get("leaked"):
        try:
            from phone_pilot.memory_analyze.shark_analyzer import analyze_with_shark
            hprof_path = result.get("hprof", "")
            if hprof_path:
                shark_result = analyze_with_shark(
                    hprof_path,
                    device_serial=ctx.device_serial,
                    package_name=pkg,
                )
                if shark_result.get("ok"):
                    shark_path = mem_dir / "shark_analysis.json"
                    shark_path.write_text(json.dumps(shark_result, ensure_ascii=False, indent=2), encoding="utf-8")
                    _runner_log(f"  [shark] 分析完成: {shark_result.get('leak_count', 0)} 个泄漏")
        except ImportError:
            pass  # shark_analyzer not available yet
        except Exception as e:
            _runner_log(f"  [shark] 分析异常: {e}")

    return result


def dump_hprof_snapshot(ctx: ScriptContext, name: str, *, package: Optional[str] = None, timeout_s: float = 60.0) -> dict:
    """Dump hprof 内存快照 / Dump hprof memory snapshot.

    对指定应用的 Java 堆执行 hprof dump，用于内存分析。
    Dumps Java heap of the target app for memory analysis.

    存储位置 / Storage paths:
    - 脚本环境（RunSession 存在）→ session.meminfo_dir/
    - 非脚本环境（MCP 等）→ .recordings/hprof/

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        name: 快照文件名（不含扩展名）/ Snapshot name without extension
        package: 应用包名，为空时使用当前焦点应用 / App package, or current focus if None
        timeout_s: dump 超时秒数 / Timeout in seconds

    Returns / 返回值:
        dict: {"ok": bool, "path": str, "size_bytes": int, ...}
    """
    pkg = (package or "").strip()
    if not pkg:
        focus = _get_current_focus(ctx)
        if isinstance(focus, dict):
            pkg = str(focus.get("package") or "")
    if not pkg:
        return {"ok": False, "error": "package_required"}

    # 根据环境选择 hprof 存储目录
    session = ctx.session
    if session:
        out_dir = str(session.meminfo_dir)
        hprof_subdir = ""
    else:
        out_dir = ctx.out_dir or "./.recordings"
        hprof_subdir = "hprof"

    from phone_pilot.memory_analyze.dumper import dump_hprof
    result = dump_hprof(
        ctx.device_serial, pkg,
        out_dir=out_dir, hprof_subdir=hprof_subdir,
        name=name, timeout_s=float(timeout_s),
    )
    if result.get("ok"):
        _runner_log(f"  [hprof] 已保存: {result.get('path')} ({result.get('size_bytes', 0) // 1024}KB)")
    else:
        _runner_log(f"  [hprof] dump 失败: {result.get('error', '未知错误')}")
    return result


def device_capture(ctx: ScriptContext) -> dict:
    """获取设备信息 / Get device info.

    返回设备型号、系统版本、屏幕尺寸等配置。可用于条件分支或报告。
    Returns device model, system version, screen size, etc. For conditional logic or reporting.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context

    Returns / 返回值:
        dict: {"ok": True, "profile": dict} 设备配置字典 / Device profile dict
    """
    _ensure_out_dir(ctx)
    # device_info from driver returns an in-memory dict; for backward compat
    # we also try the platform-specific profiler that writes to out_dir.
    prof = ctx.driver.device_info()
    return {"ok": True, "profile": prof}


def unlock_device(ctx: ScriptContext, *, pin: Optional[str] = None) -> dict:
    """解锁设备 / Unlock device.

    通过滑动或 PIN 解锁屏幕。pin 不为空时输入 PIN 解锁。
    Unlocks screen via swipe or PIN. If pin is provided, enters PIN to unlock.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        pin: 可选 PIN 码，用于 PIN/密码锁 / Optional PIN for PIN/password lock

    Returns / 返回值:
        dict: driver.unlock 的结果 / Driver result
    """
    return ctx.driver.unlock(pin=pin)


# ---------------------------------------------------------------------------
# raw_uia / dump_ui — UIAutomator 逃生通道
# ---------------------------------------------------------------------------

def dump_ui(ctx: ScriptContext) -> str:
    """获取当前 UI 层级树原始字符串。
    Get raw UI hierarchy (XML/JSON) string.

    UIAutomator 逃生通道：当 find_text/find_image 无法覆盖时，可 dump 后人工分析。
    Escape hatch: when find_text/find_image can't help, dump for manual analysis.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context

    Returns / 返回值:
        str: UI 层级原始输出（平台相关格式）/ Raw UI hierarchy output
    """
    return ctx.driver.ui.dump_ui_hierarchy()


def raw_uia(
    ctx: ScriptContext,
    selector: str,
    *,
    by: str = "text",
) -> Optional[UIElement]:
    """通过原始 UIAutomator 属性查找单个元素。
    Find single element by raw UIAutomator attribute.

    UIAutomator 逃生通道。当 find_text/find_image 无法覆盖时使用。
    Escape hatch when high-level APIs don't suffice.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        selector: 属性值（如文本、resource-id）/ Attribute value to match
        by: 查找维度 / Lookup dimension:
            "text" | "resource_id" | "class_name" | "desc"

    Returns / 返回值:
        UIElement: 找到的元素，可 .tap() / Found element
        None: 未找到 / None if not found

    Notes / 说明:
        匹配规则为包含（selector in attr），非精确。
        Match is contains (selector in attr), not exact.
    """
    nodes = ctx.driver.ui.dump_ui_nodes()
    for node in nodes:
        val: Optional[str] = None
        if by == "text":
            val = getattr(node, "text", None)
        elif by == "resource_id":
            val = getattr(node, "resource_id", None)
        elif by == "class_name":
            val = getattr(node, "class_name", None)
        elif by == "desc":
            val = getattr(node, "desc", None) or getattr(node, "content_desc", None)

        if val is not None and selector in str(val):
            # Convert UINode → UIElement
            elem = UIElement(
                x=getattr(node, "x", None),
                y=getattr(node, "y", None),
                width=getattr(node, "width", None),
                height=getattr(node, "height", None),
                desc=getattr(node, "desc", None) or getattr(node, "content_desc", None),
                resource_id=getattr(node, "resource_id", None),
                clickable=getattr(node, "clickable", None),
                enabled=getattr(node, "enabled", None),
                kind=getattr(node, "class_name", None),
            )
            # Attach ops so tap/swipe work
            elem._ops = _build_element_ops(ctx)
            if hasattr(node, "text"):
                from phone_pilot.core.ui.element import TextElement
                elem = TextElement(
                    x=elem.x, y=elem.y, width=elem.width, height=elem.height,
                    desc=elem.desc, resource_id=elem.resource_id,
                    clickable=elem.clickable, enabled=elem.enabled,
                    kind=elem.kind, _ops=elem._ops,
                    text=getattr(node, "text", None),
                )
            return elem
    return None


def _build_element_ops(ctx: ScriptContext) -> dict:
    """为 raw_uia 创建的 UIElement 构建基本 ops。"""
    ops: dict = {}
    try:
        ops["tap_xy"] = lambda x, y: ctx.driver.input.tap(x, y)
    except Exception:
        pass
    try:
        ops["swipe"] = lambda x1, y1, x2, y2, **kw: ctx.driver.input.swipe(x1, y1, x2, y2, **kw)
    except Exception:
        pass
    try:
        ops["screenshot"] = lambda name: screenshot(ctx, name)
    except Exception:
        pass
    return ops
