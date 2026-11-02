"""
Unit tests for Vision Grounding Client, Coordinate Denormalization, and Graceful Bypass.
"""

from __future__ import annotations

import json
import urllib.error
import pytest

from phone_pilot.extensions.vision.grounding import (
    VisionGroundingClient,
    VisionGroundingConfig,
    denormalize_box,
    parse_vlm_grounding_response,
)


def test_vision_config_defaults(monkeypatch: pytest.MonkeyPatch):
    """Test environment variable parsing for VisionGroundingConfig."""
    monkeypatch.delenv("PHONE_PILOT_VISION_API_KEY", raising=False)
    monkeypatch.delenv("PHONE_PILOT_VISION_BASE_URL", raising=False)
    monkeypatch.delenv("PHONE_PILOT_VISION_MODEL", raising=False)
    monkeypatch.delenv("PHONE_PILOT_VISION_ENABLED", raising=False)

    cfg = VisionGroundingConfig.from_env()
    assert cfg.api_key is None
    assert cfg.model == "qwen-vl-max"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.enabled is None


def test_vision_config_custom_env(monkeypatch: pytest.MonkeyPatch):
    """Test custom environment variables."""
    monkeypatch.setenv("PHONE_PILOT_VISION_API_KEY", "sk-test-secret")
    monkeypatch.setenv("PHONE_PILOT_VISION_BASE_URL", "https://custom.api.com/v1/")
    monkeypatch.setenv("PHONE_PILOT_VISION_MODEL", "gpt-4o")
    monkeypatch.setenv("PHONE_PILOT_VISION_ENABLED", "true")
    monkeypatch.setenv("PHONE_PILOT_VISION_TIMEOUT", "25.0")

    cfg = VisionGroundingConfig.from_env()
    assert cfg.api_key == "sk-test-secret"
    assert cfg.base_url == "https://custom.api.com/v1/"
    assert cfg.model == "gpt-4o"
    assert cfg.enabled is True
    assert cfg.timeout == 25.0


def test_is_available_logic():
    """Verify is_available returns False when key is missing or disabled."""
    # No key
    c1 = VisionGroundingClient(VisionGroundingConfig(api_key=None))
    assert not c1.is_available()

    # Empty key
    c2 = VisionGroundingClient(VisionGroundingConfig(api_key=""))
    assert not c2.is_available()

    # Valid key, enabled auto
    c3 = VisionGroundingClient(VisionGroundingConfig(api_key="sk-abc"))
    assert c3.is_available()

    # Valid key, but explicitly disabled
    c4 = VisionGroundingClient(VisionGroundingConfig(api_key="sk-abc", enabled=False))
    assert not c4.is_available()


def test_graceful_bypass_when_not_configured():
    """When API is not configured, ground() must gracefully return without exception."""
    client = VisionGroundingClient(VisionGroundingConfig(api_key=None))
    res = client.ground(b"fake_png_bytes_12345678", "close button", (1080, 2400))

    assert not res.ok
    assert res.reason == "vision_api_not_configured"
    assert res.center is None
    assert res.bounds is None


