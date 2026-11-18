"""Real local process/pipeline regressions; never invoke device executables."""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from phone_pilot.android.driver import AndroidDriver
from phone_pilot.harmony.driver import HarmonyDriver
from phone_pilot.ios.device import log_capture as ios_capture


@pytest.fixture(params=["android", "harmony", "ios"])
def capture(request, monkeypatch, tmp_path):
    writer = tmp_path / "writer.py"
    writer.write_text(
        "import os, pathlib, signal, sys, time\n"
        "if os.environ.get('CAPTURE_IGNORE_TERM') == '1':\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(os.environ['CAPTURE_PID_FILE']).write_text(str(os.getpid()))\n"
        "if os.environ.get('CAPTURE_WAIT_TRIGGER') == '1':\n"
        "    while not pathlib.Path(os.environ['CAPTURE_PID_FILE'] + '.go').exists():\n"
        "        time.sleep(0.01)\n"
        "if os.environ.get('CAPTURE_SHORT_LINE') == '1':\n"
        "    print('keep callback=LOAD_SUCCESS type=native', flush=True)\n"
        "    pathlib.Path(os.environ['CAPTURE_PID_FILE'] + '.sent').touch()\n"
        "    time.sleep(60)\n"
        "    sys.exit(0)\n"
        "while True:\n"
        "    print('keep ' + 'x' * 8192, flush=True)\n"
        "    time.sleep(0.01)\n"
    )
    pid_file = tmp_path / "writer.pid"
    monkeypatch.setenv("CAPTURE_PID_FILE", str(pid_file))
    cmd = [sys.executable, str(writer)]
    platform = request.param
    if platform == "ios":
        monkeypatch.setattr(ios_capture, "_build_cmd", lambda *a, **k: cmd)
        captures = ios_capture._log_captures

        def start(path, **kw):
            return ios_capture.start_log_capture("offline", path, **kw)

        def stop(path):
            return ios_capture.stop_log_capture("offline", path)
    else:
        if platform == "android":
            from phone_pilot.android.adb import utils

            monkeypatch.setattr(utils, "adb_prefix", lambda serial: cmd)
            driver = object.__new__(AndroidDriver)
        else:
            from phone_pilot.harmony.hdc import utils

            monkeypatch.setattr(utils, "hdc_prefix", lambda serial: cmd)
            driver = object.__new__(HarmonyDriver)
        driver._device_serial = "offline"
        driver._log_captures = {}
        captures = driver._log_captures
        start, stop = driver.start_log_capture, driver.stop_log_capture
    retained = ios_capture._retained_log_captures if platform == "ios" else {}
    if platform != "ios":
        driver._retained_log_captures = retained
    api = SimpleNamespace(start=start, stop=stop, captures=captures, retained=retained, pid_file=pid_file)
    yield api
    # Only clean groups created by this fixture, including assertion-failure paths.
    for path, info in list(captures.items()):
        proc = info["proc"]
        if os.name == "posix" and info.get("pgid") == proc.pid:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                assert not group_members(proc.pid)
        proc.wait(timeout=3)
        info["fh"].close()
        captures.pop(path)
    retained.clear()


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "local process did not reach expected state"


def group_members(pgid):
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,stat="], capture_output=True, text=True, check=True
    )
    return [
        (int(pid), state)
        for pid, group, state in (line.split() for line in result.stdout.splitlines())
        if int(group) == pgid
    ]


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("grep"), reason="POSIX pipeline"
)
@pytest.mark.parametrize(
    "mode", ["terminate", "ignore_term", "producer_exited", "direct"]
)
def test_capture_stops_entire_private_group(capture, monkeypatch, tmp_path, mode):
    if mode == "ignore_term":
        monkeypatch.setenv("CAPTURE_IGNORE_TERM", "1")
    output = str(tmp_path / "capture.log")
    filters = (
        {}
        if mode == "direct"
        else {"tags": "keep,other", "exclude_tags": "drop", "process": "keep"}
    )
    # An unrelated session must survive both TERM and KILL escalation.
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        result = capture.start(output, **filters)
        assert result["ok"], result
        info = capture.captures[output]
        proc = info["proc"]
        assert os.getpgid(proc.pid) == proc.pid != os.getpgrp()
        wait_until(
            lambda: capture.pid_file.exists() and Path(output).stat().st_size > 0
        )
        writer_pid = int(capture.pid_file.read_text())
        members = group_members(proc.pid)
        assert writer_pid in [pid for pid, _ in members]
        if mode != "direct":
            assert len(members) >= 3  # A real shell, producer and grep children.
        if mode == "producer_exited":
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        stopped = capture.stop(output)
        assert stopped["ok"], stopped
        assert proc.returncode is not None
        assert info["fh"].closed
        assert output not in capture.captures
        assert not group_members(proc.pid)
        assert unrelated.poll() is None
        before = Path(output).read_bytes()
        stat = Path(output).stat()
        time.sleep(0.15)
        assert Path(output).read_bytes() == before
        assert Path(output).stat().st_mtime_ns == stat.st_mtime_ns
        assert stopped["lines"] > 0
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("grep"), reason="POSIX pipeline"
)
def test_filtered_capture_preserves_one_short_callback(capture, monkeypatch, tmp_path):
    monkeypatch.setenv("CAPTURE_SHORT_LINE", "1")
    output = str(tmp_path / "short-line.log")
    result = capture.start(
        output, tags="keep,other", exclude_tags="drop", process="keep"
    )
    assert result["ok"], result
    wait_until(lambda: Path(str(capture.pid_file) + ".sent").exists())
    # A single short line must reach the output while the producer is still alive.
    # No EOF or buffer-filling output is available to flush any of the grep stages.
    wait_until(lambda: Path(output).read_bytes() != b"", timeout=2)
    stopped = capture.stop(output)
    assert stopped["ok"], stopped
    assert stopped["lines"] == 1
    assert Path(output).read_text() == "keep callback=LOAD_SUCCESS type=native\n"
    before = Path(output).read_bytes()
    time.sleep(0.1)
    assert Path(output).read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group")
