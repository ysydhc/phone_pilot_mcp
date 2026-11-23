"""
VLM Multimodal Visual Acceptance Oracle & Deterministic Pixel Sanity Checker.
多模态视觉验收裁判与确定性像素异常预检引擎。

Provides:
- Deterministic pixel check (solid black / white / flat frame detection via Pillow).
- OpenAI-compatible multimodal VLM judgment against task_context (title / problem / expected).
- Graceful bypass when API key is unavailable (zero error, status="SKIPPED").
- Dependency-injected transport for deterministic unit testing.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("phone_pilot.vision.visual_oracle")


@dataclass
class VisualOracleConfig:
    """Configuration for VLM Visual Acceptance Oracle."""

    enabled: bool | None = None
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = None
    model: str = "gpt-4o"
    timeout: float = 30.0
    fail_on_solid_screen: bool = False

    @classmethod
    def from_env(cls) -> VisualOracleConfig:
        """Load VLM config from PHONE_PILOT_VISION_* with OPENAI_* fallback."""
        env_enabled = os.environ.get("PHONE_PILOT_VISION_ENABLED")
        enabled: bool | None = None
        if env_enabled is not None:
            enabled = env_enabled.strip().lower() in ("1", "true", "yes", "on")

        api_key = os.environ.get("PHONE_PILOT_VISION_API_KEY", "").strip() or None
        base_url = os.environ.get("PHONE_PILOT_VISION_BASE_URL", "").strip()
        model = os.environ.get("PHONE_PILOT_VISION_MODEL", "").strip()

        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip() or None
        if not base_url:
            base_url = (
                os.environ.get("OPENAI_BASE_URL", "").strip()
                or "https://api.openai.com/v1"
            )
        if not model:
            model = os.environ.get("OPENAI_VISION_MODEL", "").strip()
        if not model:
            model = "gbu-gemini-3.8-flash" if "gburouter" in base_url else "gpt-4o"

        raw_timeout = os.environ.get("PHONE_PILOT_VISION_TIMEOUT", "").strip()
        timeout = float(raw_timeout) if raw_timeout else 30.0

        return cls(
            enabled=enabled,
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )


@dataclass
class VisualVerdict:
    """Structured result from VisualOracle."""

    case_id: str
    phase: str = ""
    verdict: str = "SKIPPED"  # PASS | FAIL | INCONCLUSIVE | SKIPPED
    confidence: float = 0.0
    summary: str = ""
    defects: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    pixel_stats: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    latency_ms: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "phase": self.phase,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "summary": self.summary,
            "defects": list(self.defects),
            "highlights": list(self.highlights),
            "pixel_stats": dict(self.pixel_stats),
            "model": self.model,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
        }


def inspect_image_pixels(image_bytes: bytes) -> dict[str, Any]:
    """Inspect basic pixel statistics (brightness & std-dev) using Pillow if available."""
    if not image_bytes:
        return {"ok": False, "error": "empty_image_bytes"}
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return {"ok": False, "error": "pillow_not_installed"}

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            rgb = img.convert("RGB")
            w, h = rgb.size
            stat = ImageStat.Stat(rgb)
            mean_rgb = [round(v, 2) for v in stat.mean]
            std_rgb = [round(v, 2) for v in stat.stddev]
            mean_luma = round(sum(stat.mean) / 3.0, 2)
            max_std = round(max(stat.stddev), 2)
            solid_black = mean_luma < 8.0 and max_std < 4.0
            solid_white = mean_luma > 247.0 and max_std < 4.0
            solid_flat = max_std < 3.0
            return {
                "ok": True,
                "width": w,
                "height": h,
                "mean_rgb": mean_rgb,
                "std_rgb": std_rgb,
                "mean_luma": mean_luma,
                "max_std": max_std,
                "solid_black": solid_black,
                "solid_white": solid_white,
                "solid_flat": solid_flat,
            }
    except Exception as exc:
        return {"ok": False, "error": f"image_decode_error: {exc}"}


def parse_visual_verdict_json(raw_text: str) -> dict[str, Any] | None:
    """Parse VLM JSON response, stripping markdown code fences if present."""
    if not raw_text or not isinstance(raw_text, str):
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def build_visual_oracle_prompt(
    *,
    case_id: str,
    title: str = "",
    expected: str = "",
    task_context: dict[str, Any] | None = None,
    ui_summary: str = "",
    pixel_stats: dict[str, Any] | None = None,
    has_before_image: bool = False,
) -> str:
    """Build a domain-agnostic multimodal QA prompt for visual acceptance."""
    ctx_lines = [f"- Case ID: {case_id}"]
    if title:
        ctx_lines.append(f"- Case Title: {title}")
    if expected:
        ctx_lines.append(f"- Expected Outcome: {expected}")
    if task_context:
        if task_context.get("card_id"):
            ctx_lines.append(f"- Requirement / Bug Card: {task_context['card_id']}")
        if task_context.get("title"):
            ctx_lines.append(f"- Task Title: {task_context['title']}")
        if task_context.get("problem"):
            ctx_lines.append(f"- Problem Statement: {task_context['problem']}")
        if task_context.get("expected"):
            ctx_lines.append(f"- Task Acceptance Criteria: {task_context['expected']}")
    if ui_summary:
        ctx_lines.append(f"- UI Tree Summary: {ui_summary[:800]}")
    if pixel_stats and pixel_stats.get("ok"):
        ctx_lines.append(
            f"- Pixel Stats: size={pixel_stats.get('width')}x{pixel_stats.get('height')}, "
            f"mean_luma={pixel_stats.get('mean_luma')}, max_std={pixel_stats.get('max_std')}, "
            f"solid_black={pixel_stats.get('solid_black')}, solid_white={pixel_stats.get('solid_white')}"
        )

    compare_hint = (
        "Image 1 is the BEFORE/baseline screenshot and Image 2 is the AFTER/target final-state screenshot. "
        if has_before_image
        else "The attached image is the target final-state screenshot from the device. "
    )

    return (
        "You are an objective mobile QA visual acceptance judge.\n"
        f"{compare_hint}"
        "Evaluate whether the UI satisfies the expected outcome and is free of visual defects.\n\n"
        "Context:\n"
        + "\n".join(ctx_lines)
        + "\n\nCheck specifically for:\n"
        "1. Black screen, white screen, frozen loading spinner, or crashed/error dialog (e.g. Load Failed / didFailToReceiveAdWithError / no ad fill).\n"
        "2. Layout corruption: text truncation/ellipsis clipping, overlapping/ghosting controls, safe-area/notch occlusion.\n"
        "3. Alignment with Expected Outcome & Task Acceptance Criteria.\n\n"
        "Reply ONLY with valid JSON matching this schema:\n"
        "{\n"
        '  "verdict": "PASS" | "FAIL" | "INCONCLUSIVE",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "summary": "简明中文视觉验收结论（1-2句话）",\n'
        '  "defects": ["发现的视觉或功能缺陷，无则为空数组"],\n'
        '  "highlights": ["画面中关键可见元素"]\n'
        "}"
    )


class VisualOracle:
    """Multimodal VLM visual acceptance judge with deterministic pixel fallback."""

    def __init__(
        self,
        config: VisualOracleConfig | None = None,
        transport: Callable[[str, dict[str, str], bytes, float], bytes] | None = None,
    ) -> None:
        self.config = config or VisualOracleConfig.from_env()
        self._transport = transport or self._default_urllib_transport

    @classmethod
    def from_env(cls) -> VisualOracle:
        return cls(VisualOracleConfig.from_env())

    def is_available(self) -> bool:
        if self.config.enabled is False:
            return False
        return bool(self.config.api_key)

    @staticmethod
    def _default_urllib_transport(
        url: str,
        headers: dict[str, str],
        data: bytes,
        timeout: float,
    ) -> bytes:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def evaluate(
        self,
        image_bytes: bytes,
        *,
        case_id: str,
        phase: str = "",
        title: str = "",
        expected: str = "",
        task_context: dict[str, Any] | None = None,
        before_image_bytes: bytes | None = None,
        ui_summary: str = "",
    ) -> VisualVerdict:
        t0 = time.monotonic()
        pixel_stats = inspect_image_pixels(image_bytes)
        pixel_defects: list[str] = []
        if pixel_stats.get("solid_black"):
            pixel_defects.append("像素预检告警：画面呈纯黑（mean_luma < 8, max_std < 4）")
        elif pixel_stats.get("solid_white"):
            pixel_defects.append("像素预检告警：画面呈纯白（mean_luma > 247, max_std < 4）")

        if not image_bytes or len(image_bytes) < 16:
            return VisualVerdict(
                case_id=case_id,
                phase=phase,
                verdict="FAIL" if self.config.fail_on_solid_screen else "SKIPPED",
                summary="截图数据为空或损坏，无法执行视觉裁判",
                defects=["invalid_image_data"],
                pixel_stats=pixel_stats,
                reason="invalid_image_data",
            )

        if not self.is_available():
            verdict = "FAIL" if (self.config.fail_on_solid_screen and pixel_defects) else "SKIPPED"
            summary = (
                pixel_defects[0]
                if pixel_defects
                else "未配置视觉大模型 API Key，已降级为跳过 VLM 裁判"
            )
            return VisualVerdict(
                case_id=case_id,
                phase=phase,
                verdict=verdict,
                summary=summary,
                defects=pixel_defects,
                pixel_stats=pixel_stats,
                reason="vision_api_not_configured",
            )

        prompt = build_visual_oracle_prompt(
            case_id=case_id,
            title=title,
            expected=expected,
            task_context=task_context,
            ui_summary=ui_summary,
            pixel_stats=pixel_stats,
            has_before_image=bool(before_image_bytes and len(before_image_bytes) >= 16),
        )

        content_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if before_image_bytes and len(before_image_bytes) >= 16:
            b64_before = base64.b64encode(before_image_bytes).decode("ascii")
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_before}"},
                }
            )
        b64_target = base64.b64encode(image_bytes).decode("ascii")
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64_target}"},
            }
        )

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": content_parts}],
            "temperature": 0.1,
            "max_tokens": 768,
        }
        base = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "User-Agent": "curl/8.4.0",
        }

        try:
            raw_resp = self._transport(
                url, headers, json.dumps(payload).encode("utf-8"), self.config.timeout
            )
            resp_obj = json.loads(raw_resp.decode("utf-8"))
            choices = resp_obj.get("choices") or []
            raw_content = ""
            if choices and isinstance(choices[0], dict):
                raw_content = (choices[0].get("message") or {}).get("content") or ""
            parsed = parse_visual_verdict_json(raw_content)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if not parsed:
                return VisualVerdict(
                    case_id=case_id,
                    phase=phase,
                    verdict="INCONCLUSIVE",
                    summary="VLM 返回内容无法解析为标准 JSON",
                    defects=pixel_defects,
                    pixel_stats=pixel_stats,
                    model=self.config.model,
                    latency_ms=elapsed_ms,
                    reason="unparseable_vlm_response",
                )

            v_str = str(parsed.get("verdict") or "INCONCLUSIVE").strip().upper()
            if v_str not in ("PASS", "FAIL", "INCONCLUSIVE"):
                v_str = "INCONCLUSIVE"
            defects = [str(d) for d in (parsed.get("defects") or []) if str(d).strip()]
            for pd in pixel_defects:
                if pd not in defects:
                    defects.append(pd)
            highlights = [
                str(h) for h in (parsed.get("highlights") or []) if str(h).strip()
            ]
            try:
                conf = float(parsed.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0

            return VisualVerdict(
                case_id=case_id,
                phase=phase,
                verdict=v_str,
                confidence=max(0.0, min(1.0, conf)),
                summary=str(parsed.get("summary") or "").strip(),
                defects=defects,
                highlights=highlights,
                pixel_stats=pixel_stats,
                model=self.config.model,
                latency_ms=elapsed_ms,
                reason="ok",
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.warning("VisualOracle request failed: %s", exc)
            return VisualVerdict(
                case_id=case_id,
                phase=phase,
                verdict="SKIPPED",
                summary=f"VLM 请求异常已降级跳过: {exc}",
                defects=pixel_defects,
                pixel_stats=pixel_stats,
                model=self.config.model,
                latency_ms=elapsed_ms,
                reason=f"vlm_request_error: {exc}",
            )
