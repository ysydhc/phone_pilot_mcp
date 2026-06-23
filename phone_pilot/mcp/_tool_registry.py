"""Layered tool registry for MCP server.

Splits 64+ tools into a core set (~15, always visible) and extended
categories (~49, loaded on demand via ``phone_discover_tools``).
This reduces the default tool-description token overhead from ~10K to ~2.3K.

Environment variable ``PHONE_PILOT_LOAD_ALL_TOOLS=1`` disables layering
and registers every tool at startup (backward-compatible mode).
"""

from __future__ import annotations

import os
import threading
from typing import Any

# ---------------------------------------------------------------------------
# Core tools — always registered, cover ~90 % of common workflows
# ---------------------------------------------------------------------------

CORE_TOOLS: frozenset[str] = frozenset(
    {
        # Page state (LLM-first)
        "phone_get_page_state",
        "phone_tap_element",
        # High-frequency interaction
        "phone_tap_text",
        "phone_tap",
        "phone_swipe",
        "phone_input_text",
        # Screenshot
        "phone_screenshot",
        # Device discovery
        "phone_list_devices",
        # App lifecycle
        "phone_launch_app",
        "phone_force_stop",
        # Navigation
        "phone_go_back",
        "phone_go_home",
        # Smart find
        "phone_smart_find",
        # Script execution
        "phone_run_script",
        # Meta-tool (self)
        "phone_discover_tools",
    }
)

# ---------------------------------------------------------------------------
# Extended categories — loaded on demand
# ---------------------------------------------------------------------------

EXTENDED_CATEGORIES: dict[str, list[str]] = {
    "device": [
        "phone_get_screen_size",
        "phone_get_device_info",
        "phone_build_device_profile",
        "phone_export_device_info_md",
    ],
    "input": [
        "phone_long_press",
        "phone_keyevent",
    ],
    "ui_find": [
        "phone_find_element",
        "phone_find_image",
        "phone_ocr_find",
        "phone_scroll_to_find",
        "phone_wait_for_element",
        "phone_dismiss_popup",
        "phone_get_current_activity",
    ],
    "app": [
        "phone_clear_data",
        "phone_clear_background",
        "phone_unlock",
        "phone_open_deeplink",
        "phone_launch_from_home",
        "phone_install_app",
        "phone_uninstall_app",
        "phone_list_packages",
    ],
    "recording": [
        "phone_start_recording",
        "phone_recording_status",
        "phone_stop_recording",
        "phone_compare_screenshot",
        "phone_checkpoint_save",
        "phone_checkpoint_diff",
    ],
    "logcat": [
        "phone_start_logcat",
        "phone_stop_logcat",
        "phone_search_logcat",
        "phone_logcat_since",
        "phone_logcat_assert_sequence",
    ],
    "advanced": [
        "phone_verify",
        "phone_memory_snapshot",
        "phone_memory_check_leak",
        "phone_pull_file",
        "phone_push_file",
        "phone_read_clipboard",
        "phone_get_notifications",
        "phone_toggle_wifi",
        "phone_toggle_airplane",
        "phone_execute_shell",
        "phone_res_add",
        "phone_res_update",
        "phone_res_delete",
        "phone_res_get",
        "phone_res_list",
        "phone_res_resolve",
        "phone_recordings_list",
        "phone_recordings_get",
        "phone_replay_recording",
        "phone_push_and_run_monkey",
    ],
    "script_record": [
        "phone_record_script_start",
        "phone_record_script_status",
        "phone_record_script_stop",
        "phone_replay_trace",
    ],
    "mock": [
        "phone_mock_start",
        "phone_mock_stop",
        "phone_mock_set_rule",
        "phone_mock_load_seed",
        "phone_mock_get_rules",
        "phone_mock_delete_rule",
        "phone_mock_get_requests",
        "phone_mock_status",
        "phone_mock_assert_switched",
    ],
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "device": "Device info: screen size, device details, device profile export / 设备信息：屏幕尺寸、设备详情、设备画像导出",
    "input": "Advanced input: long press, key events / 高级输入：长按、按键事件",
    "ui_find": "UI lookup: find element, image match, OCR, scroll-to-find, wait, dismiss popup / UI 查找：元素查找、图片匹配、OCR、滚动查找、等待、关弹窗",
    "app": "App management: clear data, unlock, deeplink, install/uninstall, list packages / 应用管理：清数据、解锁、deeplink、安装卸载、包列表",
    "recording": "Screen recording & checkpoints: start/stop recording, screenshot diff, checkpoint save/diff / 录屏与检查点",
    "logcat": "Log capture & assertions: start/stop/search, since-timestamp, ordered sequence assert / 日志捕获与断言：启停搜索、时间点过滤、有序序列验证",
    "advanced": "Advanced: verify, memory analysis, file transfer, clipboard, notifications, network, shell, resources, monkey / 高级：验证、内存、文件传输、剪贴板、通知、网络、Shell、资源、Monkey",
    "script_record": "Record an MCP session into a runnable script_api script, or replay a captured trace / 录制 MCP 操作生成 script_api 脚本，或回放 trace",
    "mock": "本地网络打桩 mock server:动态写规则、查命中、判断 App 是否切换 / Local network mock server: set rules, inspect hits, assert app switched",
}

