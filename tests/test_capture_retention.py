"""Host-only import failure must not delete raw capture or restart the writer."""

from pathlib import Path
from types import SimpleNamespace
import tempfile

from phone_pilot.core.safe_io import SafeDirectory, UnsafePath
from phone_pilot.script_api import ScriptContext


def test_failed_import_retains_capture_for_retry(tmp_path, monkeypatch):
    temporary = tempfile.TemporaryDirectory(prefix="pp-test-log-")
    source = Path(temporary.name).resolve() / "capture.txt"
    source.write_bytes(b"captured logs\n")
    output = tmp_path / "logs"
    output.mkdir()
    calls = []
    ctx = ScriptContext()
    ctx._driver = SimpleNamespace(
        stop_log_capture=lambda path: calls.append(path) or {"ok": True, "lines": 1}
    )
    ctx._logcat_processes = {
        "sample": {
            "path": output / "log.txt",
            "driver_output_path": str(source),
            "temporary": temporary,
        }
    }
    real_copy = SafeDirectory.copy_file
    monkeypatch.setattr(
        SafeDirectory,
        "copy_file",
        lambda *a, **k: (_ for _ in ()).throw(UnsafePath("injected source mutation")),
    )
    result = ctx.stop_logcat("sample")
    assert not result["ok"]
    assert result["retained_capture_path"] == str(source)
    assert source.read_bytes() == b"captured logs\n"
    assert "sample" in ctx._logcat_processes
    monkeypatch.setattr(SafeDirectory, "copy_file", real_copy)
    result = ctx.stop_logcat("sample")
    assert result["ok"] and len(calls) == 1
    assert (output / "log.txt").read_bytes() == b"captured logs\n"
    assert not source.exists()
    assert not ctx._logcat_processes


def test_failed_capture_survives_context_destruction(tmp_path, monkeypatch):
    import gc

    paths = []

    def start(path, **kwargs):
        paths.append(Path(path))
        Path(path).write_bytes(b"preserved raw capture")
        return {"ok": True}

    ctx = ScriptContext(
        out_dir=str(tmp_path),
        _driver=SimpleNamespace(
            start_log_capture=start, stop_log_capture=lambda path: {"ok": True}
        ),
    )
    ctx.start_logcat("x", clear_before=False)
    temporary = ctx._logcat_processes["x"]["temporary"]
    monkeypatch.setattr(
        SafeDirectory,
        "copy_file",
        lambda *a, **k: (_ for _ in ()).throw(UnsafePath("injected")),
    )
    assert not ctx.stop_logcat("x")["ok"]
    del ctx
    gc.collect()
    assert paths[0].read_bytes() == b"preserved raw capture"
    temporary.cleanup()


def test_failed_recording_import_retains_local_and_remote(tmp_path, monkeypatch):
    import shutil
    import phone_pilot.script_api.context as context
    from phone_pilot.core.run_store import RunSession

    monkeypatch.setattr(context.time, "sleep", lambda *args: None)
    session = RunSession(tmp_path / "run")
    session.recordings_dir.mkdir(parents=True)
    removed = []

    def pull(remote, local):
        Path(local).write_bytes(b"complete recording")
        return {"ok": True}

    ctx = ScriptContext(
        _session=session,
        _driver=SimpleNamespace(
            screen=SimpleNamespace(stop_screenrecord=lambda: {"ok": True}),
            pull_file=pull,
            remove_remote_file=lambda path: removed.append(path),
        ),
    )
    ctx._recording, ctx._record_name, ctx._record_remote = (
        True,
        "video",
        "/remote/record.mp4",
    )
    monkeypatch.setattr(
        SafeDirectory,
        "copy_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(UnsafePath("no space")),
    )
    result = ctx.stop_record()
    assert not result["ok"] and result["retained_remote_path"] == "/remote/record.mp4"
    local = Path(result["retained_capture_path"])
    assert local.read_bytes() == b"complete recording" and not removed
    assert list(session.recordings_dir.iterdir()) == []
    assert local.parent.parent == Path(tempfile.gettempdir()).resolve()
    shutil.rmtree(local.parent)


