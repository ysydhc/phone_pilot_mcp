"""
Semantic UI Element Navigator & Self-Healing Matcher.
LLM/VLM 驱动的跨平台语义 UI 导航与自愈定位器。

Resolution cascade:
1. Deterministic match (exact -> case-insensitive -> substring across target + aliases)
2. LLM UI-tree semantic match (handles i18n/copy changes, e.g. "激励视频" -> "Rewarded")
3. VLM visual grounding fallback (handles WebView/Canvas/custom-rendered controls)
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from phone_pilot.extensions.vision.grounding import (
    VisionGroundingClient,
    VisionGroundingConfig,
)
from phone_pilot.extensions.vision.visual_oracle import (
    VisualOracleConfig,
    parse_visual_verdict_json,
)

logger = logging.getLogger("phone_pilot.vision.semantic_nav")


@dataclass
class NavMatchResult:
    """Result of semantic UI element resolution."""

    ok: bool
    center: tuple[int, int] | None = None
    matched_label: str = ""
    method: str = ""  # exact | alias | substring | llm_tree | vlm_grounding
    healed: bool = False
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "center": list(self.center) if self.center else None,
            "matched_label": self.matched_label,
            "method": self.method,
            "healed": self.healed,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def _element_label(elem: dict[str, Any]) -> str:
    for key in ("label", "text", "name", "title", "content_desc", "desc"):
        val = elem.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _element_center(elem: dict[str, Any]) -> tuple[int, int] | None:
    center = elem.get("center")
    if isinstance(center, (list, tuple)) and len(center) == 2:
        try:
            return (int(center[0]), int(center[1]))
        except (TypeError, ValueError):
            pass
    bounds = elem.get("bounds")
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        try:
            x1, y1, x2, y2 = [float(v) for v in bounds]
            return (int(round((x1 + x2) / 2.0)), int(round((y1 + y2) / 2.0)))
        except (TypeError, ValueError):
            pass
    if isinstance(bounds, dict):
        try:
            if {"x", "y", "width", "height"} <= set(bounds.keys()):
                cx = float(bounds["x"]) + float(bounds["width"]) / 2.0
                cy = float(bounds["y"]) + float(bounds["height"]) / 2.0
                return (int(round(cx)), int(round(cy)))
            if {"left", "top", "right", "bottom"} <= set(bounds.keys()):
                cx = (float(bounds["left"]) + float(bounds["right"])) / 2.0
                cy = (float(bounds["top"]) + float(bounds["bottom"])) / 2.0
                return (int(round(cx)), int(round(cy)))
        except (TypeError, ValueError):
            pass
    return None


class SemanticNavigator:
    """Three-tier UI element resolver (Deterministic -> LLM Tree -> VLM Grounding)."""

    def __init__(
        self,
        config: VisualOracleConfig | None = None,
        transport: Callable[[str, dict[str, str], bytes, float], bytes] | None = None,
    ) -> None:
        self.config = config or VisualOracleConfig.from_env()
        self._transport = transport or self._default_urllib_transport

    @classmethod
    def from_env(cls) -> SemanticNavigator:
        return cls(VisualOracleConfig.from_env())

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

    def _match_deterministic(
        self,
        target: str,
        aliases: Sequence[str],
        ui_elements: Sequence[dict[str, Any]],
    ) -> NavMatchResult | None:
        candidates = [target] + [a for a in aliases if a and a != target]
        normalized_cands = [(c, c.strip().lower()) for c in candidates if c.strip()]

        # 1. Exact case-insensitive match
        for idx, (raw_c, low_c) in enumerate(normalized_cands):
            for elem in ui_elements:
                lbl = _element_label(elem)
                center = _element_center(elem)
                if not lbl or center is None:
                    continue
                if lbl.strip().lower() == low_c:
                    return NavMatchResult(
                        ok=True,
                        center=center,
                        matched_label=lbl,
                        method="exact" if idx == 0 else "alias",
                        healed=False,
                        confidence=1.0,
                        reason=f"matched candidate {raw_c!r}",
                    )

        # 2. Substring match
        for raw_c, low_c in normalized_cands:
            for elem in ui_elements:
                lbl = _element_label(elem)
                center = _element_center(elem)
                if not lbl or center is None:
                    continue
                if low_c in lbl.strip().lower():
                    return NavMatchResult(
                        ok=True,
                        center=center,
                        matched_label=lbl,
                        method="substring",
                        healed=False,
                        confidence=0.85,
                        reason=f"substring matched {raw_c!r} in {lbl!r}",
                    )
        return None

    def _match_llm_tree(
        self,
        target: str,
        ui_elements: Sequence[dict[str, Any]],
    ) -> NavMatchResult | None:
        if self.config.enabled is False or not self.config.api_key:
            return None

        indexed: list[tuple[int, str, tuple[int, int], str]] = []
        for idx, elem in enumerate(ui_elements):
            lbl = _element_label(elem)
            center = _element_center(elem)
            if not lbl or center is None:
                continue
            etype = str(elem.get("type") or elem.get("class") or "element")
            indexed.append((idx, lbl, center, etype))
        if not indexed:
            return None

        lines = [
            f"[{idx}] type={etype} label={lbl!r} center={list(center)}"
            for idx, lbl, center, etype in indexed[:80]
        ]
        prompt = (
            "You are a mobile UI navigation assistant.\n"
            f"Target UI element to click: {target!r}\n"
            "Even if the app language changed (e.g. Chinese <-> English) or copy was slightly renamed, "
            "pick the single best matching UI element index from the list below.\n"
            "If none matches, return index: -1.\n\n"
            "Elements:\n"
            + "\n".join(lines)
            + "\n\nReply ONLY with valid JSON:\n"
            '{"index": <int>, "confidence": <0.0..1.0>, "reason": "<brief reason>"}'
        )

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 256,
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
            if not parsed:
                return None
            picked_idx = int(parsed.get("index", -1))
            if picked_idx < 0:
                return None
            for idx, lbl, center, _ in indexed:
                if idx == picked_idx:
                    return NavMatchResult(
                        ok=True,
                        center=center,
                        matched_label=lbl,
                        method="llm_tree",
                        healed=True,
                        confidence=float(parsed.get("confidence", 0.8)),
                        reason=str(parsed.get("reason") or "llm_semantic_match"),
                    )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("SemanticNavigator LLM tree match failed: %s", exc)
        return None

    def _match_vlm_grounding(
        self,
        target: str,
        screenshot_bytes: bytes | None,
        screen_size: tuple[int, int] | None,
    ) -> NavMatchResult | None:
        if (
            self.config.enabled is False
            or not self.config.api_key
            or not screenshot_bytes
            or not screen_size
        ):
            return None
        g_cfg = VisionGroundingConfig(
            enabled=self.config.enabled,
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            model=self.config.model,
            timeout=self.config.timeout,
        )
        client = VisionGroundingClient(config=g_cfg, transport=self._transport)
        res = client.ground(screenshot_bytes, target, screen_size)
        if res.ok and res.center is not None:
            return NavMatchResult(
                ok=True,
                center=res.center,
                matched_label=target,
                method="vlm_grounding",
                healed=True,
                confidence=res.confidence or 0.75,
                reason=res.reason or "vlm_visual_grounding",
            )
        return None

    def locate(
        self,
        target: str,
        *,
        aliases: Sequence[str] = (),
        ui_elements: Sequence[dict[str, Any]] = (),
        screenshot_bytes: bytes | None = None,
        screen_size: tuple[int, int] | None = None,
    ) -> NavMatchResult:
        """Locate target UI element using deterministic -> LLM tree -> VLM grounding cascade."""
        hit = self._match_deterministic(target, aliases, ui_elements)
        if hit is not None:
            return hit

        hit = self._match_llm_tree(target, ui_elements)
        if hit is not None:
            return hit

        hit = self._match_vlm_grounding(target, screenshot_bytes, screen_size)
        if hit is not None:
            return hit

        return NavMatchResult(
            ok=False,
            reason=f"element_not_found: {target!r}",
        )
