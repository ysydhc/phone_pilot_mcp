"""MCP 工具契约测试 / MCP tools contract tests.

Mock-based unit tests for MCP tools. No real device connection required.
使用 pytest + unittest.mock，验证 MCP 工具返回格式与状态管理。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _run(coro):
    """Run async coroutine in sync context."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Mock fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_resolve():
    """Mock _resolve_device_serial to return test device."""
    with patch("phone_pilot.mcp.server._resolve_device_serial") as m:
        m.return_value = ("test_serial", "android", None)
        yield m


@pytest.fixture
def mock_driver():
    """Create mock driver with all required methods."""
    driver = MagicMock()
    driver.go_home.return_value = {"ok": True}
    driver.go_back.return_value = {"ok": True}
    driver.unlock.return_value = {"ok": True}
    driver.clear_background.return_value = {"ok": True}
    driver.open_deeplink.return_value = {"ok": True}

    driver.screen.start_screenrecord.return_value = {
        "ok": True,
        "remote_path": "/sdcard/test.mp4",
    }
    driver.screen.stop_screenrecord.return_value = {"ok": True}
    driver.screen.get_screen_size.return_value = (1080, 2400)

    driver.app.clear_data.return_value = {"ok": True}
    driver.app.uninstall.return_value = {"ok": True}

    driver.pull_file.return_value = {"ok": True}
    driver.push_file.return_value = {"ok": True, "local_path": "/local/path", "remote_path": "/sdcard/pushed.txt"}
    driver.remove_remote_file.return_value = None

    driver.start_log_capture.return_value = {"ok": True, "pid": 1234}
    driver.stop_log_capture.return_value = {"ok": True, "lines": 100}
    driver.read_log.return_value = "line1\nline2 Error\nline3\n"

    mock_node = MagicMock()
    mock_node.text = "TestButton"
    mock_node.content_desc = ""
    mock_node.resource_id = "com.test:id/btn"
    mock_node.bounds_tuple.return_value = (100, 200, 300, 400)
    mock_node.center.return_value = (200, 300)
    driver.ui.dump_ui_nodes.return_value = [mock_node]
    driver.ui.get_current_activity.return_value = {
        "package": "com.test",
        "activity": ".MainActivity",
    }

    return driver


@pytest.fixture
def mock_get_driver(mock_driver):
    """Patch get_driver to return mock driver."""
    with patch("phone_pilot.mcp.server.get_driver", return_value=mock_driver) as m:
        yield m


# ---------------------------------------------------------------------------
# Device Management Tools / 设备管理工具
# ---------------------------------------------------------------------------


class TestDeviceManagement:
    """设备管理工具契约测试 / Device management tools contract tests."""

    def test_phone_list_devices_ok(self):
        """list_devices 正常返回 {"ok": True, "devices": [...]}.
        list_devices returns ok=True with devices list."""
        from phone_pilot.mcp.tool_contracts import validate_response

        mock_proc = MagicMock()
        mock_proc.stdout = "List of devices attached\ntest_123\tdevice\n"
        # mock adb runner 和解析器所在的源模块
        # Mock the source modules where adb runner and parser are defined
        with patch("phone_pilot.android.adb.runner.CommandRunner.run", return_value=mock_proc), \
             patch("phone_pilot.android.adb.parsers.parse_adb_devices") as mock_parse, \
             patch("phone_pilot.android.adb.utils.adb_executable", return_value="adb"):
            mock_parse.return_value = [
                {"serial": "test_123", "status": "device", "description": "Pixel 6"}
            ]
            from phone_pilot.mcp.server import phone_list_devices
            result = _run(phone_list_devices())

        assert result["ok"] is True
        assert "devices" in result
        assert isinstance(result["devices"], list)
        assert len(result["devices"]) >= 1
        assert result["devices"][0]["serial"] == "test_123"
        # 契约校验 / contract validation
        ok, missing = validate_response("phone_list_devices", result)
        assert ok is True, f"Contract validation failed, missing: {missing}"