# ---------------------------------------------------------------------------
# Runtime state — hidden tools cache
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_hidden_tools: dict[str, Any] = {}  # name -> Tool object
_active_categories: set[str] = set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_layered_mode() -> bool:
    """Return True unless PHONE_PILOT_LOAD_ALL_TOOLS=1."""
    return os.environ.get("PHONE_PILOT_LOAD_ALL_TOOLS", "").strip() not in (
        "1",
        "true",
        "yes",
    )


def setup_layered_tools(mcp_server: Any) -> None:
    """Remove extended tools from the MCP server and cache them.

    Call this **once** after all tool sub-modules have been imported.
    In non-layered mode (env var override) this is a no-op.
    """
    if not is_layered_mode():
        return

    all_extended_names: set[str] = set()
    for names in EXTENDED_CATEGORIES.values():
        all_extended_names.update(names)

    with _lock:
        for name in all_extended_names:
            tool = mcp_server._tool_manager._tools.pop(name, None)
            if tool is not None:
                _hidden_tools[name] = tool


def activate_category(mcp_server: Any, category: str) -> list[str]:
    """Re-register all tools in *category* and return their names.

    Already-active categories are returned immediately (idempotent).
    """
    names = EXTENDED_CATEGORIES.get(category, [])
    if not names:
        return []

    activated: list[str] = []
    with _lock:
        _active_categories.add(category)
        for name in names:
            tool = _hidden_tools.pop(name, None)
            if tool is not None:
                mcp_server._tool_manager._tools[name] = tool
                activated.append(name)
            elif name in mcp_server._tool_manager._tools:
                # Already active from a previous call
                activated.append(name)
    return activated


def activate_by_keyword(mcp_server: Any, keyword: str) -> list[str]:
    """Activate hidden tools whose name or description matches *keyword*."""
    kw = keyword.lower()
    activated: list[str] = []
    with _lock:
        to_restore = [
            name
            for name, tool in _hidden_tools.items()
            if kw in name.lower() or kw in (tool.description or "").lower()
        ]
        for name in to_restore:
            tool = _hidden_tools.pop(name)
            mcp_server._tool_manager._tools[name] = tool
            activated.append(name)
    return activated


def get_category_summary() -> list[dict[str, Any]]:
    """Return a summary of all extended categories for display."""
    summary: list[dict[str, Any]] = []
    with _lock:
        for cat_id, names in EXTENDED_CATEGORIES.items():
            loaded = cat_id in _active_categories
            hidden_count = sum(1 for n in names if n in _hidden_tools)
            summary.append(
                {
                    "category": cat_id,
                    "description": CATEGORY_DESCRIPTIONS.get(cat_id, ""),
                    "tool_count": len(names),
                    "loaded": loaded,
                    "tools": names,
                    "pending_count": hidden_count,
                }
            )
    return summary


def get_hidden_tool_info() -> dict[str, str]:
    """Return {name: description} for all currently hidden tools."""
    with _lock:
        return {name: tool.description or "" for name, tool in _hidden_tools.items()}
