"""Verification & Script Tools for phone_pilot MCP server.

This module registers MCP tools for executing workflow scripts, running batch
verification assertions, and managing screen state checkpoints.

Tools:
    phone_run_script        – Execute a Python workflow script via script_api + chain.
    phone_verify            – Run batch verification assertions on the current screen.
    phone_checkpoint_save   – Save the current screen state as a named checkpoint.
    phone_checkpoint_diff   – Compare the current screen against a saved checkpoint.
"""

from __future__ import annotations

import asyncio  # noqa: F401 – available for downstream use
import json
import re  # noqa: F401 – available for downstream use
from pathlib import Path  # noqa: F401 – available for downstream use
from typing import Any, Optional  # noqa: F401 – available for downstream use

import pathlib

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp._helpers import (
    get_driver,
    _resolve_device_serial,
)


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
    install_apk: str = "",
) -> dict:
    """
    Execute a Python workflow script that uses script_api + chain.
    执行一个 Python 工作流脚本（使用 script_api + chain），返回结构化结果。

    The script must define a `main(ctx: ScriptContext) -> dict | None` function.
    脚本需定义 `main(ctx: ScriptContext) -> dict | None` 函数。

    .. note::
        phone_pilot is an automation test tool, NOT a build tool.
        It does NOT automatically compile APKs — use ``install_apk`` to install
        a pre-built APK, or build externally before running the script.
        phone_pilot 是自动化测试工具，不是构建工具。
        不会自动编译 APK。使用 ``install_apk`` 安装预构建的 APK，
        或在外部构建后再执行脚本。

    Args:
        path: Python script file path. 脚本文件路径。
        device_serial: Device serial (empty = auto-detect). 设备序列号。
        platform: Platform ("android"|"harmony"|"auto"). 平台。
        timeout_s: Execution timeout in seconds. 执行超时秒数。
        repo_path: Git repo path for git diff tracking. 被测项目 git 仓库路径（仅记录 diff，不触发构建）。
        change_description: Optional change description from agent. Agent 变更说明。
        install_apk: Optional APK path to install before running the script.
            运行脚本前先安装此 APK。空则跳过。

    Returns:
        {ok, result, stdout, elapsed_ms, git_diff?, apk_installed?, error?}
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

    # Install APK if requested
    apk_installed: dict | None = None
    if install_apk:
        from phone_pilot.mcp._script_tools import _phone_install_app_sync

        install_result = _phone_install_app_sync(serial, plat, install_apk)
        if not install_result.get("ok"):
            return {
                "ok": False,
                "error": f"install_apk failed: {install_result.get('error', 'unknown')}",
                "install_result": install_result,
            }
        apk_installed = {"ok": True, "apk_path": str(install_apk)}

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

    # Git diff tracking (renamed from code_changes for clarity)
    git_diff = None
    if repo_path:
        from phone_pilot.core.code_changes import capture_code_changes

        git_diff = capture_code_changes(
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
    if git_diff:
        response["git_diff"] = git_diff
        # Backward-compat alias
        response["code_changes"] = git_diff
    if apk_installed:
        response["apk_installed"] = apk_installed

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
