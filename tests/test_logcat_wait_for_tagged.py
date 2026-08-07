"""logcat_wait_for_tagged：轮询 + 超时兜底扫描组合 API。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from phone_pilot.script_api import logcat_wait_for_tagged
from phone_pilot.script_api.context import ScriptContext


def _ctx():
    ctx = MagicMock(spec=ScriptContext)
    return ctx


def test_hit_on_first_poll():
    ctx = _ctx()
    with patch(
        "phone_pilot.script_api.logcat.logcat_mark", return_value="08-07 10:00:00.000"
    ), patch(
        "phone_pilot.script_api.logcat.logcat_wait_for",
        return_value={"ok": True, "hits": {"AdLoaded": ["... AdLoaded ..."]}, "missing": []},
    ):
        result = logcat_wait_for_tagged(ctx, "AdLoaded", tag="AdmTag", timeout_s=5)
    assert result["ok"] is True
    assert result["matched_line"] == "... AdLoaded ..."


def test_timeout_falls_back_to_logcat_since_with_substring_semantics():
    """兜底扫描必须固定 regex=False，pattern 含特殊字符时语义一致。"""
    ctx = _ctx()
    with patch(
        "phone_pilot.script_api.logcat.logcat_wait_for",
        return_value={"ok": False, "hits": {}, "missing": ["[ERROR]"]},
    ), patch(
        "phone_pilot.script_api.logcat.logcat_mark", return_value="08-07 10:00:00.000"
    ), patch(
        "phone_pilot.script_api.logcat.logcat_since"
    ) as mock_since:
        mock_since.return_value = {
            "ok": False, "hits": [], "lines": ["... noise line 1 ...", "... noise line 2 ..."],
        }
        result = logcat_wait_for_tagged(ctx, "[ERROR]", tag="AdmTag", timeout_s=5)
    assert mock_since.call_args.kwargs.get("regex") is False
    assert result["ok"] is False
    assert result["matched_line"] is None
    assert len(result["tail"]) > 0


def test_mark_failed_returns_immediately():
    ctx = _ctx()
    with patch(
        "phone_pilot.script_api.logcat.logcat_mark", return_value=None
    ):
        result = logcat_wait_for_tagged(ctx, "x", tag="AdmTag", timeout_s=5)
    assert result["ok"] is False
    assert result["error"] == "mark_failed"
