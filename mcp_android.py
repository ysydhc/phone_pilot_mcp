#!/usr/bin/env python3
"""
Cursor MCP entrypoint.

This file is intentionally kept small for readability.
The full MCP implementation (all tools + helpers) lives in:
  `android_tool/mcp_server.py`

MCP / Cursor expects this module to export a FastMCP instance named `mcp`.
"""

from __future__ import annotations

# IMPORTANT:
# `mcp dev mcp_android.py` will look for a FastMCP server object exported as `mcp`.
# Do not `import mcp` in this module, otherwise `mcp_android.py:mcp` becomes the `mcp` module
# (not a FastMCP instance) and the CLI will error.
from android_tool.mcp_server import mcp


if __name__ == "__main__":
    # For Cursor / MCP clients: run as a stdio server:
    #   uv run mcp_android.py
    mcp.run(transport="stdio")


