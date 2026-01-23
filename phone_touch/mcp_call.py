#!/usr/bin/env python3
"""
Minimal stdio MCP client for calling tools exposed by the installed `mcp_android` module.

Examples:
  phone-touch-call --list-tools
  phone-touch-call android_list_devices
  phone-touch-call android_device_capture --args '{"device_serial":"ZY22J7FWVJ","out_dir":"./recordings"}'
  phone-touch-call android_record_start --args '{"name":"输入法_搜索","device_serial":"ZY22J7FWVJ"}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from android_tool.adb_utils import adb_executable


def _parse_json_arg(s: str) -> dict[str, Any]:
    s = (s or "").strip()
    if not s:
        return {}
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON for --args: {e}") from e
    if not isinstance(obj, dict):
        raise SystemExit("--args must be a JSON object (dictionary).")
    return obj


async def _run(tool_name: str | None, args: dict[str, Any], list_tools: bool) -> int:
    # Run the installed module as a stdio server.
    env = dict(os.environ)
    # Ensure the spawned MCP server can find adb even if PATH is not inherited properly.
    env.setdefault("ANDROID_ADB_PATH", adb_executable())
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_android"],
        cwd=None,
        env=env,
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            if list_tools:
                res = await session.list_tools()
                print(json.dumps(res.model_dump(mode="json"), ensure_ascii=False, indent=2))
                return 0

            if not tool_name:
                raise SystemExit("tool_name is required unless --list-tools is set.")

            res = await session.call_tool(tool_name, arguments=args or None)
            print(json.dumps(res.model_dump(mode="json"), ensure_ascii=False, indent=2))
            return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Call MCP tools exposed by phone-touch (stdio).")
    p.add_argument("tool_name", nargs="?", help="Tool name, e.g. android_record_start")
    p.add_argument("--args", default="{}", help="JSON object of tool arguments")
    p.add_argument("--list-tools", action="store_true", help="List available tools and exit")
    ns = p.parse_args(argv)
    args = _parse_json_arg(ns.args)
    return anyio.run(_run, ns.tool_name, args, ns.list_tools)


if __name__ == "__main__":
    raise SystemExit(main())


