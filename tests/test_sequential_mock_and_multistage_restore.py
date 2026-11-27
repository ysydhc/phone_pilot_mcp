"""Unit tests for universal sequential MockServer rules, POST body capture, and multi-stage scene restore."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from phone_pilot.core.scene_restore import _action_label, _start_replay_mock_server
from phone_pilot.mock import RequestLog, RuleStore
from phone_pilot.mock.server import MockServer


def _http_post(url: str, payload: dict) -> tuple[int, dict]:
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _http_get(url: str) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=5.0) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def test_rulestore_sequential_responses_and_shared_seq_group():
    """R6-TC-01a: Sequential responses advance across aliases sharing `seq_group` and clamp at tail."""
    store = RuleStore()
    seq = [
        {"status": 200, "body": {"stage": 1, "kind": "static_image"}},
        {"status": 200, "body": {"stage": 2, "kind": "motion_video"}},
    ]
    store.set_rule("POST", "/v1/feed", responses=seq, seq_group="feed_group")
    store.set_rule("GET", "/v1/feed/alias", responses=seq, seq_group="feed_group")

    r1 = store.match("POST", "/v1/feed?client=1")
    assert r1 is not None and r1.body == {"stage": 1, "kind": "static_image"}

    # Second hit via alias shares counter -> receives stage 2
    r2 = store.match("GET", "/v1/feed/alias")
    assert r2 is not None and r2.body == {"stage": 2, "kind": "motion_video"}

    # Subsequent hits clamp safely at final response instead of 404/IndexError
    r3 = store.match("POST", "/v1/feed")
    assert r3 is not None and r3.body == {"stage": 2, "kind": "motion_video"}

    store.clear()
    assert store.match("POST", "/v1/feed") is None


def test_mock_server_captures_post_body_and_serves_sequence():
    """R6-TC-01b: MockServer records POST payload body on `/requests` and serves sequential rules."""
    store = RuleStore()
    log = RequestLog()
    srv = MockServer(store, log, ctrl_port=0, data_port=0)
    srv.start()
    try:
        ctrl_base = f"http://127.0.0.1:{srv.ctrl_port}"
        data_base = f"http://127.0.0.1:{srv.data_port}"
        st, _ = _http_post(
            f"{ctrl_base}/rules",
            {
                "method": "POST",
                "path": "/v1/telemetry",
                "responses": [
                    {"status": 200, "body": {"step": "first"}},
                    {"status": 201, "body": {"step": "second"}},
                ],
                "seq_group": "telemetry_seq",
            },
        )
        assert st == 200

        s1, b1 = _http_post(f"{data_base}/v1/telemetry", {"batch": [{"id": "evt_1"}]})
        s2, b2 = _http_post(f"{data_base}/v1/telemetry", {"batch": [{"id": "evt_2"}, {"id": "evt_3"}]})
        assert (s1, b1) == (200, {"step": "first"})
        assert (s2, b2) == (201, {"step": "second"})

        _, req_dump = _http_get(f"{ctrl_base}/requests")
        reqs = req_dump["requests"]
        assert len(reqs) == 2
        assert json.loads(reqs[0]["body"]) == {"batch": [{"id": "evt_1"}]}
        assert len(json.loads(reqs[1]["body"])["batch"]) == 2
    finally:
        srv.stop()


def test_replay_mock_server_rewrites_port_in_sequential_responses(tmp_path: Path):
    """R6-TC-01c: `_start_replay_mock_server` rewrites dynamic ports inside `responses` items."""
    run_dir = tmp_path / "run_seq"
    sc_dir = run_dir / "scenes" / "dual_media"
    sc_dir.mkdir(parents=True)
    rules = [
        {
            "method": "POST",
            "path": "/api/item",
            "seq_group": "/api/item",
            "responses": [
                {"status": 200, "body": {"asset": "http://127.0.0.1:8090/media/pic.jpg"}},
                {"status": 200, "body": {"asset": "http://127.0.0.1:8090/media/clip.mp4"}},
            ],
        }
    ]
    (sc_dir / "mock_rules.json").write_text(json.dumps(rules), encoding="utf-8")

    events: list[tuple[str, dict]] = []
    port = _start_replay_mock_server(
        run_dir,
        lambda ev, **kw: events.append((ev, kw)),
        ctrl_port=0,
        data_port=0,
        scene_id="dual_media",
    )
    assert port > 0
    _, b1 = _http_post(f"http://127.0.0.1:{port}/api/item", {"req": 1})
    _, b2 = _http_post(f"http://127.0.0.1:{port}/api/item", {"req": 2})
    assert b1["asset"] == f"http://127.0.0.1:{port}/media/pic.jpg"
    assert b2["asset"] == f"http://127.0.0.1:{port}/media/clip.mp4"


def test_multistage_action_labels():
    """R6-TC-01d: Verify human-readable labels for `capture_stage` and `dismiss_modal`."""
    t1, d1 = _action_label({"type": "capture_stage", "label": "阶段1：静态图片"})
    assert "阶段1：静态图片" in t1
    assert "阶段1：静态图片" in d1

    t2, _ = _action_label({"type": "dismiss_modal"})
    assert "关闭" in t2