def test_stop_drains_queued_line_through_stopped_filters(
    capture, monkeypatch, tmp_path
):
    monkeypatch.setenv("CAPTURE_SHORT_LINE", "1")
    monkeypatch.setenv("CAPTURE_WAIT_TRIGGER", "1")
    output = str(tmp_path / "queued.log")
    assert capture.start(
        output, tags="keep,other", exclude_tags="drop", process="keep"
    )["ok"]
    info = capture.captures[output]
    wait_until(lambda: capture.pid_file.exists())
    assert len(info["children"]) == 4
    for child in info["children"][1:]:
        os.kill(child.pid, signal.SIGSTOP)
    wait_until(
        lambda: all(
            state.startswith("T")
            for pid, state in group_members(info["pgid"])
            if pid != info["proc"].pid
        )
    )
    Path(str(capture.pid_file) + ".go").touch()
    wait_until(lambda: Path(str(capture.pid_file) + ".sent").exists())
    assert Path(output).read_bytes() == b""  # The short callback is queued in the pipe.
    result = capture.stop(output)
    assert result["ok"], result
    assert Path(output).read_text() == "keep callback=LOAD_SUCCESS type=native\n"
    assert result["lines"] == 1
    assert all(child.returncode is not None for child in info["children"])
    assert not group_members(info["pgid"])


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group")
@pytest.mark.parametrize("failure", ["permission", "unsafe_group"])
def test_stop_failure_preserves_capture(capture, monkeypatch, tmp_path, failure):
    output = str(tmp_path / "retained.log")
    assert capture.start(output)["ok"]
    wait_until(lambda: capture.pid_file.exists())
    info = capture.captures[output]
    original_pgid = info["pgid"]
    with monkeypatch.context() as patch:
        if failure == "permission":

            def deny_signal(*args):
                raise PermissionError("denied")

            patch.setattr(os, "killpg", deny_signal)
        else:
            info["pgid"] = os.getpgrp()
        result = capture.stop(output)
        assert not result["ok"]
        assert capture.captures[output] is info
        assert not info["fh"].closed
        assert info["proc"].poll() is None
    info["pgid"] = original_pgid
    assert capture.stop(output)["ok"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group")
@pytest.mark.parametrize("probe_permission", [False, True])
@pytest.mark.parametrize("snapshot_kind", ["zombies", "mixed", "malformed"])
def test_group_snapshot_ignores_only_zombies(
    capture, monkeypatch, tmp_path, probe_permission, snapshot_kind
):
    from unittest.mock import Mock

    output = str(tmp_path / "zombie-only.log")
    pgid = os.getpgrp() + 1000000
    proc = Mock(pid=pgid)
    proc.wait.return_value = 0
    fh = open(output, "w")
    info = {"proc": proc, "pgid": pgid, "fh": fh}
    capture.captures[output] = info
    rows = f"{pgid} Z\n{pgid} Z+\n{pgid + 1} S\n"
    if snapshot_kind == "mixed":
        rows += f"{pgid} S\n"
    elif snapshot_kind == "malformed":
        rows += "invalid row extra\n"
    signals = []

    def signal_group(group, sig):
        assert group == pgid
        signals.append(sig)
        if sig != 0 or probe_permission:
            raise PermissionError("synthetic group probe")

    def snapshot_run(args, **kwargs):
        assert args == ["ps", "-axo", "pgid=,stat="]
        assert kwargs["check"] and kwargs["timeout"] == 1
        return SimpleNamespace(stdout=rows)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "killpg", signal_group)
            patch.setattr(subprocess, "run", snapshot_run)
            result = capture.stop(output)
        if snapshot_kind == "zombies":
            assert result["ok"], result
            assert info["group_stopped"]
            assert output not in capture.captures
            assert fh.closed
            proc.wait.assert_called_once_with(timeout=3)
            # No escalation or repeat signal after proving only zombies remain.
            assert signals == [signal.SIGCONT, 0]
        else:
            assert not result["ok"]
            assert capture.captures[output] is info
            assert not fh.closed
            assert not info.get("group_stopped")
            proc.wait.assert_not_called()
    finally:
        capture.captures.pop(output, None)
        fh.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group")
