"""Tests for driver.shell() API — arbitrary device shell command execution."""

from __future__ import annotations

from unittest.mock import MagicMock

from phone_pilot.script_api.context import ScriptContext
from phone_pilot.script_api.actions import shell


def _make_ctx() -> ScriptContext:
    ctx = MagicMock(spec=ScriptContext)
    ctx.auto_screenshot = False
    ctx._observe = MagicMock()
    return ctx


def test_shell_executes_command_on_device():
    """shell() 将命令透传给 driver.shell()。"""
    ctx = _make_ctx()
    ctx.driver.shell.return_value = {"ok": True, "stdout": "package:com.example\n", "returncode": 0}

    result = shell(ctx, "pm list packages com.example")

    assert result["ok"] is True
    assert result["stdout"] == "package:com.example\n"
    ctx.driver.shell.assert_called_once_with("pm list packages com.example")


def test_shell_forwards_stderr_and_returncode():
    """shell() 返回完整结果包含 stderr 和 returncode。"""
    ctx = _make_ctx()
    ctx.driver.shell.return_value = {
        "ok": False,
        "stdout": "",
        "stderr": "Error: not found",
        "returncode": 1,
    }

    result = shell(ctx, "badcommand")

    assert result["ok"] is False
    assert result["stderr"] == "Error: not found"
    assert result["returncode"] == 1


def test_shell_records_observation():
    """shell() 调用后写入 RunSession observation。"""
    ctx = _make_ctx()
    ctx.driver.shell.return_value = {"ok": True, "stdout": "", "returncode": 0}

    shell(ctx, "echo test")

    ctx._observe.assert_called_once()
    args = ctx._observe.call_args
    assert "shell" in args[0][0]