# ---------------------------------------------------------------------------
# P0 Navigation Tools
# ---------------------------------------------------------------------------


class TestP0Navigation:
    """P0 导航工具契约测试 / P0 navigation tools contract tests."""

    def test_phone_go_home_ok(self, mock_resolve, mock_get_driver):
        """go_home 正常返回 {"ok": True, "platform": str}."""
        from phone_pilot.mcp.server import phone_go_home

        from phone_pilot.mcp.tool_contracts import validate_response

        result = _run(phone_go_home())
        assert result["ok"] is True
        assert "platform" in result
        assert result["platform"] == "android"
        assert result.get("device_serial") == "test_serial"
        ok, missing = validate_response("phone_go_home", result)
        assert ok is True, f"Contract validation failed, missing: {missing}"

    def test_phone_go_back_ok(self, mock_resolve, mock_get_driver):
        """go_back 正常返回."""
        from phone_pilot.mcp.server import phone_go_back

        from phone_pilot.mcp.tool_contracts import validate_response

        result = _run(phone_go_back())
        assert result["ok"] is True
        assert "platform" in result
        ok, missing = validate_response("phone_go_back", result)
        assert ok is True, f"Contract validation failed, missing: {missing}"

    def test_phone_unlock_ok(self, mock_resolve, mock_get_driver):
        """unlock 正常返回."""
        from phone_pilot.mcp.server import phone_unlock

        result = _run(phone_unlock())
        assert result["ok"] is True
        assert "platform" in result

    def test_phone_clear_background_ok(self, mock_resolve, mock_get_driver):
        """clear_background 正常返回."""
        from phone_pilot.mcp.server import phone_clear_background

        result = _run(phone_clear_background())
        assert result["ok"] is True
        assert "platform" in result

    def test_phone_clear_data_ok(self, mock_resolve, mock_get_driver):
        """clear_data 正常返回且包含 package."""
        from phone_pilot.mcp.server import phone_clear_data

        result = _run(phone_clear_data(package="com.test.app"))
        assert result["ok"] is True
        assert result.get("package") == "com.test.app"

    def test_phone_open_deeplink_ok(self, mock_resolve, mock_get_driver):
        """open_deeplink 正常返回且包含 uri."""
        from phone_pilot.mcp.server import phone_open_deeplink

        result = _run(phone_open_deeplink(uri="myapp://page/detail"))
        assert result["ok"] is True
        assert result.get("uri") == "myapp://page/detail"


# ---------------------------------------------------------------------------
# P0 Recording State
# ---------------------------------------------------------------------------


