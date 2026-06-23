"""MCP 操作录制 → script_api 代码生成 / 回放。
MCP action recording → script_api code generation / replay.

把"用 MCP 工具探索操作"的过程固化为可执行的 script_api 脚本或可回放的 trace，
消除"探索用 MCP、执行重写 script_api"的重复劳动。
Turns an interactive MCP exploration session into a runnable script_api script
(or a replayable trace), removing the explore-then-rewrite duplication.

切点 / Hook point:
    server.py 在注册工具前对 ``mcp.tool`` 做一层包装（install_recording_hook），
    每个工具调用成功后，若录制开启且工具在白名单内，则追加一条 trace。
    server.py wraps ``mcp.tool`` before tools register; each successful call of a
    whitelisted tool appends a trace entry while recording is active.
"""

from __future__ import annotations

import functools
import inspect
import threading
import time
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Recording state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_active: bool = False
_trace: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Code generation map: MCP tool name -> (args dict) -> script_api source line.
# Only "action" tools that mutate device state are recorded; pure queries
# (e.g. phone_get_page_state) are intentionally excluded so generated scripts
# contain only meaningful steps.
# ---------------------------------------------------------------------------


def _py(val: Any) -> str:
    """Render a value as a Python literal for generated code."""
    return repr(val)


# Each generator receives the call's argument dict and returns one line of
# script_api source (without trailing newline), or None to skip.
CODEGEN: dict[str, Callable[[dict], str | None]] = {
    "phone_tap": lambda a: f"tap_xy(ctx, {int(a.get('x', 0))}, {int(a.get('y', 0))})",
    "phone_tap_text": lambda a: f"find_text(ctx, {_py(a.get('text', ''))}).tap()",
    "phone_input_text": lambda a: (
        f"type_text(ctx, {_py(a.get('text', ''))}"
        + (", enter=True)" if a.get("enter") else ")")
    ),
    "phone_keyevent": lambda a: (
        f"keyevent(ctx, {_py(a.get('keycode') or a.get('key', ''))})"
    ),
    "phone_swipe": lambda a: (
        f"swipe(ctx, {int(a.get('x1', 0))}, {int(a.get('y1', 0))}, "
        f"{int(a.get('x2', 0))}, {int(a.get('y2', 0))})"
    ),
    "phone_long_press": lambda a: (
        f"ctx.driver.input.long_press({int(a.get('x', 0))}, {int(a.get('y', 0))})"
    ),
    "phone_launch_app": lambda a: f"restart_app_pkg(ctx, {_py(a.get('package', ''))})",
    "phone_force_stop": lambda a: (
        f"ctx.driver.app.force_stop({_py(a.get('package', ''))})"
    ),
    "phone_go_back": lambda a: "ctx.driver.go_back()",
    "phone_go_home": lambda a: "reset_home_screen(ctx)",
    "phone_clear_background": lambda a: "clear_background(ctx)",
    "phone_unlock": lambda a: (
        f"unlock_device(ctx, pin={_py(a['pin'])})"
        if a.get("pin")
        else "unlock_device(ctx)"
    ),
    "phone_open_deeplink": lambda a: (
        f"ctx.driver.open_deeplink({_py(a.get('uri', ''))})"
    ),
    "phone_launch_from_home": lambda a: (
        f"launch_from_home(ctx, {_py(a.get('query', ''))})"
    ),
    "phone_screenshot": lambda a: f"screenshot(ctx, {_py(a.get('name') or 'shot')})",
    "phone_scroll_to_find": lambda a: f"scroll_to_find(ctx, {_py(a.get('text', ''))})",
    "phone_wait_for_element": lambda a: (
        "wait_for_element_appear(ctx.device_serial, "
        + (
            f"query={_py(a['text'])}"
            if a.get("text")
            else f"selector={{'resource_id': {_py(a.get('resource_id', ''))}}}"
        )
        + (", require_clickable=True" if a.get("require_clickable") else "")
        + ")"
    ),
}

# The set of tools worth recording (those with a generator above).
RECORDABLE: frozenset[str] = frozenset(CODEGEN.keys())

