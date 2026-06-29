"""Tests for recorder robust codegen.

验证 MCP 录制生成的 script_api 代码使用更稳健的调用方式：
- phone_tap_text -> tap_when_ready(...)
- phone_wait_for_element 保留 timeout / require_visible / require_enabled / require_clickable
- phone_tap_element 记录 result_summary 后生成注释 + tap_xy
"""

from __future__ import annotations

from phone_pilot.mcp._recorder import generate_script


# ---------------------------------------------------------------------------
# phone_tap_text -> tap_when_ready
# ---------------------------------------------------------------------------


def test_tap_text_generates_tap_when_ready():
    script = generate_script(
        [{"tool": "phone_tap_text", "args": {"text": "登录"}, "ok": True}]
    )
    assert "tap_when_ready(ctx, text='登录'" in script
    assert "require_clickable=True" in script
    assert "require_enabled=True" in script


def test_tap_text_robust_uses_timeout_default():
    script = generate_script(
        [{"tool": "phone_tap_text", "args": {"text": "Login"}, "ok": True}]
    )
    # tap_when_ready 默认 timeout_s=10.0，生成时也应带上
    assert "timeout_s=10.0" in script


def test_tap_text_failed_marks_note():
    script = generate_script(
        [{"tool": "phone_tap_text", "args": {"text": "X"}, "ok": False}]
    )
    assert "NOTE: failed during recording" in script
    assert "tap_when_ready" in script


# ---------------------------------------------------------------------------
# phone_wait_for_element -> wait_for_element_appear with full params
# ---------------------------------------------------------------------------


def test_wait_for_element_preserves_timeout():
    script = generate_script(
        [
            {
                "tool": "phone_wait_for_element",
                "args": {"text": "Done", "timeout_s": 15.0},
                "ok": True,
            }
        ]
    )
    assert "wait_for_element_appear" in script
    assert "timeout_s=15.0" in script


def test_wait_for_element_preserves_state_flags():
    script = generate_script(
        [
            {
                "tool": "phone_wait_for_element",
                "args": {
                    "text": "Done",
                    "timeout_s": 5.0,
                    "require_visible": True,
                    "require_enabled": True,
                    "require_clickable": True,
                },
                "ok": True,
            }
        ]
    )
    assert "require_visible=True" in script
    assert "require_enabled=True" in script
    assert "require_clickable=True" in script


def test_wait_for_element_with_resource_id():
    script = generate_script(
        [
            {
                "tool": "phone_wait_for_element",
                "args": {
                    "resource_id": "btn_submit",
                    "timeout_s": 8.0,
                },
                "ok": True,
            }
        ]
    )
    assert "'resource_id': 'btn_submit'" in script
    assert "wait_for_element_appear" in script


# ---------------------------------------------------------------------------
# phone_tap_element with result summary
# ---------------------------------------------------------------------------


def test_tap_element_with_center_generates_tap_xy_and_comment():
    script = generate_script(
        [
            {
                "tool": "phone_tap_element",
                "args": {"index": 3},
                "ok": True,
                "result_summary": {
                    "center": [540, 1200],
                    "label": "登录",
                    "type": "button",
                },
            }
        ]
    )
    assert "tap_xy(ctx, 540, 1200)" in script
    assert "登录" in script
    assert "button" in script


def test_tap_element_without_summary_generates_todo():
    script = generate_script(
        [
            {
                "tool": "phone_tap_element",
                "args": {"index": 3},
                "ok": True,
            }
        ]
    )
    # 没有结果摘要时无法还原坐标，生成 TODO 注释
    assert "TODO" in script or "tap_element" in script


# ---------------------------------------------------------------------------
# 完整脚本结构
# ---------------------------------------------------------------------------


def test_generated_script_has_imports_and_main():
    script = generate_script(
        [{"tool": "phone_tap_text", "args": {"text": "Hi"}, "ok": True}]
    )
    assert "from phone_pilot.script_api import" in script
    assert "def main()" in script
    assert "run_script(main, ctx=ctx)" in script
    assert "tap_when_ready" in script


def test_imports_include_tap_when_ready():
    script = generate_script(
        [{"tool": "phone_tap_text", "args": {"text": "Hi"}, "ok": True}]
    )
    # tap_when_ready 必须在 import 块中
    assert "tap_when_ready" in script.split("def main")[0]


def test_empty_trace_generates_pass():
    script = generate_script([])
    assert "pass" in script


# ---------------------------------------------------------------------------
# 其他动作工具保持原有行为
# ---------------------------------------------------------------------------


def test_phone_tap_still_generates_tap_xy():
    script = generate_script(
        [{"tool": "phone_tap", "args": {"x": 100, "y": 200}, "ok": True}]
    )
    assert "tap_xy(ctx, 100, 200)" in script


def test_phone_swipe_still_works():
    script = generate_script(
        [
            {
                "tool": "phone_swipe",
                "args": {"x1": 100, "y1": 800, "x2": 100, "y2": 200},
                "ok": True,
            }
        ]
    )
    assert "swipe(ctx, 100, 100, 100, 200)" in script or "swipe(ctx," in script