class TestP0Recording:
    """P0 录屏状态管理测试 / P0 recording state tests."""

    def test_start_recording_ok(self, mock_resolve, mock_get_driver):
        """start_recording 正常返回 local_path."""
        from phone_pilot.mcp.server import phone_start_recording

        from phone_pilot.mcp.tool_contracts import validate_response

        result = _run(phone_start_recording())
        assert result["ok"] is True
        assert "local_path" in result
        assert result["local_path"].endswith(".mp4")
        ok, missing = validate_response("phone_start_recording", result)
        assert ok is True, f"Contract validation failed, missing: {missing}"

    def test_start_recording_already_recording(self, mock_resolve, mock_get_driver):
        """重复 start 返回 already_recording."""
        from phone_pilot.mcp import server as mcp_server
        from phone_pilot.mcp.server import phone_start_recording

        _run(phone_start_recording())
        assert "test_serial" in mcp_server._recording_state
        result = _run(phone_start_recording())
        assert result["ok"] is False
        assert result.get("error") == "already_recording"

    def test_stop_recording_ok(self, mock_resolve, mock_get_driver):
        """stop_recording 正常返回并清理状态."""
        from phone_pilot.mcp import server as mcp_server
        from phone_pilot.mcp.server import phone_start_recording, phone_stop_recording

        start_result = _run(phone_start_recording())
        local_path = start_result["local_path"]
        result = _run(phone_stop_recording())
        assert result["ok"] is True
        assert result.get("local_path") == local_path
        assert "test_serial" not in mcp_server._recording_state

    def test_stop_recording_not_recording(self, mock_resolve, mock_get_driver):
        """未 start 时 stop 返回 not_recording."""
        from phone_pilot.mcp.server import phone_stop_recording

        result = _run(phone_stop_recording())
        assert result["ok"] is False
        assert result.get("error") == "not_recording"

    def test_start_recording_with_serial_returns_starting(self, mock_resolve, mock_get_driver):
        """方案 B：传入 device_serial 时立即返回 status=starting."""
        from phone_pilot.mcp.server import phone_start_recording

        result = _run(phone_start_recording(device_serial="dev123"))
        assert result["ok"] is True
        assert result.get("status") == "starting"
        assert result.get("device_serial") == "dev123"

    def test_recording_status_requires_serial(self):
        """phone_recording_status 必须传入 device_serial."""
        from phone_pilot.mcp.server import phone_recording_status

        result = phone_recording_status("")
        assert result["ok"] is False
        assert result.get("status") == "not_recording"
        assert "device_serial" in result.get("error", "")

    def test_recording_status_not_recording(self):
        """无录屏时 status 返回 not_recording."""
        from phone_pilot.mcp.server import phone_recording_status

        result = phone_recording_status("unknown_serial")
        assert result["ok"] is True
        assert result.get("status") == "not_recording"

    def test_stop_recording_still_starting_returns_error(self, mock_resolve, mock_get_driver):
        """方案 B：status 为 starting 时 stop 返回 recording_still_starting."""
        from phone_pilot.mcp import server as mcp_server
        from phone_pilot.mcp.server import phone_stop_recording

        mcp_server._recording_status["s1"] = {"status": "starting"}
        result = _run(phone_stop_recording(device_serial="s1"))
        assert result["ok"] is False
        assert result.get("error") == "recording_still_starting"
        mcp_server._recording_status.pop("s1", None)


# ---------------------------------------------------------------------------
# P0 Logcat
# ---------------------------------------------------------------------------


class TestP0Logcat:
    """P0 Logcat 工具测试 / P0 logcat tools tests."""

    def test_start_logcat_ok(self, mock_resolve, mock_get_driver):
        """start_logcat 正常返回 path."""
        from phone_pilot.mcp.server import phone_start_logcat

        from phone_pilot.mcp.tool_contracts import validate_response

        result = _run(phone_start_logcat())
        assert result["ok"] is True
        assert "path" in result
        assert result.get("pid") == 1234
        ok, missing = validate_response("phone_start_logcat", result)
        assert ok is True, f"Contract validation failed, missing: {missing}"

    def test_start_logcat_already_capturing(self, mock_resolve, mock_get_driver):
        """重复 start 返回 already_capturing."""
        from phone_pilot.mcp import server as mcp_server
        from phone_pilot.mcp.server import phone_start_logcat

        _run(phone_start_logcat())
        assert "test_serial" in mcp_server._logcat_state
        result = _run(phone_start_logcat())
        assert result["ok"] is False
        assert result.get("error") == "already_capturing"

    def test_stop_logcat_ok(self, mock_resolve, mock_get_driver):
        """stop_logcat 正常返回."""
        from phone_pilot.mcp import server as mcp_server
        from phone_pilot.mcp.server import phone_start_logcat, phone_stop_logcat
        from phone_pilot.mcp.tool_contracts import validate_response

        _run(phone_start_logcat())
        result = _run(phone_stop_logcat())
        assert result["ok"] is True
        assert "test_serial" not in mcp_server._logcat_state or mcp_server._logcat_state.get("test_serial") is None
        ok, missing = validate_response("phone_stop_logcat", result)
        assert ok is True, f"Contract validation failed, missing: {missing}"

    def test_stop_logcat_no_capture(self, mock_resolve, mock_get_driver):
        """未 start 时 stop 返回 no_logcat_capture."""
        from phone_pilot.mcp.server import phone_stop_logcat

        result = _run(phone_stop_logcat())
        assert result["ok"] is False
        assert result.get("error") == "no_logcat_capture"

    def test_search_logcat_with_pattern(self, mock_resolve, mock_get_driver):
        """search_logcat 搜索关键词返回匹配行."""
        from phone_pilot.mcp.server import phone_search_logcat

        result = _run(phone_search_logcat(pattern="Error"))
        assert result["ok"] is True
        assert "matches" in result
        assert "count" in result
        assert result["count"] == 1
        assert "Error" in result["matches"][0]

    def test_search_logcat_regex(self, mock_resolve, mock_get_driver):
        """search_logcat regex=True 使用正则匹配."""
        from phone_pilot.mcp.server import phone_search_logcat

        result = _run(phone_search_logcat(pattern=r"line\d", regex=True))
        assert result["ok"] is True
        assert result["count"] >= 3  # line1, line2, line3


