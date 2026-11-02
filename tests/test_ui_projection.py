"""Unit tests for UI Semantic Projection & Delta Observer (phone_pilot.core.ui.projection)."""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from phone_pilot.core.ui.projection import (  # noqa: E402
    SemanticProjector,
    assert_contract_events,
    compute_state_delta,
    project_page_state,
)


def _build_realistic_page_state(num_elements: int = 60) -> dict[str, Any]:
    """Construct a realistic 60-element page_state with placeholders, texts, and ad controls."""
    elements: list[dict[str, Any]] = []
    all_texts: list[str] = []

    for i in range(num_elements):
        if i % 3 != 0:
            # Pure placeholder / background views
            elements.append(
                {
                    "index": i,
                    "type": "view" if i % 2 == 0 else "layout",
                    "label": "",
                    "center": [540, 100 + i * 20],
                    "bounds": [0, 80 + i * 20, 1080, 120 + i * 20],
                    "clickable": False,
                    "scrollable": False,
                    "resource_id": "",
                }
            )
        elif i == 15:
            elements.append(
                {
                    "index": i,
                    "type": "button",
                    "label": "激励视频广告",
                    "center": [540, 400],
                    "bounds": [200, 360, 880, 440],
                    "clickable": True,
                    "scrollable": False,
                    "resource_id": "btn_reward_video",
                }
            )
            all_texts.append("激励视频广告")
        elif i == 18:
            # Neighbor interactive button near index 15 (after placeholders 16, 17 are pruned)
            elements.append(
                {
                    "index": i,
                    "type": "button",
                    "label": "加载配置",
                    "center": [540, 460],
                    "bounds": [200, 445, 880, 500],
                    "clickable": True,
                    "scrollable": False,
                    "resource_id": "btn_load_cfg",
                }
            )
            all_texts.append("加载配置")
        elif i == 42:
            elements.append(
                {
                    "index": i,
                    "type": "button",
                    "label": "关闭广告",
                    "center": [980, 120],
                    "bounds": [940, 80, 1020, 160],
                    "clickable": True,
                    "scrollable": False,
                    "resource_id": "iv_close_ad",
                }
            )
            all_texts.append("关闭广告")
        else:
            lbl = f"普通列表项_{i}"
            elements.append(
                {
                    "index": i,
                    "type": "text",
                    "label": lbl,
                    "center": [300, 100 + i * 25],
                    "bounds": [40, 80 + i * 25, 600, 120 + i * 25],
                    "clickable": False,
                    "scrollable": False,
                    "resource_id": "",
                }
            )
            all_texts.append(lbl)

    return {
        "ok": True,
        "device_serial": "emulator-5554",
        "platform": "android",
        "activity": {"package": "com.baidu.adx.demo", "activity": ".MainActivity"},
        "screen": {"width": 1080, "height": 2400},
        "elements": elements,
        "element_count": len(elements),
        "scrollable_areas": [
            {"bounds": [0, 200, 1080, 2200], "direction": "vertical", "class": ""}
        ],
        "all_texts": all_texts,
        "screenshot_base64": "A" * 2048,
        "screenshot_annotated_base64": None,
    }


def test_focus_projection_compression_ratio_over_70_percent() -> None:
    """Verify focus projection + compaction achieves >= 70% JSON payload reduction on 50+ element page."""
    raw_state = _build_realistic_page_state(60)
    projected = project_page_state(
        raw_state,
        focus="广告|视频|关闭|Install",
        max_elements=15,
        include_neighbors=1,
        compact=True,
    )

    orig_len = len(json.dumps(raw_state, ensure_ascii=False))
    proj_len = len(json.dumps(projected, ensure_ascii=False))
    actual_reduction = 1.0 - (proj_len / orig_len)

    assert projected["ok"] is True
    assert projected["focused"] is True
    assert projected["total_elements_before"] == 60
    assert projected["compression_ratio"] >= 0.70
    assert actual_reduction >= 0.70
    assert "screenshot_base64" not in projected
    assert "screenshot_annotated_base64" not in projected


def test_preserves_original_element_indices() -> None:
    """ADR-2: Retained elements must preserve their original `index` from page_state['elements']."""
    raw_state = _build_realistic_page_state(60)
    orig_by_index = {e["index"]: e for e in raw_state["elements"]}

    projected = project_page_state(
        raw_state,
        focus=["激励视频", "关闭"],
        max_elements=10,
        include_neighbors=1,
        compact=True,
    )

    retained_indices = [e["index"] for e in projected["elements"]]
    assert 15 in retained_indices
    assert 42 in retained_indices
    # Neighbor interactive element at index 18 should also be included with neighbor score
    assert 18 in retained_indices

    for elem in projected["elements"]:
        idx = elem["index"]
        assert idx in orig_by_index
        assert elem["label"] == orig_by_index[idx]["label"]
        assert elem["center"] == orig_by_index[idx]["center"]


