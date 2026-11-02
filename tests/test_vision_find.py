"""Unit tests for find_vision and find_text(use_vision=True) cross-platform integration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phone_pilot.script_api.context import ScriptContext
from phone_pilot.script_api.find import find_vision, find_text


def _make_context(platform: str, serial: str) -> ScriptContext:
    ctx = MagicMock(spec=ScriptContext)
    ctx.platform = platform
    ctx.device_serial = serial
    ctx.driver = MagicMock()
    ctx.driver.platform = platform
    ctx.driver.screen.screenshot.return_value = b"\x89PNG\r\n\x1a\nfake"
    ctx.driver.screen.get_screen_size.return_value = (1080, 2400)
    ctx._observe = MagicMock()
    ctx._ui_cache = None
    return ctx


def test_find_vision_skips_when_api_unconfigured(monkeypatch):
    monkeypatch.delenv("PHONE_PILOT_VISION_API_KEY", raising=False)
    for plat in ("android", "harmony", "ios"):
        ctx = _make_context(plat, f"serial-{plat}")
        elem = find_vision(ctx, "Play Game")
        assert elem is None
        ctx._observe.assert_called_with("find_vision", "'Play Game' 视觉API未配置，已跳过", status="skip")


def test_find_vision_success_across_platforms(monkeypatch):
    monkeypatch.setenv("PHONE_PILOT_VISION_API_KEY", "sk-test")
    for plat in ("android", "harmony", "ios"):
        ctx = _make_context(plat, f"serial-{plat}")
        mock_client = MagicMock()
        mock_client.is_available.return_value = True

        mock_result = MagicMock()
        mock_result.ok = True
        mock_result.bounds = (100, 200, 300, 400)
        mock_result.normalized_box = [200, 100, 400, 300]
        mock_result.confidence = 0.95
        mock_result.label = "Close Ad"
        mock_client.ground.return_value = mock_result

        elem = find_vision(ctx, "Close Ad", client=mock_client)
        assert elem is not None
        assert elem.bounds() == (100, 200, 300, 400)
        assert elem.center() == (200, 300)
        assert elem.display_text == "Close Ad"


def test_find_text_vision_fallback_across_platforms(monkeypatch):
    monkeypatch.setenv("PHONE_PILOT_VISION_API_KEY", "sk-test")
    for plat in ("android", "harmony", "ios"):
        ctx = _make_context(plat, f"serial-{plat}")

        # Simulate UIA failure
        with (
            patch("phone_pilot.script_api.find._uia_find_text_boxes", return_value=[]),
            patch("phone_pilot.script_api.find._try_self_heal", return_value=None),
        ):
            mock_client = MagicMock()
            mock_client.is_available.return_value = True
            mock_result = MagicMock()
            mock_result.ok = True
            mock_result.bounds = (50, 50, 150, 150)
            mock_result.normalized_box = [50, 50, 150, 150]
            mock_result.confidence = 0.9
            mock_result.label = "Rewarded Claim"
            mock_client.ground.return_value = mock_result

            with patch("phone_pilot.extensions.vision.grounding.VisionGroundingClient", return_value=mock_client):
                elem = find_text(ctx, "Rewarded Claim", use_ocr=False, use_vision=True, retry_attempts=1)
                assert elem is not None
                assert elem.bounds() == (50, 50, 150, 150)
                assert elem.center() == (100, 100)
