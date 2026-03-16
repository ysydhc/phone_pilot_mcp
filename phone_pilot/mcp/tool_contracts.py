"""MCP 工具响应契约 / MCP Tool Response Contracts.

定义 62 个 phone_* 工具成功/失败时返回 dict 的必含键。
Defines required keys for success/failure responses of 62 phone_* tools.

校验对象为工具函数返回的 dict，非 MCP 协议层。
Validation target is the tool-returned dict, not MCP wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# ToolContract dataclass
# ---------------------------------------------------------------------------


@dataclass
class ToolContract:
    """工具响应契约：定义成功/失败时必须包含的键。
    Tool response contract: defines required keys for success/failure responses.

    校验对象为工具函数返回的 dict，非 MCP 协议层。
    Validation target is the tool-returned dict, not MCP wire format.
    """

    tool_name: str
    category: str  # "A" | "B" | "C"
    required_keys_when_ok: list[str]  # 成功时 ok=True 必含键 / Required keys when ok=True
    required_keys_when_error: list[str]  # 失败时 ok=False 必含键，至少 ["ok","error"]
    optional_keys: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Contract registry / 契约注册表
# ---------------------------------------------------------------------------

_CONTRACTS: dict[str, ToolContract] = {}


def _register(c: ToolContract) -> None:
    _CONTRACTS[c.tool_name] = c


# ---------------------------------------------------------------------------
# 62 Tool Contracts (按 server.py 顺序)
# ---------------------------------------------------------------------------

# A: 无需 UI / No UI needed
# B: 需要 UI / Needs UI (screenshot, display, visual)
# C: 难标准化 / Hard to standardize

_register(
    ToolContract(
        "phone_list_devices",
        "A",
        required_keys_when_ok=["ok", "devices"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_build_device_profile",
        "A",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_export_device_info_md",
        "A",
        required_keys_when_ok=["ok", "md_path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_id", "device_serial"],
    )
)

_register(
    ToolContract(
        "phone_screenshot",
        "B",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform", "screenshot_path", "screenshot_base64"],
    )
)  # 成功时必有 screenshot_path 或 screenshot_base64 之一 / Either when ok

_register(
    ToolContract(
        "phone_get_screen_size",
        "A",
        required_keys_when_ok=["ok", "width", "height"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_start_recording",
        "B",
        required_keys_when_ok=["ok", "local_path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_stop_recording",
        "B",
        required_keys_when_ok=["ok", "local_path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_go_home",
        "B",
        required_keys_when_ok=["ok", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_go_back",
        "B",
        required_keys_when_ok=["ok", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_unlock",
        "B",
        required_keys_when_ok=["ok", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_clear_background",
        "B",
        required_keys_when_ok=["ok", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_clear_data",
        "A",
        required_keys_when_ok=["ok", "package"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_open_deeplink",
        "B",
        required_keys_when_ok=["ok", "uri"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_tap",
        "B",
        required_keys_when_ok=["ok", "x", "y", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_swipe",
        "B",
        required_keys_when_ok=["ok", "x1", "y1", "x2", "y2", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_long_press",
        "B",
        required_keys_when_ok=["ok", "x", "y", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_input_text",
        "B",
        required_keys_when_ok=["ok", "text", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_keyevent",
        "B",
        required_keys_when_ok=["ok", "key", "platform"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_find_element",
        "B",
        required_keys_when_ok=["ok", "count", "elements"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_get_current_activity",
        "A",
        required_keys_when_ok=["ok", "package", "activity"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_find_image",
        "B",
        required_keys_when_ok=["ok", "count", "matches"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform", "template_path"],
    )
)

_register(
    ToolContract(
        "phone_ocr_find",
        "B",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform", "query", "boxes_count", "matches_count", "matches"],
    )
)

_register(
    ToolContract(
        "phone_launch_app",
        "B",
        required_keys_when_ok=["ok", "package"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_force_stop",
        "B",
        required_keys_when_ok=["ok", "package"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_list_packages",
        "A",
        required_keys_when_ok=["ok", "count", "packages"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_compare_screenshot",
        "B",
        required_keys_when_ok=["ok", "match", "similarity", "threshold"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform", "baseline_path"],
    )
)

_register(
    ToolContract(
        "phone_res_add",
        "A",
        required_keys_when_ok=["ok", "record"],
        required_keys_when_error=["ok", "error"],
        optional_keys=[],
    )
)

_register(
    ToolContract(
        "phone_res_update",
        "A",
        required_keys_when_ok=["ok", "record"],
        required_keys_when_error=["ok", "error"],
        optional_keys=[],
    )
)

_register(
    ToolContract(
        "phone_res_delete",
        "A",
        required_keys_when_ok=["ok", "deleted"],
        required_keys_when_error=["ok", "error"],
        optional_keys=[],
    )
)

_register(
    ToolContract(
        "phone_res_get",
        "A",
        required_keys_when_ok=["ok", "record"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["key"],
    )
)

_register(
    ToolContract(
        "phone_res_list",
        "A",
        required_keys_when_ok=["ok", "records", "count"],
        required_keys_when_error=["ok", "error"],
        optional_keys=[],
    )
)

_register(
    ToolContract(
        "phone_res_resolve",
        "A",
        required_keys_when_ok=["ok", "value", "resolved", "cached"],
        required_keys_when_error=["ok", "error"],
        optional_keys=[],
    )
)

_register(
    ToolContract(
        "phone_recordings_list",
        "A",
        required_keys_when_ok=["ok", "recordings", "count"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["out_dir"],
    )
)

_register(
    ToolContract(
        "phone_recordings_get",
        "C",  # 返回结构依赖 load_recording_by_key，可变 / Variable structure from load_recording_by_key
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["recording_id", "name", "path", "mks_data"],
    )
)

_register(
    ToolContract(
        "phone_replay_recording",
        "B",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "recording_key", "speed", "run_result"],
    )
)

_register(
    ToolContract(
        "phone_push_and_run_monkey",
        "B",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "script_path"],
    )
)

_register(
    ToolContract(
        "phone_run_script",
        "C",  # 复杂返回：result/stdout/elapsed_ms/code_changes / Complex: result, stdout, code_changes
        required_keys_when_ok=["ok", "result", "stdout", "elapsed_ms"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["code_changes"],
    )
)

_register(
    ToolContract(
        "phone_verify",
        "C",  # 多维度断言结果，结构复杂 / Multi-dimensional assertions, complex structure
        required_keys_when_ok=["ok", "passed", "failed", "total", "results"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["evidence_dir", "code_changes"],
    )
)

_register(
    ToolContract(
        "phone_checkpoint_save",
        "B",
        required_keys_when_ok=["ok", "name", "path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["screenshot_path", "ui_texts", "activity"],
    )
)

_register(
    ToolContract(
        "phone_checkpoint_diff",
        "B",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["similarity", "text_added", "text_removed", "activity_changed", "diff_screenshot_path"],
    )
)

_register(
    ToolContract(
        "phone_start_logcat",
        "A",
        required_keys_when_ok=["ok", "path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["pid", "device_serial"],
    )
)

_register(
    ToolContract(
        "phone_stop_logcat",
        "A",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["lines", "device_serial"],
    )
)

_register(
    ToolContract(
        "phone_search_logcat",
        "A",
        required_keys_when_ok=["ok", "matches", "count"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_scroll_to_find",
        "B",
        required_keys_when_ok=["ok", "center", "bounds", "text"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_wait_for_element",
        "B",
        required_keys_when_ok=["ok", "found"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["element", "device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_dismiss_popup",
        "B",
        required_keys_when_ok=["ok", "dismissed"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["detail", "device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_smart_find",
        "B",
        required_keys_when_ok=["ok", "center", "bounds", "text"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_launch_from_home",
        "B",
        required_keys_when_ok=["ok"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_install_app",
        "A",
        required_keys_when_ok=["ok", "apk_path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_uninstall_app",
        "A",
        required_keys_when_ok=["ok", "package"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_memory_snapshot",
        "A",
        required_keys_when_ok=["ok", "summary"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_memory_check_leak",
        "C",  # 泄漏检测结果结构可变 / Leak detection result structure varies
        required_keys_when_ok=["ok", "leaked"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["detail", "device_serial"],
    )
)

_register(
    ToolContract(
        "phone_pull_file",
        "A",
        required_keys_when_ok=["ok", "local_path", "remote_path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_push_file",
        "A",
        required_keys_when_ok=["ok", "local_path", "remote_path"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_read_clipboard",
        "A",
        required_keys_when_ok=["ok", "text"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_get_notifications",
        "A",
        required_keys_when_ok=["ok", "notifications"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_toggle_wifi",
        "A",
        required_keys_when_ok=["ok", "wifi_on"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_toggle_airplane",
        "A",
        required_keys_when_ok=["ok", "airplane_on"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_execute_shell",
        "C",
        required_keys_when_ok=["ok", "stdout", "stderr", "returncode"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial"],
    )
)

_register(
    ToolContract(
        "phone_get_device_info",
        "B",
        required_keys_when_ok=["ok", "screen", "activity"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["model", "os_version", "device_serial"],
    )
)

_register(
    ToolContract(
        "phone_get_page_state",
        "C",  # 复杂：elements/scrollable_areas/all_texts/screenshot 等 / Complex: elements, screenshots
        required_keys_when_ok=["ok", "activity", "screen", "elements", "element_count", "scrollable_areas", "all_texts"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["screenshot_base64", "screenshot_annotated_base64", "device_serial", "platform"],
    )
)

_register(
    ToolContract(
        "phone_tap_element",
        "B",
        required_keys_when_ok=["ok", "index", "center", "label", "type"],
        required_keys_when_error=["ok", "error"],
        optional_keys=["device_serial", "platform"],
    )
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_contract(tool_name: str) -> ToolContract | None:
    """获取指定工具的契约 / Get contract for a tool."""
    return _CONTRACTS.get(tool_name)


def get_all_contracts() -> dict[str, ToolContract]:
    """获取全部契约 / Get all contracts."""
    return dict(_CONTRACTS)


def validate_response(tool_name: str, response: dict) -> tuple[bool, list[str]]:
    """校验工具返回 dict 是否满足契约。
    Validate tool-returned dict against contract.

    Returns:
        (True, []) 通过 / Pass
        (False, list_of_missing_keys) 不通过 / Fail with missing keys
    """
    contract = get_contract(tool_name)
    if contract is None:
        return (False, [f"no_contract_for_{tool_name}"])
    if not isinstance(response, dict):
        return (False, ["response_not_dict"])
    ok = response.get("ok")
    if ok is True:
        required = contract.required_keys_when_ok
    elif ok is False:
        required = contract.required_keys_when_error
    else:
        return (False, ["ok"])
    missing = [k for k in required if k not in response]
    return (True, []) if not missing else (False, missing)
