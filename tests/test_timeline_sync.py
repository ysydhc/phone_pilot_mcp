"""Round 2 Unit Tests: Monotonic Timeline Sync, Schema v2 Assertions & Sub-Cases Matrix."""

from __future__ import annotations

import json
from pathlib import Path

from phone_pilot.core.manifest_generator import ensure_scene_catalog
from phone_pilot.core.timeline_sync import (
    build_synced_timeline,
    compute_threadtime_offset,
    parse_log_timestamp_epoch,
)
from phone_pilot.mock.rules import RequestLog


def test_request_log_records_monotonic_and_wall_timestamps():
    log = RequestLog()
    log.record("GET", "/v1/mock/bid?id=1", matched=True, rule_id="GET:/v1/mock/bid")
    entries = log.entries()
    assert len(entries) == 1
    assert entries[0].ts > 0.0
    assert entries[0].mono_ts > 0.0


def test_build_synced_timeline_preserves_causal_order_and_midnight_rollover():
    """R2-TC-01: `/bid` Mock request strictly precedes `didReceive` log; midnight threadtime wraps cleanly."""
    # Midnight rollover check: 23:59:59.500 -> 00:00:01.200 == 1.70s
    delta = compute_threadtime_offset("11-26 00:00:01.200  1234  1234 I Demo: ok", "11-25 23:59:59.500")
    assert delta is not None
    assert abs(delta - 1.70) < 0.01

    mono_t0 = 1000.0
    wall_t0 = parse_log_timestamp_epoch("2026-11-25T02:03:09.000+08:00")
    assert wall_t0 is not None

    # Mock `/bid` arrives at +0.62s monotonic; SDK `didReceive` arrives at +0.75s wall; `/track/imp` at +1.40s
    tl = build_synced_timeline(
        span_s=8.4,
        suite_wall_t0=wall_t0,
        suite_mono_t0=mono_t0,
        header_line="[SCENE] Start",
        footer_line="[PASS] Done",
        mock_items=[
            {"method": "GET", "path": "/v1/bid?req=1", "mono_ts": mono_t0 + 0.62},
            {"method": "GET", "path": "/v1/track/imp?seq=1", "mono_ts": mono_t0 + 1.40},
        ],
        timed_log_items=[
            (0.15, "[Client] request created"),
            (0.75, "[Client] didReceivePayload count=1"),
            (1.45, "[Client] impression recorded"),
        ],
    )
    lines = [item["line"] for item in tl]
    times = [item["t"] for item in tl]
    assert times == sorted(times)
    bid_idx = next(i for i, ln in enumerate(lines) if "/v1/bid" in ln)
    recv_idx = next(i for i, ln in enumerate(lines) if "didReceivePayload" in ln)
    assert bid_idx < recv_idx, f"Causal inversion detected: {tl}"
    assert tl[0]["t"] == 0.0 and tl[-1]["t"] == 8.4


def test_ensure_scene_catalog_extracts_schema_v2_assertions_and_subcases(tmp_path: Path):
    """R2-TC-02: Verify BusinessReport Schema v2 direct `assertions` + composite `sub_cases` extraction."""
    run_dir = tmp_path / "run_r2_sample"
    run_dir.mkdir()
    (run_dir / "run_meta.json").write_text(
        json.dumps({"script_name": "test_release_gate", "started_at": "2026-11-25T02:00:00", "device": {"platform": "ios"}}),
        encoding="utf-8",
    )
    (run_dir / "business_report.json").write_text(
        json.dumps(
            {
                "schema_version": "2",
                "cases": [{"case_id": "suite_1_edge", "title": "边角变体全遍历", "group": "edge"}],
                "business_steps": [
                    {
                        "case_id": "suite_1_edge",
                        "business_step_id": "gate_suite",
                        "status": "PASS",
                        "actual": "3/3 (12.0s)",
                        "assertions": [
                            {"dimension": "ui", "status": "PASS", "expected": "UI ok", "actual": "3/3"},
                            {"dimension": "callback_log", "status": "PASS", "expected": "Logs ok", "actual": "logs=15"},
                            {"dimension": "network", "status": "PASS", "expected": "Net ok", "actual": "reqs=8"},
                        ],
                    }
                ],
                "evidence": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "release_gate_summary.json").write_text(
        json.dumps(
            {
                "summary": [
                    {
                        "suite": "边角变体全遍历",
                        "file": "test_edge.py",
                        "elapsed_s": 12.0,
                        "detail": "3/3",
                        "raw": {
                            "ok": True,
                            "results": {
                                "edge_scenes": {
                                    "ok": True,
                                    "passed": "2/2",
                                    "scenes": {"square_video": {"ok": True}, "wh_mismatch": {"ok": False}},
                                },
                                "fault_guards": {"ok": True, "faults": {"double_show": {"ok": True}}},
                            },
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    cat = ensure_scene_catalog(run_dir, force_refresh=True)
    sc = cat["scenes"][0]
    assert len(sc["assertions"]) == 3
    assert {a["dim"] for a in sc["assertions"]} == {"ui", "callback_log", "network"}
    assert len(sc["sub_cases"]) >= 5
    # Failed sub-case is sorted to index 0 for instant anomaly visibility
    assert sc["sub_cases"][0]["name"] == "edge_scenes.wh_mismatch"
    assert sc["sub_cases"][0]["status"] == "FAIL"