# ---------------------------------------------------------------------------
# P0 ScriptContext Tools
# ---------------------------------------------------------------------------


class TestP0ScriptCtx:
    """P0 ScriptContext 工具测试 / P0 script context tools tests."""

    def test_scroll_to_find_found(self, mock_resolve, mock_get_driver):
        """scroll_to_find 找到元素返回 center/bounds/text."""
        mock_elem = MagicMock()
        mock_elem.center.return_value = (200, 300)
        mock_elem.bounds.return_value = (100, 200, 300, 400)
        mock_elem.display_text = "Target"

        with patch("phone_pilot.mcp.server.scroll_to_find", return_value=mock_elem):
            from phone_pilot.mcp.server import phone_scroll_to_find

            result = _run(phone_scroll_to_find(text="Target"))
        assert result["ok"] is True
        assert result.get("center") == [200, 300]
        assert result.get("bounds") == [100, 200, 300, 400]
        assert result.get("text") == "Target"

    def test_scroll_to_find_not_found(self, mock_resolve, mock_get_driver):
        """scroll_to_find 未找到返回 {"ok": False}."""
        with patch("phone_pilot.mcp.server.scroll_to_find", return_value=None):
            from phone_pilot.mcp.server import phone_scroll_to_find

            result = _run(phone_scroll_to_find(text="NotFound"))
        assert result["ok"] is False
        assert "error" in result

    def test_wait_for_element_found(self, mock_resolve, mock_get_driver):
        """wait_for_element 找到元素返回 found=True."""
        from phone_pilot.mcp.server import phone_wait_for_element

        result = _run(phone_wait_for_element(text="TestButton", timeout_s=1.0))
        assert result["ok"] is True
        assert result.get("found") is True
        assert "element" in result

    def test_wait_for_element_timeout(self, mock_resolve, mock_get_driver):
        """wait_for_element 超时返回 found=False."""
        mock_driver = mock_get_driver.return_value
        mock_driver.ui.dump_ui_nodes.return_value = []

        from phone_pilot.mcp.server import phone_wait_for_element

        result = _run(phone_wait_for_element(text="NonExistent", timeout_s=0.001))
        assert result["ok"] is True
        assert result.get("found") is False

    def test_dismiss_popup_dismissed(self, mock_resolve, mock_get_driver):
        """dismiss_popup 关闭弹窗返回 dismissed=True."""
        mock_guard = MagicMock()
        mock_guard.check_and_dismiss.return_value = {"button": "OK"}

        with patch("phone_pilot.mcp.server.PopupGuard", return_value=mock_guard):
            from phone_pilot.mcp.server import phone_dismiss_popup

            result = _run(phone_dismiss_popup())
        assert result["ok"] is True
        assert result.get("dismissed") is True

    def test_dismiss_popup_none(self, mock_resolve, mock_get_driver):
        """dismiss_popup 无弹窗返回 dismissed=False."""
        mock_guard = MagicMock()
        mock_guard.check_and_dismiss.return_value = None

        with patch("phone_pilot.mcp.server.PopupGuard", return_value=mock_guard):
            from phone_pilot.mcp.server import phone_dismiss_popup

            result = _run(phone_dismiss_popup())
        assert result["ok"] is True
        assert result.get("dismissed") is False


