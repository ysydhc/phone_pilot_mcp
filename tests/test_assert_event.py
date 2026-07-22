"""Tests for assert_event whole-word structured log assertion."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from phone_pilot.script_api.logcat import assert_event

HDR = "07-22 10:00:01.000  1234  1234 D AdTest: "


def _ctx(log_lines: list[str], mark: str | None = "07-22 10:00:00.000"):
    driver = MagicMock()
    driver.logcat_time.return_value = mark
    driver.read_log.return_value = "\n".join(HDR + ln for ln in log_lines)
    return SimpleNamespace(driver=driver, platform="android")


def test_exact_event_word_no_substring_hit():
    # B9: LOAD_FAIL 不得命中 LOAD_FAILURE
    ctx = _ctx(["event=LOAD_FAILURE code=1"])
    r = assert_event(ctx, "LOAD_FAIL", timeout_s=0)
    assert r["ok"] is False


def test_kv_word_boundary_dash_dot():
    # B10: type=video 不得命中 video_xxx / video-x / video.mp4
    for noise in ("type=video_xxx", "type=video-x", "type=video.mp4"):
        ctx = _ctx([f"event=MATERIAL {noise}"])
        r = assert_event(ctx, "MATERIAL", {"type": "video"}, timeout_s=0)
        assert r["ok"] is False, noise


def test_hit_at_line_start_and_end():
    # B11: k=v 在行首/行尾均命中
    ctx = _ctx(["event=MATERIAL type=video"])
    r = assert_event(ctx, "MATERIAL", {"type": "video"}, timeout_s=0)
    assert r["ok"] is True
    assert "type=video" in r["line"]


def test_event_with_regex_metachar():
    # B12: event 含 . 按字面匹配
    ctx = _ctx(["event=REQ.FAIL code=500"])
    r = assert_event(ctx, "REQ.FAIL", {"code": "500"}, timeout_s=0)
    assert r["ok"] is True
    ctx2 = _ctx(["event=REQXFAIL code=500"])  # . 不作通配
    assert assert_event(ctx2, "REQ.FAIL", timeout_s=0)["ok"] is False


def test_mark_failed_no_silent_fallback():
    # B24 / ADR-13: logcat_time 返回 None → 立即 mark_failed
    ctx = _ctx(["event=X"], mark=None)
    r = assert_event(ctx, "X", timeout_s=0)
    assert r["ok"] is False
    assert r["reason"] == "mark_failed"
    ctx.driver.read_log.assert_not_called()  # 绝不静默降级去读日志


def test_empty_kv_matches_event_only():
    # B34
    ctx = _ctx(["event=APP_READY page=main"])
    assert assert_event(ctx, "APP_READY", timeout_s=0)["ok"] is True
    assert assert_event(ctx, "APP_READY", {}, timeout_s=0)["ok"] is True


def test_first_matching_line_returned():
    ctx = _ctx(["event=IMP seq=1", "event=IMP seq=2"])
    r = assert_event(ctx, "IMP", timeout_s=0)
    assert "seq=1" in r["line"]


def test_failure_returns_tail():
    ctx = _ctx([f"noise {i}" for i in range(30)])
    r = assert_event(ctx, "NEVER", timeout_s=0)
    assert r["ok"] is False
    assert len(r["tail"]) == 20  # 最后 20 行证据


def test_timeout_zero_single_read():
    # B35: timeout_s=0 只读一次
    ctx = _ctx([])
    assert_event(ctx, "X", timeout_s=0)
    assert ctx.driver.read_log.call_count == 1
