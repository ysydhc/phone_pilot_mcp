"""Pytest fixtures for phone_pilot MCP tools tests.

MCP 工具测试的 pytest 配置 / Pytest configuration for MCP tools tests.
"""

import pytest


@pytest.fixture(autouse=True)
def clean_state():
    """每个测试前后清理 MCP 模块级状态 / Clean module-level state before and after each test."""
    from phone_pilot.mcp._state import _recording_state, _logcat_state

    _recording_state.clear()
    _logcat_state.clear()
    yield
    _recording_state.clear()
    _logcat_state.clear()
