"""Global mutable state shared across MCP tool modules.

All access to these dicts MUST be protected by ``_state_lock``.
"""

from __future__ import annotations

import threading
from typing import Any

_state_lock = threading.Lock()

# Per-device recording paths for pull and cleanup on stop
_recording_state: dict[str, dict] = {}
# Cache driver used during recording so stop uses the same instance
_recording_driver_cache: dict[str, Any] = {}
# Async recording status for polling: device_serial -> {"status": ..., ...}
_recording_status: dict[str, dict] = {}

# Per-device logcat output path
_logcat_state: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Mock server state — single global instance
# 单实例 mock server 句柄:存储运行中的 MockServer + MockControlClient + ReverseManager
# ---------------------------------------------------------------------------

# 结构: {"server": MockServer, "client": MockControlClient,
#        "reverse": ReverseManager, "serial": str, "data_port": int}
_mock_handle: dict = {}


def get_mock_handle() -> dict:
    """Return a shallow copy of the current mock handle (thread-safe)."""
    with _state_lock:
        return dict(_mock_handle)


def set_mock_handle(handle: dict) -> None:
    """Replace the mock handle dict (thread-safe)."""
    with _state_lock:
        _mock_handle.clear()
        _mock_handle.update(handle)


def clear_mock_handle() -> None:
    """Clear the mock handle (thread-safe)."""
    with _state_lock:
        _mock_handle.clear()
