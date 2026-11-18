"""Offline acceptance tests; no driver detection, device, or shared venv writes."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from phone_pilot.core.business_report import load_business_report
from phone_pilot.core.run_store import RunSession
from phone_pilot.script_api import ScriptContext


def declarations(two=False):
    steps = [
        {
            "business_step_id": "load",
            "expected": "ad loaded",
            "required_evidence": ["screenshot"],
        }
    ]
    if two:
        steps.append({"business_step_id": "close", "expected": "return to app"})
    return [{"case_id": "TC1", "title": "reward video", "steps": steps}]


@pytest.fixture
def session(tmp_path):
    s = RunSession(tmp_path / "run")
    for directory in (s.screenshots_dir, s.recordings_dir, s.logcat_dir, s.meminfo_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return s


def tiny_png():
    import cv2
    import numpy as np

    ok, output = cv2.imencode(".png", np.full((2, 2, 3), (0, 0, 255), dtype=np.uint8))
    assert ok
    return output.tobytes()


def collect_pass(s):
    screen = s.add_evidence("screenshot", data=tiny_png(), source="offline fixture")
    for dimension in ("ui", "callback_log", "network"):
        evidence = (
            screen
            if dimension == "ui"
            else s.add_evidence(
                dimension,
                data="contextual event",
                metadata={"log_range": {"start": 1, "end": 2}, "video_offset_s": 2.1},
            )
        )
        s.add_assertion(
            dimension,
            "PASS",
            "specific expectation",
            "specific observation",
            evidence_refs=[evidence["evidence_id"]],
        )


def test_declaration_denominator_and_exit_zero_not_business_pass(session):
    session.declare_cases(declarations(two=True))
    session.begin_business_step("TC1", "load")
    session.end_business_step()
    meta = json.loads(session.finalize(exit_code=0).read_text())
    summary = load_business_report(session.run_dir)["summary"]
    assert meta["status"] == "passed"
    assert meta["business_status"] == "INCONCLUSIVE"
    assert summary["planned_steps"] == 2
    assert summary["executed_steps"] == 1
    assert summary["not_run"] == 1
    assert summary["final_completion_rate"] == 0


def test_pass_timestamps_monotonic_and_tool_parent(session):
    session.declare_cases(declarations())
    first = session.begin_business_step("TC1", "load", action="load ad")
    collect_pass(session)
    session.add_step(1, "find_text")
    session.add_step(1, "find_text")  # legacy callers reset tool counters
    end = session.end_business_step(actual="ad loaded")
    assert end["status"] == "PASS"
    assert end["duration_ms"] >= 0
    assert end["monotonic_end_ns"] >= first["monotonic_start_ns"]
    assert dt.datetime.fromisoformat(end["started_at"]).utcoffset() is not None
    assert session._steps[0]["parent_case_id"] == "TC1"
    assert session._steps[0]["parent_step_id"] == "load"
    assert session._steps[0]["parent_attempt_id"] == "a1"
    assert session._steps[0]["tool_call_id"] != session._steps[1]["tool_call_id"]
    assert end["metadata"]["environment_reason"]
    assert session.business_report["summary"]["final_completion_rate"] == 1


def test_retry_preserves_first_failure_and_evidence(session):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    first_evidence = session.add_evidence("screenshot", data=b"failure-original")
    session.end_business_step(
        "FAIL", "timeout", error_message="load timeout", failure_class="business"
    )
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    session.end_business_step()
    report = session.business_report
    assert len(report["business_steps"]) == 2
    assert report["business_steps"][0]["error_message"] == "load timeout"
    assert (
        session.run_dir / first_evidence["path"]
    ).read_bytes() == b"failure-original"
    assert report["summary"]["first_pass_rate"] == 0
    assert report["summary"]["final_completion_rate"] == 1
    assert report["summary"]["recovered"] == 1
    assert report["summary"]["step_results"][0]["recovery_status"] == "RECOVERED"


@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_reload_rechecks_evidence_integrity(session, mutation):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    session.end_business_step()
    path = session.run_dir / session.business_report["evidence"][0]["path"]
    if mutation == "missing":
        # Resolved pytest-owned path only, not a user file.
        assert path.resolve().is_relative_to(session.run_dir.resolve())
        path.unlink()
    else:
        path.write_bytes(b"altered")
    report = load_business_report(session.run_dir)
    assert report["business_steps"][0]["status"] == "NOT_COLLECTED"
    assert report["summary"]["status"] != "PASS"
    assert report["summary"]["evidence_complete_rate"] == 0


def test_missing_refs_or_required_dimension_cannot_pass(session):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    session.add_evidence("screenshot", capture_error="capture unavailable")
    session.add_assertion("ui", "PASS", "visible", "visible", evidence_refs=["missing"])
    assert session.end_business_step()["status"] == "NOT_COLLECTED"


def test_na_must_be_declared_and_reasoned(session):
    decl = declarations()
    decl[0]["steps"][0].update(
        required_dimensions=["ui"],
        not_applicable={
            "callback_log": "visual-only step",
            "network": "no network action",
        },
    )
    session.declare_cases(decl)
    session.begin_business_step("TC1", "load")
    with pytest.raises(ValueError, match="N/A"):
        session.add_assertion("ui", "NOT_APPLICABLE", "", "")
    e = session.add_evidence("screenshot", data=tiny_png())
    session.add_assertion(
        "ui", "PASS", "visible", "visible", evidence_refs=[e["evidence_id"]]
    )
    assert session.end_business_step()["status"] == "PASS"
    with pytest.raises(ValueError):
        session.declare_cases(decl)


def test_declaration_copy_no_post_failure_na_edit(session):
    decl = declarations()
    session.declare_cases(decl)
    decl[0]["steps"][0]["required_dimensions"] = []
    session.begin_business_step("TC1", "load")
    with pytest.raises(ValueError):
        session.add_assertion("network", "NOT_APPLICABLE", "", "")
    assert session.end_business_step()["status"] != "PASS"


def test_context_exception_not_swallowed_and_capture_failure_explicit(session):
    ctx = ScriptContext(
        _session=session,
        _driver=SimpleNamespace(
            screen=SimpleNamespace(
                screenshot=lambda: (_ for _ in ()).throw(
                    RuntimeError("camera unavailable")
                )
            )
        ),
    )
    ctx.declare_cases(declarations())
    business_error = RuntimeError("reward did not arrive")
    with pytest.raises(RuntimeError) as caught:
        with ctx.business_step("TC1", "load"):
            raise business_error
    assert caught.value is business_error
    report = session.business_report
    assert report["business_steps"][0]["status"] == "FAIL"
    assert report["evidence"][0]["capture_error"] == "camera unavailable"
    assert report["evidence"][0]["metadata"]["phase"] == "failure"


def test_context_capture_is_original_and_not_assertion(session):
    ctx = ScriptContext(
        _session=session,
        _driver=SimpleNamespace(
            screen=SimpleNamespace(screenshot=lambda: b"unannotated")
        ),
    )
    ctx.declare_cases(declarations())
    with ctx.business_step(
        "TC1", "load", capture_before=True, capture_after=True
    ) as step:
        step.actual = "command accepted"
    assert step.result["status"] == "NOT_COLLECTED"
    assert len(session.business_report["evidence"]) == 2
    paths = [e["path"] for e in session.business_report["evidence"]]
    assert paths[0] != paths[1]
    assert all((session.run_dir / p).read_bytes() == b"unannotated" for p in paths)


def test_screenshots_never_overwrite_repeated_names(session):
    first = session.save_screenshot("failure", b"first")
    second = session.save_screenshot("failure", b"second")
    session.add_step(1, "same", png_bytes=b"a")
    session.add_step(1, "same", png_bytes=b"b")
    assert first != second
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert session._steps[0]["screenshot"] != session._steps[1]["screenshot"]


def test_scope_path_timezone_and_duplicate_attempt_rejected(session, tmp_path):
    session.declare_cases(declarations())
    with pytest.raises(ValueError, match="not declared"):
        session.begin_business_step("TC1", "unknown")
    session.begin_business_step("TC1", "load")
    with pytest.raises(RuntimeError, match="nested"):
        session.begin_business_step("TC1", "load")
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    with pytest.raises(ValueError, match="inside"):
        session.add_evidence("log", path=outside)
    with pytest.raises(ValueError, match="timezone"):
        session.add_evidence("log", data="x", capture_at="2026-11-17T12:00:00")
    session.end_business_step("FAIL")
    with pytest.raises(ValueError, match="unique"):
        session.begin_business_step("TC1", "load", attempt_id="a1")


def test_relative_run_dir_data_evidence(tmp_path, monkeypatch):
    from phone_pilot.core.business_report import BusinessReport

    monkeypatch.chdir(tmp_path)
    report = BusinessReport("relative_run")
    report.declare_cases(declarations())
    report.begin_business_step("TC1", "load")
    evidence = report.add_evidence("screenshot", data=b"original")
    assert evidence["status"] == "PASS"
    assert (report.run_dir / evidence["path"]).read_bytes() == b"original"


def test_early_declaration_infers_source_and_refreshes_device(tmp_path, monkeypatch):
    import phone_pilot.script_api.context as context

    script = tmp_path / "reward_case.py"
    script.write_text("# offline fixture")
    monkeypatch.setattr(context.sys, "argv", [str(script)])
    monkeypatch.setattr(context, "detect_device", lambda: ("mock-device", "android"))
    monkeypatch.setattr(
        context, "create_driver", lambda *args: SimpleNamespace(platform="android")
    )
    ctx = ScriptContext(
        recordings_dir=str(tmp_path / "recordings"), clear_logcat_on_start=False
    )
    ctx.declare_cases(declarations())
    session = ctx.session
    assert session.script_name == "reward_case"
    assert session._script_path == str(script)
    assert (session.run_dir / "script.py").read_text() == "# offline fixture"
    ctx._init_driver()  # mock-only; no device commands
    assert ctx.session is session
    assert session._device_info == {"serial": "mock-device", "platform": "android"}
    session.close()


def test_failed_assertion_can_have_complete_evidence(session):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    e = session.business_report["evidence"][0]
    session.add_assertion(
        "ui", "FAIL", "ad visible", "ad absent", evidence_refs=[e["evidence_id"]]
    )
    assert session.end_business_step()["status"] == "FAIL"
    assert session.business_report["summary"]["evidence_complete_rate"] == 1


@pytest.mark.parametrize(
    "mutation",
    ["dimensions", "na", "duplicate_case", "step_identity", "duplicate_attempt"],
)
def test_reload_rejects_corrupt_declaration_and_identity(session, mutation):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    session.end_business_step()
    path = session.run_dir / "business_report.json"
    data = json.loads(path.read_text())
    if mutation == "dimensions":
        data["cases"][0]["steps"][0]["required_dimensions"] = []
    elif mutation == "na":
        data["cases"][0]["steps"][0]["not_applicable"] = {"network": ""}
    elif mutation == "duplicate_case":
        data["cases"].append(data["cases"][0])
    elif mutation == "step_identity":
        data["business_steps"][0]["business_step_id"] = "undeclared"
    else:
        data["business_steps"].append(data["business_steps"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_business_report(session.run_dir)


@pytest.mark.parametrize("field", ["expected", "actual"])
def test_blank_assertion_not_pass(session, field):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    session._business.active["assertions"][0][field] = "  \n\t"
    assert session.end_business_step()["status"] == "NOT_COLLECTED"
    assert session.business_report["summary"]["evidence_complete_rate"] == 0


@pytest.mark.parametrize("mode", ["wrong_kind", "fake_png", "reused"])
def test_evidence_dimension_content_and_reuse_cannot_false_pass(session, mode):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    if mode == "wrong_kind":
        e = session.add_evidence("screenshot", data=tiny_png())
        for dim in ("ui", "callback_log", "network"):
            session.add_assertion(
                dim, "PASS", "expected", "actual", evidence_refs=[e["evidence_id"]]
            )
    else:
        collect_pass(session)
        screen = session._business.evidence[0]
        if mode == "fake_png":
            import hashlib

            (session.run_dir / screen["path"]).write_bytes(b"fake PNG")
            screen["sha256"] = hashlib.sha256(b"fake PNG").hexdigest()
        else:
            screen["reused"] = True
    assert session.end_business_step()["status"] == "NOT_COLLECTED"
    assert session.business_report["summary"]["evidence_complete_rate"] == 0


def test_malformed_report_raises_value_error(tmp_path):
    (tmp_path / "business_report.json").write_text('{"schema_version": "2"}')
    with pytest.raises(ValueError, match="malformed"):
        load_business_report(tmp_path)


def test_annotation_save_failure_does_not_break_legacy_tool(session, monkeypatch):
    import phone_pilot.script_api.context as context

    ctx = ScriptContext(
        _session=session,
        _driver=SimpleNamespace(screen=SimpleNamespace(screenshot=lambda: b"original")),
        auto_screenshot=True,
    )
    monkeypatch.setattr(context, "_annotate_screenshot", lambda *args: b"annotated")
    original_save = session.save_screenshot

    def fail_annotation(name, data):
        if "annotated" in name:
            raise OSError("disk full")
        return original_save(name, data)

    monkeypatch.setattr(session, "save_screenshot", fail_annotation)
    ctx._observe("find_text", element=object())
    assert len(session._steps) == 1
    assert "annotation save failed" in session._steps[0]["capture_error"]
    assert (
        session.screenshots_dir / session._steps[0]["screenshot"]
    ).read_bytes() == b"original"


def test_old_capture_timestamp_rejected_on_add_and_load(session):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    old = session.add_evidence(
        "screenshot", data=tiny_png(), capture_at="2000-01-01T00:00:00+00:00"
    )
    assert old["status"] == "NOT_COLLECTED"
    assert "time window" in old["capture_error"]
    collect_pass(session)
    session.end_business_step()
    path = session.run_dir / "business_report.json"
    data = json.loads(path.read_text())
    for evidence in data["evidence"]:
        if evidence["kind"] == "screenshot":
            evidence["capture_at"] = "2000-01-01T00:00:00+00:00"
            evidence["status"] = "PASS"
    path.write_text(json.dumps(data))
    report = load_business_report(session.run_dir)
    assert report["summary"]["status"] != "PASS"
    assert report["summary"]["evidence_complete_rate"] == 0


def test_duplicate_path_cannot_be_relabelled_as_new_attempt(session):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    original = session.business_report["evidence"][0]
    session.end_business_step("FAIL")
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    duplicate = session.add_evidence("screenshot", path=original["path"])
    assert duplicate["reused"] and duplicate["status"] == "NOT_COLLECTED"
    session.add_assertion(
        "ui", "PASS", "visible", "visible", evidence_refs=[duplicate["evidence_id"]]
    )
    assert session.end_business_step()["status"] == "NOT_COLLECTED"
    path = session.run_dir / "business_report.json"
    data = json.loads(path.read_text())
    data["evidence"][-1].update(reused=False, status="PASS", capture_error="")
    path.write_text(json.dumps(data))
    report = load_business_report(session.run_dir)
    assert report["summary"]["status"] != "PASS"
    assert report["summary"]["evidence_complete_rate"] == 0


@pytest.mark.parametrize(
    "kind,folder", [("network", "evidence"), ("screenshot", "screenshots")]
)
@pytest.mark.parametrize("inside", [False, True])
def test_data_evidence_symlink_rejected_before_any_write(
    tmp_path, kind, folder, inside
):
    from phone_pilot.core.business_report import BusinessReport

    root = tmp_path / "run"
    report = BusinessReport(root)
    report.declare_cases(declarations())
    report.begin_business_step("TC1", "load")
    destination = root / "other" if inside else tmp_path / "outside"
    destination.mkdir()
    (root / folder).symlink_to(destination, target_is_directory=True)
    before = (root / "business_report.json").read_bytes()
    with pytest.raises(ValueError, match="unsafe evidence"):
        report.add_evidence(kind, data=b"must never be written")
    assert list(destination.iterdir()) == []
    assert report.evidence == []
    assert (root / "business_report.json").read_bytes() == before


def test_data_evidence_parent_chain_symlink_rejected_before_write(tmp_path):
    from phone_pilot.core.business_report import BusinessReport

    root = tmp_path / "parent" / "run"
    report = BusinessReport(root)
    report.declare_cases(declarations())
    report.begin_business_step("TC1", "load")
    # Move only a verified pytest-owned directory to simulate parent replacement.
    parent = root.parent
    assert parent.resolve().is_relative_to(tmp_path.resolve())
    moved = tmp_path / "original-parent"
    parent.rename(moved)
    destination = tmp_path / "outside"
    (destination / "run").mkdir(parents=True)
    parent.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe evidence"):
        report.add_evidence("network", data="not outside")
    assert list((destination / "run").iterdir()) == []
    assert report.evidence == []


def test_data_evidence_creation_race_rejects_symlink(tmp_path, monkeypatch):
    import os
    from phone_pilot.core.business_report import BusinessReport

    report = BusinessReport(tmp_path / "run")
    report.declare_cases(declarations())
    report.begin_business_step("TC1", "load")
    outside = tmp_path / "outside"
    outside.mkdir()
    original = os.mkdir

    def raced_mkdir(path, *args, **kwargs):
        if path == "evidence" and "dir_fd" in kwargs:
            (report.run_dir / "evidence").symlink_to(outside, target_is_directory=True)
            raise FileExistsError("competing symlink")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", raced_mkdir)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {raced_mkdir})
    with pytest.raises(ValueError, match="unsafe evidence"):
        report.add_evidence("network", data="not outside")
    assert list(outside.iterdir()) == []
    assert report.evidence == []


def test_low_confidence_evidence_not_pass(session):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    e = session.add_evidence(
        "screenshot", data=b"s", association="time_window", confidence="low"
    )
    for dimension in ("ui", "callback_log", "network"):
        session.add_assertion(
            dimension, "PASS", "expected", "actual", evidence_refs=[e["evidence_id"]]
        )
    assert session.end_business_step()["status"] == "NOT_COLLECTED"


def test_snapshot_validation_cached_only_within_operation(session, monkeypatch):
    from phone_pilot.core.safe_io import SafeDirectory

    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    session.end_business_step()
    original = SafeDirectory.hash_file
    calls = []

    def counted(self, name):
        calls.append(name)
        return original(self, name)

    monkeypatch.setattr(SafeDirectory, "hash_file", counted)
    assert session.business_report["summary"]["status"] == "PASS"
    assert len(calls) == 2  # logs/network once each, PNG read/decode once
    target = session.run_dir / session._business.evidence[-1]["path"]
    target.write_bytes(b"tampered")
    assert session.business_report["summary"]["status"] != "PASS"
    assert len(calls) == 4


@pytest.mark.parametrize(
    "operation", ["business", "screenshot", "log", "recording", "meminfo"]
)
def test_writes_reject_shared_ancestor(session, operation):
    session.run_dir.parent.chmod(0o777)
    try:
        with pytest.raises(ValueError):
            if operation == "business":
                session.declare_cases(declarations())
            elif operation == "screenshot":
                session.save_screenshot("x", b"x")
            elif operation == "log":
                session.save_logcat("x", "x")
            elif operation == "recording":
                session.save_recording("x", b"x")
            else:
                session.save_meminfo("x", {})
    finally:
        session.run_dir.parent.chmod(0o700)


def test_business_save_refuses_symlink_destination(session, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("untouched")
    (session.run_dir / "business_report.json").symlink_to(outside)
    with pytest.raises(ValueError):
        session.declare_cases(declarations())
    assert outside.read_text() == "untouched"


def test_large_video_validates_without_read_bytes(session, monkeypatch):
    from phone_pilot.core.safe_io import SafeDirectory

    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    video = session.recordings_dir / "large.mp4"
    with video.open("wb") as stream:
        stream.truncate(33 * 1024 * 1024)
    evidence = session.add_evidence(
        "video", path=video, capture_at=dt.datetime.now().astimezone().isoformat()
    )
    monkeypatch.setattr(
        SafeDirectory,
        "read_bytes",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("whole load")),
    )
    assert session._business._valid_evidence(
        evidence["evidence_id"], session._business.active
    )


def test_log_capture_driver_never_receives_archive_path(session):
    from pathlib import Path

    calls = []

    def start(path, **kw):
        calls.append(Path(path))
        Path(path).write_text("specific event\n")
        return {"ok": True}

    driver = SimpleNamespace(
        start_log_capture=start, stop_log_capture=lambda p: {"ok": True, "lines": 1}
    )
    ctx = ScriptContext(_session=session, _driver=driver)
    result = ctx.start_logcat("../../escape", clear_before=False)
    assert not calls[0].is_relative_to(session.run_dir)
    assert Path(result["path"]).is_relative_to(session.logcat_dir)
    assert not Path(result["path"]).exists()
    saved = ctx.stop_logcat("../../escape")
    assert Path(saved["path"]).read_text() == "specific event\n"
    assert not calls[0].parent.exists()


def test_log_capture_archive_swap_does_not_write_outside(session, tmp_path):
    from pathlib import Path

    def start(path, **kw):
        Path(path).write_text("private logs")
        return {"ok": True}

    ctx = ScriptContext(
        _session=session,
        _driver=SimpleNamespace(
            start_log_capture=start, stop_log_capture=lambda p: {"ok": True}
        ),
    )
    ctx.start_logcat("x", clear_before=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    assert session.logcat_dir.resolve().is_relative_to(tmp_path.resolve())
    session.logcat_dir.rmdir()
    session.logcat_dir.symlink_to(outside, target_is_directory=True)
    result = ctx.stop_logcat("x")
    retained = Path(result["retained_capture_path"])
    try:
        assert not result["ok"]
        assert retained.read_text() == "private logs"
        assert not retained.is_relative_to(session.run_dir)
        assert list(outside.iterdir()) == []
    finally:
        ctx._logcat_processes["x"]["temporary"].cleanup()


def test_recording_driver_writes_private_temp_then_imports(session, monkeypatch):
    from pathlib import Path
    import phone_pilot.script_api.context as context

    monkeypatch.setattr(context.time, "sleep", lambda *a: None)
    paths = []

    def pull(remote, local):
        path = Path(local)
        paths.append(path)
        path.write_bytes(b"video")
        return {"ok": True}

    driver = SimpleNamespace(
        screen=SimpleNamespace(stop_screenrecord=lambda: {"ok": True}),
        pull_file=pull,
        remove_remote_file=lambda p: None,
    )
    ctx = ScriptContext(
        _session=session,
        _driver=driver,
        _recording=True,
        _record_name="../../x",
        _record_remote="remote",
    )
    result = ctx.stop_record()
    assert not paths[0].is_relative_to(session.run_dir)
    assert Path(result["local_path"]).is_relative_to(session.recordings_dir)
    assert Path(result["local_path"]).read_bytes() == b"video"
    assert not paths[0].parent.exists()


def test_png_decoder_error_is_invalid_evidence_and_closes_attempt(session, monkeypatch):
    import cv2
    from phone_pilot.core.html_report import render_html_report

    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)

    def bad_decode(*args):
        raise cv2.error("malformed huge PNG header")

    monkeypatch.setattr(cv2, "imdecode", bad_decode)
    result = session.end_business_step()
    assert result["status"] == "NOT_COLLECTED"
    assert session._business.active is None
    assert load_business_report(session.run_dir)["summary"]["status"] != "PASS"
    assert "NOT_COLLECTED" in render_html_report(session.run_dir)


@pytest.mark.parametrize("path", ["logcat/missing.txt", "missing-folder/log.txt"])
def test_missing_package_file_records_capture_error(session, path):
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    evidence = session.add_evidence("logcat", path=path, capture_error="capture failed")
    assert evidence["status"] == "NOT_COLLECTED"
    assert evidence["capture_error"] == "capture failed"
    assert evidence["sha256"] is None
    assert session.end_business_step()["status"] != "PASS"
    assert (
        load_business_report(session.run_dir)["evidence"][0]["capture_error"]
        == "capture failed"
    )


def test_archived_null_metadata_rejected_without_render_crash(session):
    from phone_pilot.core.html_report import render_html_report

    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    collect_pass(session)
    session.end_business_step()
    path = session.run_dir / "business_report.json"
    data = json.loads(path.read_text())
    data["evidence"][0]["metadata"] = None
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="metadata"):
        load_business_report(session.run_dir)
    assert "metadata" in render_html_report(session.run_dir)


def test_byte_video_preserves_player_and_offset(session):
    from phone_pilot.core.html_report import render_html_report
    from phone_pilot.core.report_evidence import evidence_projection

    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    video = session.add_evidence(
        "video", data=b"\x00\x00\x00\x18ftypmp42", metadata={"offset_s": 2.1}
    )
    assert video["path"].endswith(".mp4")
    assert evidence_projection(session.run_dir, video)["is_video"]
    session.end_business_step("FAIL")
    rendered = render_html_report(session.run_dir)
    assert "<video controls" in rendered
    assert ".mp4#t=2.1" in rendered


def test_unindexed_old_path_is_not_assigned_fresh_capture(session):
    import os

    old = session.save_screenshot("old_unindexed", tiny_png())
    two_hours_ago = dt.datetime.now().timestamp() - 7200
    os.utime(old, (two_hours_ago, two_hours_ago))
    session.declare_cases(declarations())
    session.begin_business_step("TC1", "load")
    evidence = session.add_evidence("screenshot", path=old)
    assert evidence["capture_at"] is None
    assert evidence["indexed_at"]
    assert evidence["status"] == "NOT_COLLECTED"
    assert "provenance" in evidence["capture_error"]
    for dimension in ("ui", "callback_log", "network"):
        session.add_assertion(
            dimension,
            "PASS",
            "expected",
            "actual",
            evidence_refs=[evidence["evidence_id"]],
        )
    assert session.end_business_step()["status"] != "PASS"
    assert load_business_report(session.run_dir)["summary"]["status"] != "PASS"


def test_console_export_failure_retains_private_capture_and_finalizes(
    tmp_path, monkeypatch
):
    from pathlib import Path
    from phone_pilot.core.safe_io import SafeDirectory

    session = RunSession.create("console", recordings_dir=str(tmp_path))
    session._console_log_fh.write("recoverable console\n")
    session.add_step(1, "offline")
    original = SafeDirectory.copy_file

    def fail_console(self, name, source, **kwargs):
        if name == "console.log":
            raise OSError("disk full")
        return original(self, name, source, **kwargs)

    monkeypatch.setattr(SafeDirectory, "copy_file", fail_console)
    meta = json.loads(session.finalize().read_text())
    retained = Path(meta["retained_console_capture_path"])
    try:
        assert "disk full" in meta["capture_errors"]["console"]
        assert retained.read_text() == "recoverable console\n"
        assert retained.stat().st_mode & 0o077 == 0
        assert not retained.is_relative_to(session.run_dir)
        assert (session.run_dir / "steps.json").exists()
        monkeypatch.setattr(SafeDirectory, "copy_file", original)
        session.close()
        assert not retained.exists()
        assert (session.run_dir / "console.log").read_text() == "recoverable console\n"
    finally:
        if retained.exists():
            assert retained.name.startswith("pp-console-")
            retained.unlink()


def test_console_flush_and_close_errors_do_not_abort_finalize(tmp_path):
    from pathlib import Path

    session = RunSession.create("console_io_failure", recordings_dir=str(tmp_path))
    actual = session._console_log_fh
    actual.write("recoverable partial console\n")
    actual.flush()

    class FailedStream:
        def flush(self):
            raise OSError("flush disk failure")

        def close(self):
            actual.close()
            raise OSError("close disk failure")

    session._console_log_fh = FailedStream()
    retained = session._console_capture_path
    try:
        meta = json.loads(session.finalize().read_text())
        assert "flush disk failure" in meta["capture_errors"]["console"]
        assert "close disk failure" in meta["capture_errors"]["console"]
        assert Path(meta["retained_console_capture_path"]) == retained
        assert retained.read_text() == "recoverable partial console\n"
        assert (session.run_dir / "steps.json").exists()
        session.close()
        assert session._console_capture_incomplete
        assert retained.exists()
        assert not (session.run_dir / "console.log").exists()
    finally:
        assert retained.name.startswith("pp-console-")
        retained.unlink()


def test_console_initialization_failure_reported_in_meta(tmp_path, monkeypatch):
    import phone_pilot.core.run_store as store

    monkeypatch.setattr(
        store.tempfile,
        "NamedTemporaryFile",
        lambda **kw: (_ for _ in ()).throw(OSError("initial disk failure")),
    )
    session = RunSession.create("console_init", recordings_dir=str(tmp_path))
    meta = json.loads(session.finalize().read_text())
    assert "initial disk failure" in meta["capture_errors"]["console"]
    assert meta["retained_console_capture_path"] is None


@pytest.mark.parametrize("phase", ["write", "flush"])
def test_console_transient_tee_error_remains_incomplete(tmp_path, phase):
    import phone_pilot.core.log as log

    session = RunSession.create("console_transient", recordings_dir=str(tmp_path))
    actual = session._console_log_fh

    class TransientStream:
        failed = False

        def write(self, text):
            if phase == "write" and not self.failed:
                self.failed = True
                raise OSError("transient tee write")
            return actual.write(text)

        def flush(self):
            if phase == "flush" and not self.failed:
                self.failed = True
                raise OSError("transient tee flush")
            return actual.flush()

        def close(self):
            return actual.close()

    wrapper = TransientStream()
    session._console_log_fh = wrapper
    log.set_console_log_file(wrapper)
    retained = session._console_capture_path
    try:
        log._log("might be lost")
        log._log("later succeeds")
        meta = json.loads(session.finalize().read_text())
        assert "transient tee" in meta["capture_errors"]["console"]
        assert session._console_capture_incomplete
        assert retained.exists()
        assert not (session.run_dir / "console.log").exists()
    finally:
        assert retained.name.startswith("pp-console-")
        retained.unlink()


@pytest.mark.parametrize("kind", ["timeline", "perfetto_analysis"])
def test_runner_memory_outputs_cannot_write_through_symlink(
    session, tmp_path, monkeypatch, kind
):
    from phone_pilot.script_api.runner import _finalize_run
    import phone_pilot.memory_analyze.perfetto_profiler as profiler
    import phone_pilot.script_api._helpers as helpers

    outside = tmp_path / "outside-memory"
    outside.mkdir()
    assert session.meminfo_dir.resolve().is_relative_to(tmp_path.resolve())
    session.meminfo_dir.rmdir()
    session.meminfo_dir.symlink_to(outside, target_is_directory=True)
    ctx = ScriptContext(_session=session, auto_report=False)
    monkeypatch.setattr(
        profiler, "stop_and_analyze", lambda *a: {"ok": True, "result": "offline"}
    )
    monkeypatch.setattr(
        helpers, "_get_current_focus", lambda *a: {"package": "offline"}
    )
    sampler = SimpleNamespace(stop=lambda: [{"pss": 1}]) if kind == "timeline" else None
    perfetto = (
        {"ok": True, "trace_file": "fixture"} if kind == "perfetto_analysis" else None
    )
    _finalize_run(ctx, 0, "", _meminfo_sampler=sampler, _perfetto_ctx=perfetto)
    assert list(outside.iterdir()) == []
    assert (session.run_dir / "run_meta.json").exists()


@pytest.mark.parametrize("platform", ["android", "harmony"])
@pytest.mark.parametrize("phase", ["write", "flush"])
def test_command_runner_transient_tee_error_preserves_incomplete_capture(
    tmp_path, monkeypatch, platform, phase
):
    import subprocess
    import phone_pilot.core.log as log
    import phone_pilot.android.adb.runner as adb
    import phone_pilot.harmony.hdc.runner as hdc

    module = adb if platform == "android" else hdc
    runner = adb.CommandRunner if platform == "android" else hdc.HdcCommandRunner
    session = RunSession.create("command_tee", recordings_dir=str(tmp_path))
    actual = session._console_log_fh

    class TransientStream:
        failed = False

        def write(self, text):
            if phase == "write" and not self.failed:
                self.failed = True
                raise OSError("transient command write")
            return actual.write(text)

        def flush(self):
            if phase == "flush" and not self.failed:
                self.failed = True
                raise OSError("transient command flush")
            return actual.flush()

        def close(self):
            return actual.close()

    wrapper = TransientStream()
    session._console_log_fh = wrapper
    log.set_console_log_file(wrapper)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    retained = session._console_capture_path
    try:
        runner.run(["offline-tool", "fixture"], silent=True, delay_s=0)
        runner.run(["offline-tool", "later"], silent=True, delay_s=0)
        meta = json.loads(session.finalize().read_text())
        assert "transient command" in meta["capture_errors"]["console"]
        assert session._console_capture_incomplete
        assert retained.exists()
        assert not (session.run_dir / "console.log").exists()
    finally:
        assert retained.name.startswith("pp-console-")
        retained.unlink()