def test_unknown_group_retry_does_not_signal_after_reaping(capture, monkeypatch, tmp_path):
    from unittest.mock import Mock
    from phone_pilot.core import capture_pipeline

    output = str(tmp_path / "unknown-group.log")
    pgid = os.getpgrp() + 1000000
    proc = Mock(pid=pgid, returncode=0)
    proc.wait.return_value = 0
    fh = open(output, "w")
    info = {"proc": proc, "children": [proc], "pgid": pgid, "fh": fh}
    capture.captures[output] = info
    try:
        with monkeypatch.context() as patch:
            signal_mock = Mock()
            patch.setattr(capture_pipeline, "_signal_group", signal_mock)
            patch.setattr(capture_pipeline, "_group_exists", lambda info: True)
            result = capture.stop(output)
            assert not result["ok"]
            assert info["ownership_ended"] and info["drain_failed"]
            assert capture.captures.get(output) is info
            signal_mock.assert_called_once_with(info, signal.SIGCONT)
            signal_mock.reset_mock()
            retry = capture.stop(output)
            assert not retry["ok"]
            signal_mock.assert_not_called()
            assert not fh.closed
    finally:
        capture.captures.pop(output, None)
        fh.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX owned pipeline")
@pytest.mark.parametrize("failure", ["bad_filter", "timeout"])
def test_terminal_failure_releases_active_capture_keeps_source(
    capture, monkeypatch, tmp_path, failure
):
    from phone_pilot.core import capture_pipeline

    output = str(tmp_path / "terminal.log")
    original_start = capture_pipeline.start_pipeline
    broken_filter = tmp_path / "broken_filter.py"
    broken_filter.write_text(
        "import signal,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True: time.sleep(1)\n"
    )

    def start_broken(command, filters, fh):
        if failure == "bad_filter":
            filters = [["grep", "--line-buffered", "-E", "["]]
        else:
            filters = [[sys.executable, str(broken_filter)]]
        return original_start(command, filters, fh)

    with monkeypatch.context() as patch:
        patch.setattr(capture_pipeline, "start_pipeline", start_broken)
        assert capture.start(output)["ok"]
    wait_until(lambda: capture.pid_file.exists())
    info = capture.captures[output]
    # Existing raw evidence must not be deleted or filtered on terminal failure.
    info["fh"].write("retained raw evidence\n")
    info["fh"].flush()
    result = capture.stop(output)
    assert not result["ok"]
    assert result["terminal"] is True and result["stopped"] is True
    assert result["path"] == output
    assert output not in capture.captures
    assert capture.retained[output] is info
    assert info["fh"].closed
    assert not group_members(info["pgid"])
    assert all(child.returncode is not None for child in info["children"])
    assert "retained raw evidence" in Path(output).read_text()
    assert not capture.stop(output)["ok"]  # Never retry signalling terminal PIDs.
    original_raw = Path(output).read_bytes()
    restart_same = capture.start(output)
    assert not restart_same["ok"]
    assert restart_same["error"] == "retained_capture_use_new_path"
    assert Path(output).read_bytes() == original_raw
    replacement = str(tmp_path / "replacement.log")
    assert capture.start(replacement)["ok"]
    assert capture.stop(replacement)["ok"]
    assert Path(output).exists()


def test_non_posix_fails_before_file_or_process(capture, monkeypatch, tmp_path):
    output = str(tmp_path / "not-created.log")
    monkeypatch.setattr(os, "name", "nt")
    result = capture.start(output, tags="keep")
    assert not result["ok"]
    assert "POSIX" in result["error"]
    # pathlib selects WindowsPath while os.name is mocked; use the concrete path type.
    assert not tmp_path.joinpath("not-created.log").exists()
    assert not capture.pid_file.exists()
    assert output not in capture.captures