def test_compute_state_delta() -> None:
    """Verify compute_state_delta detects activity changes, added/removed texts, and elements."""
    before = {
        "ok": True,
        "activity": {"package": "com.demo", "activity": ".HomeActivity"},
        "all_texts": ["首页", "加载广告", "设置"],
        "elements": [
            {"index": 0, "type": "button", "label": "加载广告", "resource_id": "btn_load", "clickable": True},
            {"index": 1, "type": "button", "label": "设置", "resource_id": "btn_settings", "clickable": True},
        ],
    }
    after = {
        "ok": True,
        "activity": {"package": "com.demo", "activity": ".AdActivity"},
        "all_texts": ["加载广告", "跳过 5s", "立即安装"],
        "elements": [
            {"index": 0, "type": "button", "label": "加载广告", "resource_id": "btn_load", "clickable": True},
            {"index": 5, "type": "button", "label": "跳过 5s", "resource_id": "btn_skip", "clickable": True},
        ],
    }

    delta = compute_state_delta(before, after)
    assert delta["changed"] is True
    assert delta["activity_changed"] is True
    assert delta["added_texts"] == ["跳过 5s", "立即安装"]
    assert delta["removed_texts"] == ["首页", "设置"]
    assert len(delta["added_elements"]) == 1
    assert delta["added_elements"][0]["label"] == "跳过 5s"
    assert len(delta["removed_elements"]) == 1
    assert delta["removed_elements"][0]["label"] == "设置"

    # Identical state produces changed=False
    no_delta = compute_state_delta(after, after)
    assert no_delta["changed"] is False
    assert no_delta["activity_changed"] is False


def test_ordered_and_out_of_order_events() -> None:
    """Verify assert_contract_events validates monotonic order and detects out-of-order logs."""
    ordered_logs = [
        "10:00:01.100 I/AdSDK: event=AD_REQUEST {\"req_id\": \"r1\", \"slot_id\": \"s1\"}",
        "10:00:01.250 I/AdSDK: event=AD_LOADED {\"req_id\": \"r1\", \"slot_id\": \"s1\"}",
        "10:00:01.500 I/AdSDK: event=AD_IMPRESSION {\"req_id\": \"r1\", \"slot_id\": \"s1\"}",
        "10:00:02.000 I/AdSDK: event=AD_CLICK {\"req_id\": \"r1\", \"slot_id\": \"s1\"}",
    ]
    patterns = ["AD_REQUEST", "AD_LOADED", "AD_IMPRESSION", "AD_CLICK"]

    res_ok = assert_contract_events(ordered_logs, patterns, required_fields=["req_id", "slot_id"])
    assert res_ok["ok"] is True
    assert res_ok["matched_events"] == 4
    assert res_ok["expected_events"] == 4
    assert res_ok["out_of_order"] == 0
    assert res_ok["missing_fields"] == []
    assert res_ok["unmatched_patterns"] == []

    # Swap AD_IMPRESSION before AD_LOADED
    disordered_logs = [
        "10:00:01.100 I/AdSDK: event=AD_REQUEST {\"req_id\": \"r1\"}",
        "10:00:01.200 I/AdSDK: event=AD_IMPRESSION {\"req_id\": \"r1\"}",
        "10:00:01.300 I/AdSDK: event=AD_LOADED {\"req_id\": \"r1\"}",
    ]
    res_bad = assert_contract_events(disordered_logs, ["AD_REQUEST", "AD_LOADED", "AD_IMPRESSION"])
    assert res_bad["ok"] is False
    assert res_bad["matched_events"] == 3
    assert res_bad["out_of_order"] == 1


def test_missing_fields_detection() -> None:
    """Verify embedded JSON extraction detects missing or null required fields."""
    logs = "\n".join(
        [
            'I/AdSDK: AD_START {"req_id": "123", "slot_id": "999"}',
            'I/AdSDK: AD_REWARD {"req_id": "123", "reward_amount": null}',
        ]
    )
    patterns = [
        {"pattern": "AD_START", "required_fields": ["req_id", "slot_id"]},
        {"pattern": "AD_REWARD", "required_fields": ["req_id", "reward_amount", "currency"]},
    ]

    res = assert_contract_events(logs, patterns)
    assert res["ok"] is False
    assert res["matched_events"] == 2
    assert "reward_amount" in res["missing_fields"]
    assert "currency" in res["missing_fields"]


def test_callable_log_source_polling() -> None:
    """Verify callable log_source is polled until all patterns arrive and SemanticProjector works."""
    projector = SemanticProjector(default_max_elements=10)
    buffer: list[str] = ['I/AdSDK: STEP_1 {"status": "ok"}']
    calls = 0

    def _source() -> list[str]:
        nonlocal calls
        calls += 1
        if calls >= 2:
            buffer.append('I/AdSDK: STEP_2 {"status": "done"}')
        return list(buffer)

    res = projector.assert_events(
        _source,
        ["STEP_1", "STEP_2"],
        timeout=0.3,
        poll_interval=0.02,
        required_fields=["status"],
    )
    assert res["ok"] is True
    assert res["matched_events"] == 2
    assert calls >= 2


class TestSemanticProjection:
    """Class-based anchor matching TDD spec table."""

    def test_focus_projection_compression_ratio_over_70_percent(self) -> None:
        test_focus_projection_compression_ratio_over_70_percent()

    def test_preserves_original_element_indices(self) -> None:
        test_preserves_original_element_indices()


class TestStateDelta:
    """Class-based anchor matching TDD spec table."""

    def test_compute_state_delta(self) -> None:
        test_compute_state_delta()


class TestContractEvents:
    """Class-based anchor matching TDD spec table."""

    def test_ordered_and_out_of_order_events(self) -> None:
        test_ordered_and_out_of_order_events()

    def test_missing_fields_detection(self) -> None:
        test_missing_fields_detection()

    def test_callable_log_source_polling(self) -> None:
        test_callable_log_source_polling()