def test_denormalize_box_scale_1000():
    """Verify conversion of [ymin, xmin, ymax, xmax] on scale 0..1000."""
    screen_w = 1080
    screen_h = 2400

    # Box at top-right: y: 50..100, x: 800..950
    box = [50, 800, 100, 950]
    x1, y1, x2, y2, center = denormalize_box(box, screen_w, screen_h, scale=1000.0)

    assert x1 == int(round(0.800 * 1080))
    assert x2 == int(round(0.950 * 1080))
    assert y1 == int(round(0.050 * 2400))
    assert y2 == int(round(0.100 * 2400))
    assert center == ((x1 + x2) // 2, (y1 + y2) // 2)


def test_denormalize_box_dict_formats():
    """Verify conversion when box is provided as a dict with various key conventions."""
    screen_w = 1000
    screen_h = 2000

    d1 = {"ymin": 100, "xmin": 200, "ymax": 300, "xmax": 400}
    x1, y1, x2, y2, center = denormalize_box(d1, screen_w, screen_h, scale=1000.0)
    assert (x1, y1, x2, y2) == (200, 200, 400, 600)
    assert center == (300, 400)

    d2 = {"top": 100, "left": 200, "bottom": 300, "right": 400}
    x1, y1, x2, y2, center = denormalize_box(d2, screen_w, screen_h, scale=1000.0)
    assert (x1, y1, x2, y2) == (200, 200, 400, 600)
    assert center == (300, 400)


def test_denormalize_box_auto_float_scale():
    """Verify automatic detection and conversion when coordinates are in [0.0, 1.0]."""
    screen_w = 1000
    screen_h = 2000

    box = [0.1, 0.2, 0.3, 0.4]  # ymin, xmin, ymax, xmax
    x1, y1, x2, y2, center = denormalize_box(box, screen_w, screen_h)

    assert x1 == 200
    assert y1 == 200
    assert x2 == 400
    assert y2 == 600
    assert center == (300, 400)


def test_denormalize_box_inverted_and_clipping():
    """Verify inverted coordinates swap and viewport clipping."""
    screen_w = 1080
    screen_h = 2400

    # Inverted ymin/ymax and out-of-bounds coordinates
    box = [1200, -50, 100, 1100]  # ymin > ymax, xmin < 0, xmax > 1000
    x1, y1, x2, y2, center = denormalize_box(box, screen_w, screen_h, scale=1000.0)

    assert x1 == 0
    assert x2 == 1079
    assert y1 == int(round(0.100 * 2400))
    assert y2 == 2399
    assert 0 <= center[0] < screen_w
    assert 0 <= center[1] < screen_h


def test_parse_vlm_grounding_response_pure_json():
    """Parse pure JSON output."""
    raw = '{"found": true, "box": [120, 850, 180, 950], "confidence": 0.96, "description": "skip button"}'
    res = parse_vlm_grounding_response(raw)

    assert res is not None
    assert res["found"] is True
    assert res["box"] == [120, 850, 180, 950]
    assert res["confidence"] == 0.96


def test_parse_vlm_grounding_response_markdown_codeblock():
    """Parse output wrapped in markdown codeblock with extra commentary."""
    raw = """
    Here is the detected coordinate:
    ```json
    {
      "found": true,
      "box": [50, 900, 110, 980],
      "confidence": 0.91,
      "description": "X close icon"
    }
    ```
    I hope this helps!
    """
    res = parse_vlm_grounding_response(raw)

    assert res is not None
    assert res["found"] is True
    assert res["box"] == [50, 900, 110, 980]
    assert res["description"] == "X close icon"


def test_parse_vlm_grounding_response_not_found():
    """Parse 'not found' response."""
    raw = '{"found": false, "box": null, "confidence": 0.0, "description": "not found"}'
    res = parse_vlm_grounding_response(raw)

    assert res is not None
    assert res["found"] is False


def test_ground_with_mock_transport_success():
    """Test complete ground() flow with mocked HTTP response."""
    mock_vlm_body = {
        "choices": [
            {
                "message": {
                    "content": '{"found": true, "box": [100, 200, 300, 400], "confidence": 0.98, "description": "TAP TO START"}'
                }
            }
        ]
    }

    def mock_transport(url, headers, data, timeout):
        assert "Authorization" in headers
        assert b"messages" in data
        return json.dumps(mock_vlm_body).encode("utf-8")

    client = VisionGroundingClient(
        config=VisionGroundingConfig(api_key="sk-mock-key"),
        http_transport=mock_transport,
    )

    result = client.ground(b"dummy_png_bytes_content_here", "TAP TO START", (1000, 2000))

    assert result.ok is True
    assert result.center == (300, 400)
    assert result.bounds == (200, 200, 400, 600)
    assert result.normalized_box == [100, 200, 300, 400]
    assert result.confidence == 0.98
    assert result.label == "TAP TO START"


def test_ground_with_mock_transport_not_found():
    """Test ground() flow when model returns element not found."""
    mock_vlm_body = {
        "choices": [
            {
                "message": {
                    "content": '{"found": false, "box": null, "description": "no matching button"}'
                }
            }
        ]
    }

    def mock_transport(url, headers, data, timeout):
        return json.dumps(mock_vlm_body).encode("utf-8")

    client = VisionGroundingClient(
        config=VisionGroundingConfig(api_key="sk-mock-key"),
        http_transport=mock_transport,
    )

    result = client.ground(b"dummy_png_bytes_content_here", "Submit", (1080, 1920))

    assert result.ok is False
    assert "element_not_found" in (result.reason or "")
    assert result.center is None


def test_ground_http_error_graceful_handling():
    """Test network/HTTP error handling."""
    def mock_transport_err(url, headers, data, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)  # type: ignore

    client = VisionGroundingClient(
        config=VisionGroundingConfig(api_key="sk-invalid"),
        http_transport=mock_transport_err,
    )

    result = client.ground(b"dummy_png_bytes_content_here", "Skip", (1080, 1920))
    assert result.ok is False
    assert "http_error_401" in (result.reason or "")
