"""
Unit and Contract tests for MCP vision grounding tools (phone_vision_find, phone_smart_find with vision).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from phone_pilot.mcp.tool_contracts import validate_response, get_contract
from phone_pilot.mcp.server import phone_smart_find, phone_vision_find


def _run(coro):
    return asyncio.run(coro)


def test_tool_contract_registered():
    """Verify phone_vision_find is registered in ToolContracts."""
    contract = get_contract("phone_vision_find")
    assert contract is not None
    assert contract.tool_name == "phone_vision_find"
    assert "center" in contract.required_keys_when_ok
    assert "bounds" in contract.required_keys_when_ok


@patch("phone_pilot.mcp._script_tools._resolve_device_serial")
def test_phone_vision_find_unconfigured_bypass(mock_resolve, monkeypatch):
    """When API is not configured, phone_vision_find returns vision_api_not_configured."""
    monkeypatch.delenv("PHONE_PILOT_VISION_API_KEY", raising=False)
    mock_resolve.return_value = ("emulator-5554", "android", None)

    res = _run(phone_vision_find(prompt="TAP TO START button", device_serial="emulator-5554"))

    assert res["ok"] is False
    assert res["error"] == "vision_api_not_configured"
    assert "hint" in res

    ok, missing = validate_response("phone_vision_find", res)
    assert ok, f"Contract validation failed: {missing}"


@patch("phone_pilot.mcp._script_tools._resolve_device_serial")
@patch("phone_pilot.mcp._script_tools._make_script_context")
def test_phone_vision_find_mock_success(mock_ctx, mock_resolve, monkeypatch):
    """When vision client locates the element, phone_vision_find returns valid bounds and center."""
    monkeypatch.setenv("PHONE_PILOT_VISION_API_KEY", "sk-mock-key")
    mock_resolve.return_value = ("emulator-5554", "android", None)

    dummy_ctx = MagicMock()
    mock_ctx.return_value = dummy_ctx

    mock_elem = MagicMock()
    mock_elem.center.return_value = (540, 960)
    mock_elem.bounds.return_value = (400, 900, 680, 1020)
    mock_elem.display_text = "TAP TO START"
    mock_elem.text = "TAP TO START"

    with patch("phone_pilot.mcp._script_tools.find_vision", return_value=mock_elem):
        res = _run(phone_vision_find(prompt="TAP TO START", device_serial="emulator-5554"))

    assert res["ok"] is True
    assert res["center"] == [540, 960]
    assert res["bounds"] == [400, 900, 680, 1020]
    assert res["text"] == "TAP TO START"

    ok, missing = validate_response("phone_vision_find", res)
    assert ok, f"Contract validation failed: {missing}"


@patch("phone_pilot.mcp._script_tools._resolve_device_serial")
@patch("phone_pilot.mcp._script_tools._make_script_context")
def test_phone_smart_find_with_vision_fallback_not_found(mock_ctx, mock_resolve, monkeypatch):
    """When neither UIA nor Vision finds target, phone_smart_find returns graceful not_found."""
    monkeypatch.delenv("PHONE_PILOT_VISION_API_KEY", raising=False)
    mock_resolve.return_value = ("emulator-5554", "android", None)

    dummy_ctx = MagicMock()
    mock_ctx.return_value = dummy_ctx

    with patch("phone_pilot.mcp._script_tools.find_text", return_value=None):
        res = _run(phone_smart_find(text="NonExistent", use_vision=True, device_serial="emulator-5554"))

    assert res["ok"] is False
    assert res["error"] == "not_found"

    ok, missing = validate_response("phone_smart_find", res)
    assert ok, f"Contract validation failed: {missing}"


@patch("phone_pilot.mcp._script_tools._resolve_device_serial")
@patch("phone_pilot.mcp._script_tools._make_script_context")
def test_phone_smart_find_harmony_and_ios_parity(mock_ctx, mock_resolve):
    """Verify phone_smart_find correctly routes and passes platform for harmony and ios."""
    for plat, serial in [("harmony", "HDC-SERIAL-01"), ("ios", "00008030-001234560A80802E")]:
        mock_resolve.return_value = (serial, plat, None)
        dummy_ctx = MagicMock()
        mock_ctx.return_value = dummy_ctx

        mock_elem = MagicMock()
        mock_elem.center.return_value = (300, 600)
        mock_elem.bounds.return_value = (200, 500, 400, 700)
        mock_elem.display_text = "Login"
        mock_elem.text = "Login"

        with patch("phone_pilot.mcp._script_tools.find_text", return_value=mock_elem) as mock_ft:
            res = _run(phone_smart_find(text="Login", device_serial=serial, platform=plat, use_vision=True))
            assert res["ok"] is True
            assert res["platform"] == plat
            assert res["device_serial"] == serial
            mock_ft.assert_called_once_with(
                dummy_ctx,
                "Login",
                use_ocr=False,
                exact=False,
                retry_attempts=1,
                use_vision=True,
            )