def test_recording_without_remote_or_failed_pull_not_success(tmp_path, monkeypatch):
    import phone_pilot.script_api.context as context
    from phone_pilot.core.run_store import RunSession

    monkeypatch.setattr(context.time, "sleep", lambda *args: None)
    session = RunSession(tmp_path / "run")
    session.run_dir.mkdir()
    paths, removed = [], []

    def pull(remote, local):
        paths.append(Path(local))
        return {"ok": False, "error": "failed pull"}

    ctx = ScriptContext(
        _session=session,
        _driver=SimpleNamespace(
            screen=SimpleNamespace(stop_screenrecord=lambda: {"ok": True}),
            pull_file=pull,
            remove_remote_file=lambda remote: removed.append(remote),
        ),
    )
    ctx._recording, ctx._record_remote = True, ""
    assert not ctx.stop_record()["ok"]
    ctx._recording, ctx._record_remote = True, "/remote/video"
    result = ctx.stop_record()
    assert not result["ok"] and "retained_capture_path" not in result
    assert result["retained_remote_path"] == "/remote/video"
    assert not paths[0].parent.exists() and not removed


def test_simulator_host_recording_is_private_and_archived(tmp_path):
    from phone_pilot.core.run_store import RunSession

    session = RunSession(tmp_path / "run")
    session.run_dir.mkdir()
    paths = []

    def start(path):
        paths.append(Path(path))
        Path(path).write_bytes(b"host mp4")
        return {"ok": True, "path": path, "engine": "simctl"}

    driver = SimpleNamespace(
        platform="ios",
        screen=SimpleNamespace(
            start_screenrecord=start,
            stop_screenrecord=lambda: {"ok": True, "path": str(paths[0])},
        ),
        pull_file=lambda *args: (_ for _ in ()).throw(AssertionError("no device pull")),
        remove_remote_file=lambda *args: (_ for _ in ()).throw(
            AssertionError("no remote cleanup")
        ),
    )
    ctx = ScriptContext(platform="ios", _session=session, _driver=driver)
    assert ctx.start_record("sim")["ok"]
    assert not paths[0].is_relative_to(session.run_dir)
    result = ctx.stop_record()
    assert result["ok"] and Path(result["local_path"]).read_bytes() == b"host mp4"
    assert not paths[0].exists()


def test_entry_capture_failure_closes_attempt(tmp_path, monkeypatch):
    from phone_pilot.core.run_store import RunSession
    from phone_pilot.script_api.context import BusinessStepContext

    session = RunSession(tmp_path / "run")
    session.declare_cases([{"case_id": "c", "steps": [{"business_step_id": "s"}]}])
    ctx = ScriptContext(_session=session)
    original = RuntimeError("capture write failed")
    monkeypatch.setattr(
        BusinessStepContext, "capture", lambda *args: (_ for _ in ()).throw(original)
    )
    import pytest

    with pytest.raises(RuntimeError) as caught:
        with ctx.business_step("c", "s", capture_before=True):
            pass
    assert caught.value is original and session._business.active is None
    assert session._business.business_steps[0]["status"] == "FAIL"
    session.begin_business_step("c", "s")
    session.end_business_step("FAIL")


def test_terminal_log_error_preserves_source_but_releases_active(tmp_path):
    source_dir = tempfile.TemporaryDirectory(prefix="pp-terminal-test-", delete=False)
    source = Path(source_dir.name).resolve() / "capture.txt"
    source.write_bytes(b"retained terminal raw")
    ctx = ScriptContext(
        _driver=SimpleNamespace(
            stop_log_capture=lambda path: {
                "ok": False,
                "stopped": True,
                "terminal": True,
                "error": "filter failed",
            }
        )
    )
    ctx._logcat_processes = {
        "x": {
            "path": tmp_path / "x.txt",
            "driver_output_path": str(source),
            "temporary": source_dir,
        }
    }
    result = ctx.stop_logcat("x")
    assert not result["ok"] and result["retained_capture_path"] == str(source)
    assert (
        "x" not in ctx._logcat_processes
        and ctx._retained_logcat_captures["x"]["temporary"] is source_dir
    )
    assert source.read_bytes() == b"retained terminal raw"
    source_dir.cleanup()


def test_post_capture_failure_closes_attempt(tmp_path, monkeypatch):
    from phone_pilot.core.run_store import RunSession
    from phone_pilot.script_api.context import BusinessStepContext
    import pytest

    session = RunSession(tmp_path / "run")
    session.declare_cases([{"case_id": "c", "steps": [{"business_step_id": "s"}]}])
    ctx = ScriptContext(_session=session)
    original = OSError("post capture disk full")
    monkeypatch.setattr(
        BusinessStepContext, "capture", lambda *args: (_ for _ in ()).throw(original)
    )
    with pytest.raises(OSError) as caught:
        with ctx.business_step("c", "s", capture_after=True):
            pass
    assert caught.value is original and session._business.active is None
    attempt = session._business.business_steps[0]
    assert attempt["status"] == "FAIL" and attempt["finished_at"]
    session.begin_business_step("c", "s")
    session.end_business_step("FAIL")
