#!/usr/bin/env python3
"""
Minimal stdio MCP client for calling tools exposed by `mcp_android.py`.

Why:
- Keep all automation flows going through @mcp.tool() so we can evaluate tool design/behavior.
- Avoid ad-hoc shell/adb side effects in the agent.

Examples:
  python scripts/mcp_call.py --list-tools
  python scripts/mcp_call.py android_list_devices
  python scripts/mcp_call.py android_device_capture --args '{"device_serial":"ZY22J7FWVJ","out_dir":"./recordings"}'
  python scripts/mcp_call.py android_record_start --args '{"name":"输入法_搜索","device_serial":"ZY22J7FWVJ"}'
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _repo_root() -> pathlib.Path:
    # scripts/mcp_call.py -> repo root
    return pathlib.Path(__file__).resolve().parent.parent


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


def _maybe_parse_tool_text_payload(call_tool_result: dict[str, Any]) -> dict[str, Any] | None:
    """
    Our server currently returns tool results as a text blob that itself is JSON.
    Try to extract and parse that JSON for nicer formatting.
    """
    try:
        content = call_tool_result.get("content") or []
        if not content:
            return None
        first = content[0]
        if (first or {}).get("type") != "text":
            return None
        text = (first or {}).get("text") or ""
        text = text.strip()
        if not text:
            return None
        return json.loads(text)
    except Exception:
        return None


def _render_apps_table(payload: dict[str, Any], *, max_rows: int = 200) -> str:
    device = payload.get("DeviceName") or payload.get("device_name") or payload.get("device_serial") or ""
    apps = payload.get("Apps") or payload.get("apps") or []
    mode = payload.get("AppNameMode")
    lines: list[str] = []
    title = f"Device: {device}" + (f" (AppNameMode={mode})" if mode else "")
    lines.append(title)
    lines.append("")
    lines.append("| # | AppName | PackageName | VersionName | VersionCode |")
    lines.append("|---:|---|---|---|---:|")
    n = 0
    for i, a in enumerate(apps, start=1):
        if max_rows and n >= max_rows:
            break
        app_name = str((a or {}).get("AppName") or "")
        pkg = str((a or {}).get("PackageName") or "")
        vname = str((a or {}).get("VersionName") or "")
        vcode = (a or {}).get("VersionCode")
        vcode_s = "" if vcode is None else str(vcode)
        # Basic escaping for pipes.
        def esc(s: str) -> str:
            return s.replace("|", "\\|")
        lines.append(f"| {i} | {esc(app_name)} | {esc(pkg)} | {esc(vname)} | {esc(vcode_s)} |")
        n += 1
    if max_rows and isinstance(apps, list) and len(apps) > max_rows:
        lines.append("")
        lines.append(f"_Showing first {max_rows} rows (total {len(apps)})._")
    return "\n".join(lines) + "\n"


async def _run(tool_name: str | None, args: dict[str, Any], list_tools: bool) -> int:
    root = _repo_root()
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(root / "mcp_android.py")],
        cwd=str(root),
        env=None,
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
            obj = res.model_dump(mode="json")
            payload = _maybe_parse_tool_text_payload(obj)
            print(json.dumps(payload or obj, ensure_ascii=False, indent=2))
            return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Call MCP tools exposed by mcp_android.py (stdio).")
    p.add_argument("tool_name", nargs="?", help="Tool name, e.g. android_record_start")
    p.add_argument("--args", default="{}", help="JSON object of tool arguments")
    p.add_argument("--list-tools", action="store_true", help="List available tools and exit")
    p.add_argument("--format", default="json", choices=["json", "table"], help="Output format for tool results")
    p.add_argument("--table-max-rows", type=int, default=200, help="Max rows when --format table")
    ns = p.parse_args(argv)

    args = _parse_json_arg(ns.args)
    if ns.list_tools or ns.format == "json":
        return anyio.run(_run, ns.tool_name, args, ns.list_tools)

    # table format: run, then re-run inside and print formatted output
    async def run_table() -> int:
        root = _repo_root()
        server = StdioServerParameters(
            command=sys.executable,
            args=[str(root / "mcp_android.py")],
            cwd=str(root),
            env=None,
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                if not ns.tool_name:
                    raise SystemExit("tool_name is required.")
                res = await session.call_tool(ns.tool_name, arguments=args or None)
                obj = res.model_dump(mode="json")
                payload = _maybe_parse_tool_text_payload(obj) or {}
                if ns.tool_name == "android_list_packages":
                    print(_render_apps_table(payload, max_rows=int(ns.table_max_rows)))
                    return 0
                # fallback: print parsed payload if any, else raw json
                print(json.dumps(payload or obj, ensure_ascii=False, indent=2))
                return 0

    return anyio.run(run_table)


if __name__ == "__main__":
    raise SystemExit(main())


