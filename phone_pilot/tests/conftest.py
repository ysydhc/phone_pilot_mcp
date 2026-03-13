"""Pytest fixtures for phone_pilot MCP tools tests.

MCP 工具测试的 pytest 配置 / Pytest configuration for MCP tools tests.
"""

import pytest


@pytest.fixture(autouse=True)
def clean_state():
    """每个测试前后清理 MCP 模块级状态 / Clean module-level state before and after each test."""
    from phone_pilot.mcp import server as mcp_server

    mcp_server._recording_state.clear()
    mcp_server._logcat_state.clear()
    yield
    mcp_server._recording_state.clear()
    mcp_server._logcat_state.clear()
