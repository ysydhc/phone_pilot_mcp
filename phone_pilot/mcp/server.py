#!/usr/bin/env python3
"""
Unified MCP Server for Multi-Platform Mobile Automation.
统一 MCP 服务器 - 多平台移动自动化。

This module creates the FastMCP instance and registers all phone_* tools
by importing the tool sub-modules. Each sub-module registers its tools
via ``@mcp.tool()`` decorators on import.

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
"""

from __future__ import annotations

import pathlib
import sys

from contextlib import asynccontextmanager


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

# ---------------------------------------------------------------------------
# MCP server instance — shared by all tool sub-modules
# ---------------------------------------------------------------------------

mcp = FastMCP("phone_pilot")

# ---------------------------------------------------------------------------
# Install the recording hook BEFORE tool modules are imported, so every
# @mcp.tool() registration is wrapped to report calls to the recorder.
# ---------------------------------------------------------------------------

from phone_pilot.mcp._recorder import install_recording_hook  # noqa: E402
from phone_pilot.mcp._cache_hook import install_cache_invalidation_hook  # noqa: E402

install_recording_hook(mcp)
install_cache_invalidation_hook(mcp)

# ---------------------------------------------------------------------------
# Re-export helpers for backward compatibility (used by __init__.py)
# ---------------------------------------------------------------------------

from phone_pilot.mcp._helpers import get_driver, get_skills  # noqa: E402, F401

# Re-export state for backward compatibility (tests access these on server module)
from phone_pilot.mcp._state import (  # noqa: E402, F401
    _state_lock,
    _recording_state,
    _recording_driver_cache,
    _recording_status,
    _logcat_state,
)

# ---------------------------------------------------------------------------
# Import tool sub-modules — each registers its @mcp.tool() on import.
# Star imports re-export tool functions for backward compatibility
# (tests and external code may import them from phone_pilot.mcp.server).
# ---------------------------------------------------------------------------

from phone_pilot.mcp._device_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._recording_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._nav_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._input_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._ui_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._image_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._app_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._resource_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._verify_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._logcat_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._script_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._system_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._page_state_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._script_record_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._discovery_tools import *  # noqa: E402, F401, F403
from phone_pilot.mcp._mock_tools import *  # noqa: E402, F401, F403

# ---------------------------------------------------------------------------
# Layered tool loading — hide extended tools to reduce agent context overhead.
# Set PHONE_PILOT_LOAD_ALL_TOOLS=1 to disable and register all tools.
# ---------------------------------------------------------------------------

from phone_pilot.mcp._tool_registry import setup_layered_tools  # noqa: E402

setup_layered_tools(mcp)

# ---------------------------------------------------------------------------
# Server Entry Point
# ---------------------------------------------------------------------------


def _patch_stdio_skip_empty_lines():
    """
    Patch MCP SDK stdio to skip empty lines before JSON parsing.

    Some clients (e.g. Cursor) send bare newlines on stdio; the SDK's
    JSONRPCMessage.model_validate_json(line) then fails with
    "Invalid JSON: EOF while parsing a value at line 2 column 0".
    Skipping empty/whitespace-only lines avoids the error and the noisy logs.
    """
    import mcp.server.stdio as stdio_mod

    if getattr(stdio_mod, "_phone_pilot_stdio_patched", False):
        return

    @asynccontextmanager
    async def _stdio_server_patched(stdin=None, stdout=None):
        import sys
        from io import TextIOWrapper

        import anyio
        import anyio.lowlevel

        import mcp.types as types
        from mcp.shared.message import SessionMessage

        if stdin is None:
            stdin = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8"))
        if stdout is None:
            stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        async def stdin_reader():
            try:
                async with read_stream_writer:
                    async for line in stdin:
                        if not line or not line.strip():
                            continue
                        try:
                            message = types.JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:
                            await read_stream_writer.send(exc)
                            continue
                        session_message = SessionMessage(message)
                        await read_stream_writer.send(session_message)
            except anyio.ClosedResourceError:
                await anyio.lowlevel.checkpoint()

        async def stdout_writer():
            try:
                async with write_stream_reader:
                    async for session_message in write_stream_reader:
                        json_str = session_message.message.model_dump_json(
                            by_alias=True, exclude_none=True
                        )
                        await stdout.write(json_str + "\n")
                        await stdout.flush()
            except anyio.ClosedResourceError:
                await anyio.lowlevel.checkpoint()

        async with anyio.create_task_group() as tg:
            tg.start_soon(stdin_reader)
            tg.start_soon(stdout_writer)
            yield read_stream, write_stream

    stdio_mod.stdio_server = _stdio_server_patched
    stdio_mod._phone_pilot_stdio_patched = True


def run_server(transport: str = "stdio"):
    """Run the MCP server."""
    if transport == "stdio":
        _patch_stdio_skip_empty_lines()
    mcp.run(transport)


if __name__ == "__main__":
    run_server()
