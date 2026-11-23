"""Unit tests for P1 VisualOracle and SemanticNavigator."""

from __future__ import annotations

import io
import json
import pytest
from PIL import Image

from phone_pilot.extensions.vision.visual_oracle import (
    VisualOracle,
    VisualOracleConfig,
    inspect_image_pixels,
    parse_visual_verdict_json,
)
from phone_pilot.extensions.vision.semantic_nav import SemanticNavigator


def _make_png(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_inspect_image_pixels_detects_solid_black_and_white():
    black = inspect_image_pixels(_make_png((0, 0, 0)))
    assert black["ok"] is True
    assert black["solid_black"] is True
    assert black["solid_white"] is False

    white = inspect_image_pixels(_make_png((255, 255, 255)))
    assert white["ok"] is True
    assert white["solid_white"] is True
    assert white["solid_black"] is False


def test_visual_oracle_graceful_bypass_without_key(monkeypatch: pytest.MonkeyPatch):
    for k in (
        "PHONE_PILOT_VISION_API_KEY",
        "OPENAI_API_KEY",
        "PHONE_PILOT_VISION_ENABLED",
    ):
        monkeypatch.delenv(k, raising=False)
    oracle = VisualOracle.from_env()
    res = oracle.evaluate(_make_png((0, 0, 0)), case_id="case_1")
    assert res.verdict == "SKIPPED"
    assert res.reason == "vision_api_not_configured"
    assert any("纯黑" in d for d in res.defects)


def test_visual_oracle_vlm_pass_and_fail_via_mock_transport():
    def ok_transport(url, headers, data, timeout):
        body = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "verdict": "PASS",
                                "confidence": 0.96,
                                "summary": "尾帧卡片正常展示，无黑屏或遮挡。",
                                "defects": [],
                                "highlights": ["Download Button", "Endcard Banner"],
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        return json.dumps(body).encode("utf-8")

    cfg = VisualOracleConfig(api_key="sk-test", model="gbu-gemini-4-argon")
    oracle = VisualOracle(config=cfg, transport=ok_transport)
    verdict = oracle.evaluate(
        _make_png((120, 140, 200)),
        case_id="cc_retention_dialog_forced",
        phase="T3_endcard",
        task_context={"card_id": "adx-sdk-547", "title": "Reward Tailframe Fix"},
    )
    assert verdict.verdict == "PASS"
    assert verdict.confidence == pytest.approx(0.96)
    assert "尾帧卡片正常展示" in verdict.summary
    assert verdict.model == "gbu-gemini-4-argon"


def test_parse_visual_verdict_json_strips_fences():
    raw = '```json\n{"verdict": "FAIL", "confidence": 0.9, "summary": "黑屏"}\n```'
    parsed = parse_visual_verdict_json(raw)
    assert parsed is not None
    assert parsed["verdict"] == "FAIL"


def test_semantic_navigator_deterministic_and_llm_tree_self_heal():
    elements = [
        {"label": "Banner", "center": [200, 300], "type": "Button"},
        {"label": "Rewarded", "center": [613, 955], "type": "Button"},
    ]

    # 1. Alias match
    nav_no_llm = SemanticNavigator(config=VisualOracleConfig(api_key=None))
    r1 = nav_no_llm.locate("激励视频", aliases=["Rewarded"], ui_elements=elements)
    assert r1.ok is True
    assert r1.center == (613, 955)
    assert r1.method == "alias"
    assert r1.healed is False

    # 2. LLM UI-tree semantic match when no alias is given
    def llm_transport(url, headers, data, timeout):
        return json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "index": 1,
                                    "confidence": 0.92,
                                    "reason": "Rewarded is the English equivalent of 激励视频",
                                }
                            )
                        }
                    }
                ]
            }
        ).encode("utf-8")

    nav_llm = SemanticNavigator(
        config=VisualOracleConfig(api_key="sk-test", model="gbu-gemini-4-argon"),
        transport=llm_transport,
    )
    r2 = nav_llm.locate("激励视频", ui_elements=elements)
    assert r2.ok is True
    assert r2.center == (613, 955)
    assert r2.method == "llm_tree"
    assert r2.healed is True