# script_api names referenced by the generated code, for the import block.
_IMPORT_NAMES = [
    "ScriptContext",
    "run_script",
    "tap_xy",
    "find_text",
    "type_text",
    "keyevent",
    "swipe",
    "restart_app_pkg",
    "reset_home_screen",
    "clear_background",
    "unlock_device",
    "launch_from_home",
    "screenshot",
    "scroll_to_find",
    "tap_when_ready",
    "wait_for_element_appear",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_recording() -> bool:
    with _lock:
        return _active


def start() -> dict:
    """Begin a new recording session, clearing any previous trace."""
    global _active
    with _lock:
        _active = True
        _trace.clear()
    return {"ok": True, "recording": True}


def stop() -> dict:
    """Stop recording. Returns the trace and generated script."""
    global _active
    with _lock:
        _active = False
        trace = list(_trace)
    return {
        "ok": True,
        "recording": False,
        "step_count": len(trace),
        "trace": trace,
        "script": generate_script(trace),
    }


def get_trace() -> list[dict[str, Any]]:
    with _lock:
        return list(_trace)


def record_call(tool_name: str, args: dict[str, Any], ok: bool) -> None:
    """Append a trace entry if recording is active and the tool is recordable."""
    if tool_name not in RECORDABLE:
        return
    with _lock:
        if not _active:
            return
        _trace.append(
            {
                "tool": tool_name,
                "args": {k: v for k, v in args.items() if k != "device_serial"},
                "ok": bool(ok),
                "ts": time.time(),
            }
        )


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def generate_script(trace: list[dict[str, Any]] | None = None) -> str:
    """Render a trace into a runnable script_api Python script."""
    if trace is None:
        trace = get_trace()

    body_lines: list[str] = []
    for entry in trace:
        tool = entry.get("tool", "")
        args = entry.get("args", {}) or {}
        gen = CODEGEN.get(tool)
        if gen is None:
            body_lines.append(f"    # TODO: {tool}({args}) — no script_api equivalent")
            continue
        try:
            line = gen(args)
        except Exception:
            line = None
        if line:
            comment = (
                "" if entry.get("ok", True) else "  # NOTE: failed during recording"
            )
            body_lines.append(f"    {line}{comment}")

    if not body_lines:
        body_lines.append("    pass  # no recorded steps")

    imports = ",\n    ".join(_IMPORT_NAMES)
    body = "\n".join(body_lines)
    return (
        '"""Auto-generated from an MCP recording session.\n'
        "由 MCP 录制会话自动生成；可直接运行或按需修改。\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        f"from phone_pilot.script_api import (\n    {imports},\n)\n\n\n"
        "ctx = ScriptContext(auto_screenshot=True, popup_guard=True)\n\n\n"
        "def main() -> int:\n"
        f"{body}\n"
        "    return 0\n\n\n"
        'if __name__ == "__main__":\n'
        "    run_script(main, ctx=ctx)\n"
    )


# ---------------------------------------------------------------------------
# Hook installation — wraps mcp.tool() so every registered tool reports calls
# ---------------------------------------------------------------------------


def install_recording_hook(mcp_server: Any) -> None:
    """Wrap ``mcp_server.tool`` so each registered tool records its calls.

    Must be called AFTER the FastMCP instance is created and BEFORE the tool
    sub-modules are imported (so every @mcp.tool() goes through the wrapper).
    """
    original_tool = mcp_server.tool

    @functools.wraps(original_tool)
    def patched_tool(*t_args, **t_kwargs):
        decorator = original_tool(*t_args, **t_kwargs)

        def wrapping_decorator(fn):
            tool_name = getattr(fn, "__name__", "")
            # Only bother wrapping recordable tools; others register unchanged.
            if tool_name not in RECORDABLE:
                return decorator(fn)

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def async_wrapper(*a, **kw):
                    result = await fn(*a, **kw)
                    _safe_record(tool_name, kw, result)
                    return result

                return decorator(async_wrapper)

            @functools.wraps(fn)
            def sync_wrapper(*a, **kw):
                result = fn(*a, **kw)
                _safe_record(tool_name, kw, result)
                return result

            return decorator(sync_wrapper)

        return wrapping_decorator

    mcp_server.tool = patched_tool


def _safe_record(tool_name: str, kwargs: dict, result: Any) -> None:
    """Record a call without ever letting recording errors break a tool."""
    try:
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        record_call(tool_name, dict(kwargs), ok)
    except Exception:
        pass
