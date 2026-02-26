"""
MCP Server module - Model Context Protocol integration.

Provides unified MCP tools with phone_* prefix for multi-platform support.

Usage:
    # Run the server
    python -m phone_pilot.mcp.server
    
    # Or via mcp CLI
    mcp dev phone_pilot/mcp/server.py
"""

from phone_pilot.mcp.server import mcp, get_driver, get_skills, run_server

__all__ = ["mcp", "get_driver", "get_skills", "run_server"]
