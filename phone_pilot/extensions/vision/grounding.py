"""
Vision Grounding Client and Coordinate Transformation Engine.
多模态视觉定位客户端与归一化坐标转换引擎。

Provides:
- Graceful bypass when API is not configured (zero error, zero overhead).
- OpenAI-compatible multimodal Chat Completions client for VLM grounding.
- Robust JSON and Markdown coordinate parsing.
- Normalized [ymin, xmin, ymax, xmax] -> screen physical pixel bounding box and center coordinate translation.
- Zero local heavy dependencies (uses standard library urllib.request).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Sequence

logger = logging.getLogger("phone_pilot.vision.grounding")


@dataclass
class VisionGroundingConfig:
    """Configuration for VLM Vision Grounding."""

    enabled: bool | None = None
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    model: str = "qwen-vl-max"
    timeout: float = 15.0
    max_retries: int = 1

    @classmethod
    def from_env(cls) -> VisionGroundingConfig:
        """Load configuration from environment variables."""
        env_enabled = os.environ.get("PHONE_PILOT_VISION_ENABLED")
        enabled = None
        if env_enabled is not None:
            enabled = env_enabled.strip().lower() in ("1", "true", "yes", "on")

        base_url = os.environ.get("PHONE_PILOT_VISION_BASE_URL", "").strip() or "https://api.openai.com/v1"
        api_key = os.environ.get("PHONE_PILOT_VISION_API_KEY", "").strip() or None
        model = os.environ.get("PHONE_PILOT_VISION_MODEL", "").strip() or "qwen-vl-max"

        raw_timeout = os.environ.get("PHONE_PILOT_VISION_TIMEOUT", "").strip()
        timeout = float(raw_timeout) if raw_timeout else 15.0

        return cls(
            enabled=enabled,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )


@dataclass
class GroundingResult:
    """Result of visual grounding attempt."""

    ok: bool
    center: tuple[int, int] | None = None
    bounds: tuple[int, int, int, int] | None = None  # (x1, y1, x2, y2)
    normalized_box: list[int] | None = None  # [ymin, xmin, ymax, xmax] in [0, 1000]
    confidence: float = 0.0
    label: str = ""
    reason: str | None = None
    raw_response: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "center": list(self.center) if self.center else None,
            "bounds": list(self.bounds) if self.bounds else None,
            "normalized_box": self.normalized_box,
            "confidence": self.confidence,
            "label": self.label,
            "reason": self.reason,
        }


def denormalize_box(
    box: Sequence[float | int] | dict[str, Any],
    screen_width: int,
    screen_height: int,
    scale: float = 1000.0,
) -> tuple[int, int, int, int, tuple[int, int]]:
    """Convert normalized bounding box to screen pixel coordinates.

    Supports:
    - Sequence of 4 elements: [ymin, xmin, ymax, xmax]
    - Dict with keys: {"ymin", "xmin", "ymax", "xmax"}, {"top", "left", "bottom", "right"}, or {"x1", "y1", "x2", "y2"}
    - Supports scale 1000 (0..1000) or scale 1.0 (0.0..1.0).
    Returns (x1, y1, x2, y2, (center_x, center_y)) with strict viewport clipping.
    """
    if isinstance(box, dict):
        if "ymin" in box and "xmin" in box and "ymax" in box and "xmax" in box:
            ymin, xmin, ymax, xmax = float(box["ymin"]), float(box["xmin"]), float(box["ymax"]), float(box["xmax"])
        elif "top" in box and "left" in box and "bottom" in box and "right" in box:
            ymin, xmin, ymax, xmax = float(box["top"]), float(box["left"]), float(box["bottom"]), float(box["right"])
        elif "x1" in box and "y1" in box and "x2" in box and "y2" in box:
            xmin, ymin, xmax, ymax = float(box["x1"]), float(box["y1"]), float(box["x2"]), float(box["y2"])
        else:
            raise ValueError(f"Unsupported dict box format: {box}")
    elif isinstance(box, (list, tuple)):
        if len(box) != 4:
            raise ValueError(f"Box sequence must contain 4 elements, got {len(box)}: {box}")
        ymin, xmin, ymax, xmax = float(box[0]), float(box[1]), float(box[2]), float(box[3])
    else:
        raise ValueError(f"Box must be a list/tuple or dict, got {type(box).__name__}: {box}")

    # Auto-detect scale if all values <= 1.05 and scale was set to 1000
    if scale == 1000.0 and max(ymin, xmin, ymax, xmax) <= 1.05:
        scale = 1.0

    # Ensure min <= max
    if ymin > ymax:
        ymin, ymax = ymax, ymin
    if xmin > xmax:
        xmin, xmax = xmax, xmin

    x1 = max(0, min(screen_width - 1, int(round((xmin / scale) * screen_width))))
    y1 = max(0, min(screen_height - 1, int(round((ymin / scale) * screen_height))))
    x2 = max(0, min(screen_width - 1, int(round((xmax / scale) * screen_width))))
    y2 = max(0, min(screen_height - 1, int(round((ymax / scale) * screen_height))))

    # Ensure bounding box has minimal dimension
    if x1 >= x2:
        x2 = min(screen_width - 1, x1 + 1)
    if y1 >= y2:
        y2 = min(screen_height - 1, y1 + 1)

    center = ((x1 + x2) // 2, (y1 + y2) // 2)
    return x1, y1, x2, y2, center


def parse_vlm_grounding_response(raw_text: str) -> dict[str, Any] | None:
    """Robustly parse VLM output JSON for grounding results."""
    text = raw_text.strip()
    if not text:
        return None

    # 1. Direct JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # 2. Extract markdown code block ```json ... ```
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if code_match:
        try:
            data = json.loads(code_match.group(1).strip())
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 3. Search for outermost JSON object with regex
    obj_match = re.search(r"\{[\s\S]*?\}", text)
    if obj_match:
        try:
            data = json.loads(obj_match.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    # 4. Fallback: extract box pattern [num, num, num, num]
    box_match = re.search(
        r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]",
        text,
    )
    if box_match:
        try:
            nums = [float(box_match.group(i)) for i in range(1, 5)]
            return {
                "found": True,
                "box": nums,
                "confidence": 0.8,
                "description": "extracted_from_regex",
            }
        except Exception:
            pass

    return None


def build_grounding_prompt(target_prompt: str) -> str:
    """Build standardized prompt for multimodal GUI element detection."""
    return (
        f"You are a mobile GUI element detector. Locate the element described as: '{target_prompt}'.\n"
        "Return a JSON object in this exact format:\n"
        "{\n"
        '  "found": true,\n'
        '  "box": {"ymin": int, "xmin": int, "ymax": int, "xmax": int},\n'
        '  "confidence": 0.95,\n'
        '  "description": "short description of located element"\n'
        "}\n"
        "Where coordinates are normalized integers on a 0 to 1000 scale relative to image height and width.\n"
        "If the target element is NOT visible on the screen or cannot be identified, return:\n"
        '{"found": false, "box": null, "confidence": 0.0, "description": "not found"}\n'
        "Do not include any extra text outside the JSON block."
    )


class VisionGroundingClient:
    """Client for multimodal visual grounding with graceful bypass support."""

    def __init__(
        self,
        config: VisionGroundingConfig | None = None,
        http_transport: Callable[[str, dict[str, str], bytes, float], bytes] | None = None,
    ) -> None:
        self.config = config or VisionGroundingConfig.from_env()
        self._http_transport = http_transport or self._default_urllib_transport

    def is_available(self) -> bool:
        """Check whether vision grounding is configured and enabled."""
        if self.config.enabled is False:
            return False
        if not self.config.api_key:
            return False
        return True

    @staticmethod
    def _default_urllib_transport(
        url: str,
        headers: dict[str, str],
        data: bytes,
        timeout: float,
    ) -> bytes:
        """Default HTTP POST transport using standard library urllib."""
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def ground(
        self,
        image_bytes: bytes,
        target_prompt: str,
        screen_size: tuple[int, int],
    ) -> GroundingResult:
        """Locate element on screen via VLM grounding.

        If API key is not configured, returns GroundingResult(ok=False, reason="vision_api_not_configured")
        without throwing an error or attempting network calls.
        """
        if not self.is_available():
            logger.debug(
                "Vision grounding skipped: API not configured or disabled (PHONE_PILOT_VISION_API_KEY is empty)"
            )
            return GroundingResult(
                ok=False,
                label=target_prompt,
                reason="vision_api_not_configured",
            )

        if not image_bytes or len(image_bytes) < 16:
            return GroundingResult(
                ok=False,
                label=target_prompt,
                reason="invalid_image_data",
            )

        width, height = screen_size
        if width <= 0 or height <= 0:
            return GroundingResult(
                ok=False,
                label=target_prompt,
                reason="invalid_screen_size",
            )

        prompt = build_grounding_prompt(target_prompt)
        b64_image = base64.b64encode(image_bytes).decode("ascii")

        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_image}",
                            },
                        },
                    ],
                }
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        }

        base = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        data = json.dumps(payload).encode("utf-8")

        try:
            resp_bytes = self._http_transport(
                url, headers, data, self.config.timeout
            )
            resp_dict = json.loads(resp_bytes.decode("utf-8"))
            choices = resp_dict.get("choices", [])
            if not choices:
                return GroundingResult(
                    ok=False,
                    label=target_prompt,
                    reason="empty_vlm_response",
                    raw_response=resp_dict,
                )

            content = choices[0].get("message", {}).get("content", "")
            parsed = parse_vlm_grounding_response(content)
            if not parsed or not parsed.get("found"):
                desc = parsed.get("description") if parsed else "parse_failure"
                return GroundingResult(
                    ok=False,
                    label=target_prompt,
                    reason=f"element_not_found: {desc}",
                    raw_response=resp_dict,
                )

            raw_box = parsed.get("box")
            if not raw_box:
                return GroundingResult(
                    ok=False,
                    label=target_prompt,
                    reason="invalid_box_coordinates",
                    raw_response=resp_dict,
                )

            x1, y1, x2, y2, center = denormalize_box(
                raw_box, width, height, scale=1000.0
            )
            confidence = float(parsed.get("confidence", 0.9))

            if isinstance(raw_box, dict):
                norm_box_int = [
                    int(round(float(raw_box.get(k, 0))))
                    for k in ("ymin", "xmin", "ymax", "xmax")
                ]
            else:
                norm_box_int = [int(round(float(v))) for v in raw_box]

            return GroundingResult(
                ok=True,
                center=center,
                bounds=(x1, y1, x2, y2),
                normalized_box=norm_box_int,
                confidence=confidence,
                label=parsed.get("description", target_prompt),
                raw_response=resp_dict,
            )

        except urllib.error.HTTPError as e:
            logger.warning("Vision grounding HTTP error: %s %s", e.code, e.reason)
            return GroundingResult(
                ok=False,
                label=target_prompt,
                reason=f"http_error_{e.code}: {e.reason}",
            )
        except Exception as e:
            logger.warning("Vision grounding request failed: %s", e)
            return GroundingResult(
                ok=False,
                label=target_prompt,
                reason=f"request_failed: {str(e)}",
            )
