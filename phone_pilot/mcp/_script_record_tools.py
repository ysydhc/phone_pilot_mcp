"""Script-recording control tools for the phone_pilot MCP server.

Lets an agent capture an interactive MCP session and turn it into a runnable
script_api script, or replay the captured trace step by step.

- phone_record_script_start: begin recording subsequent action-tool calls
- phone_record_script_stop:  stop and return trace + generated script_api code
- phone_record_script_status: report whether recording is active + step count
- phone_replay_trace: re-execute a previously captured trace on a device
"""

from __future__ import annotations

import asyncio
import importlib

from phone_pilot.mcp.server import mcp
from phone_pilot.mcp import _recorder
from phone_pilot.mcp._helpers import _resolve_device_serial


@mcp.tool()
async def phone_record_script_start() -> dict:
    """开始录制后续的"操作类" MCP 调用，用于生成 script_api 脚本。
    Start recording subsequent action-tool MCP calls for script generation.

    录制白名单内的操作工具（tap/swipe/input/launch 等），查询类工具
    （如 phone_get_page_state）不录入，使生成脚本只含有意义的步骤。
    Records whitelisted action tools only; pure queries are excluded.

    Returns / 返回值:
        dict: {"ok": True, "recording": True}
    """
    return _recorder.start()


@mcp.tool()
async def phone_record_script_status() -> dict:
    """查询录制状态与已捕获步数。
    Report recording status and captured step count.

    Returns / 返回值:
        dict: {"ok": True, "recording": bool, "step_count": int}
    """
    trace = _recorder.get_trace()
    return {
        "ok": True,
        "recording": _recorder.is_recording(),
        "step_count": len(trace),
    }


@mcp.tool()
async def phone_record_script_stop(save_path: str = "") -> dict:
    """停止录制并返回 trace 与生成的 script_api 代码。
    Stop recording and return the trace plus generated script_api code.

    Parameters / 参数:
        save_path: 可选，将生成脚本写入该路径 / Optional path to write the script

    Returns / 返回值:
        dict: {"ok": True, "recording": False, "step_count": int,
               "trace": [...], "script": str, "saved_to": str|None}
    """
    result = _recorder.stop()
    saved_to = None
    if save_path:
        try:
            import pathlib

            p = pathlib.Path(save_path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(result["script"], encoding="utf-8")
            saved_to = str(p)
        except Exception as e:
            result["save_error"] = str(e)
    result["saved_to"] = saved_to
    return result


def _resolve_tool_fn(tool_name: str):
    """Look up a registered tool's async function by name on the server module."""
    server_mod = importlib.import_module("phone_pilot.mcp.server")
    fn = getattr(server_mod, tool_name, None)
    return fn if callable(fn) else None


@mcp.tool()
async def phone_replay_trace(
    trace: list[dict],
    device_serial: str = "",
    platform: str = "auto",
    stop_on_error: bool = True,
    step_delay_s: float = 0.5,
) -> dict:
    """回放一段之前录制的 trace（按顺序重新执行每个操作）。
    Replay a previously captured trace by re-executing each step in order.

    Parameters / 参数:
        trace: phone_record_script_stop 返回的 trace 列表 / Trace list
        device_serial: 目标设备，空则自动检测 / Target device, auto if empty
        platform: 平台类型 / Platform type
        stop_on_error: 某步失败是否停止，默认 True / Stop on first failed step
        step_delay_s: 步骤间延迟秒数 / Delay between steps

    Returns / 返回值:
        dict: {"ok": bool, "executed": int, "total": int,
               "results": [{step, tool, ok, error?}], "stopped_early": bool}
    """
    resolved, plat, err = _resolve_device_serial(device_serial or None, platform)
    if err:
        return err

    results: list[dict] = []
    stopped_early = False
    executed = 0

    for i, entry in enumerate(trace or []):
        tool_name = entry.get("tool", "")
        args = dict(entry.get("args", {}) or {})
        fn = _resolve_tool_fn(tool_name)
        if fn is None:
            results.append(
                {"step": i, "tool": tool_name, "ok": False, "error": "tool_not_found"}
            )
            if stop_on_error:
                stopped_early = True
                break
            continue

        # Inject the resolved device for this replay run.
        args.setdefault("device_serial", resolved)

        try:
            res = await fn(**args)
            ok = bool(res.get("ok", True)) if isinstance(res, dict) else True
            entry_result = {"step": i, "tool": tool_name, "ok": ok}
            if not ok and isinstance(res, dict):
                entry_result["error"] = res.get("error")
        except Exception as e:
            entry_result = {"step": i, "tool": tool_name, "ok": False, "error": str(e)}
            ok = False

        results.append(entry_result)
        executed += 1
        if not ok and stop_on_error:
            stopped_early = True
            break
        if step_delay_s > 0:
            await asyncio.sleep(step_delay_s)

    return {
        "ok": not stopped_early,
        "executed": executed,
        "total": len(trace or []),
        "results": results,
        "stopped_early": stopped_early,
        "device_serial": resolved,
        "platform": plat,
    }