# ---------------------------------------------------------------------------
# P1 Tools
# ---------------------------------------------------------------------------


class TestP1Tools:
    """P1 工具契约测试 / P1 tools contract tests."""

    def test_smart_find_found(self, mock_resolve, mock_get_driver):
        """smart_find 找到返回元素信息."""
        mock_elem = MagicMock()
        mock_elem.center.return_value = (150, 250)
        mock_elem.bounds.return_value = (50, 150, 250, 350)
        mock_elem.display_text = "Submit"

        with patch("phone_pilot.mcp.server.find_text", return_value=mock_elem):
            from phone_pilot.mcp.server import phone_smart_find

            result = _run(phone_smart_find(text="Submit"))
        assert result["ok"] is True
        assert result.get("center") == [150, 250]
        assert result.get("text") == "Submit"

    def test_smart_find_popup_retry(self, mock_resolve, mock_get_driver):
        """smart_find 首次未找到时关闭弹窗后重试成功."""
        mock_elem = MagicMock()
        mock_elem.center.return_value = (100, 200)
        mock_elem.bounds.return_value = (0, 100, 200, 300)
        mock_elem.display_text = "RetryBtn"

        with patch("phone_pilot.mcp.server.find_text", side_effect=[None, mock_elem]):
            with patch("phone_pilot.mcp.server.PopupGuard") as mock_guard_cls:
                mock_guard = MagicMock()
                mock_guard_cls.return_value = mock_guard
                from phone_pilot.mcp.server import phone_smart_find

                result = _run(phone_smart_find(text="RetryBtn", try_dismiss_popup=True))
        assert result["ok"] is True
        assert mock_guard.check_and_dismiss.called

    def test_install_app_ok(self, mock_resolve):
        """install_app 正常返回."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".apk", delete=False) as f:
            apk_path = f.name
        try:
            with patch("phone_pilot.android.touch.agent.install_apk"):
                from phone_pilot.mcp.server import phone_install_app

                result = _run(phone_install_app(apk_path=apk_path))
            assert result["ok"] is True
            assert "apk_path" in result
        finally:
            Path(apk_path).unlink(missing_ok=True)

    def test_uninstall_app_ok(self, mock_resolve, mock_get_driver):
        """uninstall_app 正常返回."""
        from phone_pilot.mcp.server import phone_uninstall_app

        result = _run(phone_uninstall_app(package="com.test.app"))
        assert result["ok"] is True
        assert result.get("package") == "com.test.app"

    def test_memory_snapshot_ok(self, mock_resolve):
        """memory_snapshot 正常返回."""
        with patch("phone_pilot.memory_analyze.meminfo.capture_meminfo") as m:
            m.return_value = {"ok": True, "summary": {"total_pss": 100}}
            from phone_pilot.mcp.server import phone_memory_snapshot

            result = _run(phone_memory_snapshot(package="com.test.app"))
        assert result["ok"] is True
        assert "summary" in result

    def test_pull_file_ok(self, mock_resolve, mock_get_driver):
        """pull_file 正常返回 local_path."""
        from phone_pilot.mcp.server import phone_pull_file

        result = _run(phone_pull_file(remote_path="/sdcard/test.txt"))
        assert result["ok"] is True
        assert "local_path" in result
        assert "remote_path" in result

    def test_push_file_ok(self, mock_resolve, mock_get_driver):
        """push_file 正常返回."""
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as f:
            local_path = f.name
        try:
            from phone_pilot.mcp.server import phone_push_file

            result = _run(
                phone_push_file(
                    local_path=local_path,
                    remote_path="/sdcard/pushed.txt",
                )
            )
            assert result["ok"] is True
            assert "local_path" in result
        finally:
            Path(local_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# P2 Tools
# ---------------------------------------------------------------------------


class TestP2Tools:
    """P2 工具契约测试 / P2 tools contract tests."""

    def test_read_clipboard_ok(self, mock_resolve):
        """read_clipboard 返回 text."""
        with patch("phone_pilot.mcp.server.get_clipboard_text") as m:
            m.return_value = {"ok": True, "text": "clipboard content"}
            from phone_pilot.mcp.server import phone_read_clipboard

            result = _run(phone_read_clipboard())
        assert result["ok"] is True
        assert result.get("text") == "clipboard content"

    def test_get_notifications_ok(self, mock_resolve):
        """get_notifications 返回 notifications 列表."""
        with patch("phone_pilot.mcp.server.get_notifications") as m:
            m.return_value = {
                "ok": True,
                "notifications": [
                    {"package": "com.test", "title": "Title", "text": "Text"},
                ],
            }
            from phone_pilot.mcp.server import phone_get_notifications

            result = _run(phone_get_notifications())
        assert result["ok"] is True
        assert "notifications" in result
        assert len(result["notifications"]) == 1

    def test_toggle_wifi_ok(self, mock_resolve):
        """toggle_wifi 返回 wifi_on."""
        with patch("phone_pilot.mcp.server.set_wifi_enabled") as m:
            m.return_value = {"ok": True, "wifi_on": True}
            from phone_pilot.mcp.server import phone_toggle_wifi

            result = _run(phone_toggle_wifi(enabled=True))
        assert result["ok"] is True
        assert result.get("wifi_on") is True

    def test_toggle_airplane_ok(self, mock_resolve):
        """toggle_airplane 返回 airplane_on."""
        with patch("phone_pilot.mcp.server.set_airplane_mode") as m:
            m.return_value = {"ok": True, "airplane_on": False}
            from phone_pilot.mcp.server import phone_toggle_airplane

            result = _run(phone_toggle_airplane(enabled=False))
        assert result["ok"] is True
        assert result.get("airplane_on") is False

    def test_execute_shell_ok(self, mock_resolve):
        """execute_shell 白名单命令正常返回."""
        with patch("phone_pilot.mcp.server.execute_shell") as m:
            m.return_value = {
                "ok": True,
                "stdout": "output",
                "stderr": "",
                "returncode": 0,
            }
            from phone_pilot.mcp.server import phone_execute_shell

            result = _run(phone_execute_shell(command="getprop ro.build.version.release"))
        assert result["ok"] is True
        assert "stdout" in result

    def test_execute_shell_blocked(self, mock_resolve):
        """execute_shell 禁止命令返回错误."""
        with patch("phone_pilot.mcp.server.execute_shell") as m:
            m.return_value = {"ok": False, "error": "command prefix 'su' is forbidden"}
            from phone_pilot.mcp.server import phone_execute_shell

            result = _run(phone_execute_shell(command="su -c id"))
        assert result["ok"] is False
        assert "error" in result

    def test_get_device_info_ok(self, mock_resolve, mock_get_driver):
        """get_device_info 返回 screen/activity."""
        with patch("phone_pilot.mcp.server.get_device_model_and_version") as m:
            m.return_value = ("Pixel 6", "14")
            from phone_pilot.mcp.server import phone_get_device_info

            result = _run(phone_get_device_info())
        assert result["ok"] is True
        assert "screen" in result
        assert result["screen"]["width"] == 1080
        assert result["screen"]["height"] == 2400
        assert "activity" in result
        assert result["activity"]["package"] == "com.test"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestMCPErrorHandling:
    """MCP 工具异常处理验证 / MCP tools error handling verification."""

    def test_resolve_error_returns_ok_false(self):
        """设备未找到时返回 {"ok": False, "error": str}."""
        with patch("phone_pilot.mcp.server._resolve_device_serial") as m:
            m.return_value = (
                None,
                None,
                {"ok": False, "error": "no_device_connected", "note": "未发现可用设备"},
            )
            from phone_pilot.mcp.server import phone_go_home

            result = _run(phone_go_home())
        assert result["ok"] is False
        assert "error" in result

    def test_driver_exception_returns_ok_false(self, mock_resolve, mock_get_driver):
        """driver 抛出异常时返回 {"ok": False, "error": str}."""
        mock_get_driver.return_value.go_home.side_effect = RuntimeError("device disconnected")
        from phone_pilot.mcp.server import phone_go_home

        result = _run(phone_go_home())
        assert result["ok"] is False
        assert "error" in result
        assert "device disconnected" in result["error"]


# ---------------------------------------------------------------------------
# Contract validation / 契约校验
# ---------------------------------------------------------------------------


class TestContractValidation:
    """契约校验函数测试 / Contract validation function tests."""

    def test_validate_response_success_path(self):
        """成功路径：传入完整 response，期望 (True, [])。Success path: full response, expect (True, [])."""
        from phone_pilot.mcp.tool_contracts import validate_response

        result = validate_response(
            "phone_go_home",
            {"ok": True, "platform": "android", "device_serial": "abc"},
        )
        assert result == (True, [])

    def test_validate_response_missing_key(self):
        """成功路径缺键：缺少 platform 键。Success path missing key: platform."""
        from phone_pilot.mcp.tool_contracts import validate_response

        ok, missing = validate_response("phone_go_home", {"ok": True})
        assert ok is False
        assert "platform" in missing

    def test_validate_response_error_path_complete(self):
        """失败路径完整：含 ok=False 和 error。Error path complete: ok=False and error."""
        from phone_pilot.mcp.tool_contracts import validate_response

        result = validate_response(
            "phone_go_home",
            {"ok": False, "error": "device not found"},
        )
        assert result == (True, [])

    def test_validate_response_error_path_missing_error(self):
        """失败路径缺 error 键 → (False, ["error"])。Error path missing error key."""
        from phone_pilot.mcp.tool_contracts import validate_response

        ok, missing = validate_response("phone_go_home", {"ok": False})
        assert ok is False
        assert "error" in missing

    def test_validate_response_no_ok_key(self):
        """缺少 ok 键 → (False, ["ok"])。Missing ok key."""
        from phone_pilot.mcp.tool_contracts import validate_response

        ok, missing = validate_response("phone_go_home", {"platform": "android"})
        assert ok is False
        assert "ok" in missing

    def test_validate_response_not_dict(self):
        """传入非 dict → (False, ["response_not_dict"])。Non-dict input."""
        from phone_pilot.mcp.tool_contracts import validate_response

        ok, missing = validate_response("phone_go_home", "not a dict")
        assert ok is False
        assert "response_not_dict" in missing

    def test_validate_response_unknown_tool(self):
        """未知工具 → (False, [...])。Unknown tool returns (False, [...])."""
        from phone_pilot.mcp.tool_contracts import validate_response

        ok, missing = validate_response("phone_nonexistent", {"ok": True})
        assert ok is False
        assert len(missing) > 0

    def test_all_contracts_count(self):
        """契约表总数应为 62。Contract table should have 62 entries."""
        from phone_pilot.mcp.tool_contracts import get_all_contracts

        contracts = get_all_contracts()
        assert len(contracts) == 62, f"Expected 62 contracts, got {len(contracts)}"
