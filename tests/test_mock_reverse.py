"""
Tests for phone_pilot.mock.reverse.ReverseManager.

Strategy: inject a fake runner that records all cmd invocations and returns
synthetic CompletedProcess objects — no real device required.
"""

from __future__ import annotations

import subprocess
from typing import Any


from phone_pilot.mock.reverse import ReverseManager


# ---------------------------------------------------------------------------
# Fake runner helpers
# ---------------------------------------------------------------------------


def _ok_result(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _err_result() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="error")


class FakeRunner:
    """Records every (cmd, kwargs) call; returns a configurable result."""

    def __init__(self, result_factory=None, raise_on_call: int | None = None) -> None:
        self.calls: list[list[str]] = []
        self._result_factory = result_factory or (lambda cmd, **kw: _ok_result())
        self._raise_on_call = raise_on_call  # 1-based index to raise on

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        if self._raise_on_call is not None and len(self.calls) == self._raise_on_call:
            raise RuntimeError("simulated runner failure")
        return self._result_factory(cmd, **kwargs)


# ---------------------------------------------------------------------------
# iOS: no port forwarding needed (simulator shares host loopback)
# ---------------------------------------------------------------------------


def test_ios_setup_is_noop() -> None:
    runner = FakeRunner()
    mgr = ReverseManager(SERIAL, platform="ios", runner=runner)
    res = mgr.setup(8080)
    assert res["ok"] is True
    assert res.get("noop") is True
    # iOS must NOT shell out to adb
    assert runner.calls == []


def test_ios_teardown_and_list_noop() -> None:
    runner = FakeRunner()
    mgr = ReverseManager(SERIAL, platform="ios", runner=runner)
    assert mgr.teardown(8080)["ok"] is True
    assert mgr.remove_all()["ok"] is True
    assert mgr.list() == []
    assert runner.calls == []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

SERIAL = "emulator-5554"
PORT = 8080


def test_setup_removes_before_reverse() -> None:
    """setup() must first --remove, then forward; order matters."""
    runner = FakeRunner()
    mgr = ReverseManager(SERIAL, runner=runner)
    result = mgr.setup(PORT)

    assert result["ok"] is True
    assert len(runner.calls) == 2

    remove_cmd = runner.calls[0]
    reverse_cmd = runner.calls[1]

    # First call: remove stale mapping (idempotency guard)
    assert "--remove" in remove_cmd
    assert f"tcp:{PORT}" in remove_cmd

    # Second call: establish the reverse tunnel
    assert "reverse" in reverse_cmd
    assert reverse_cmd.count(f"tcp:{PORT}") == 2
    # Ensure the reverse call comes *after* the remove call (already guaranteed
    # by index, but make it explicit for readability)
    assert runner.calls.index(remove_cmd) < runner.calls.index(reverse_cmd)


def test_teardown_removes_port() -> None:
    runner = FakeRunner()
    mgr = ReverseManager(SERIAL, runner=runner)
    result = mgr.teardown(PORT)

    assert result["ok"] is True
    assert len(runner.calls) == 1

    cmd = runner.calls[0]
    assert "--remove" in cmd
    assert f"tcp:{PORT}" in cmd


def test_remove_all() -> None:
    runner = FakeRunner()
    mgr = ReverseManager(SERIAL, runner=runner)
    result = mgr.remove_all()

    assert result["ok"] is True
    assert len(runner.calls) == 1

    cmd = runner.calls[0]
    assert "--remove-all" in cmd


def test_list_parsing() -> None:
    raw_output = "tcp:8080 tcp:8080\ntcp:9090 tcp:9090\n\n"
    runner = FakeRunner(result_factory=lambda cmd, **kw: _ok_result(stdout=raw_output))
    mgr = ReverseManager(SERIAL, runner=runner)
    lines = mgr.list()

    assert lines == ["tcp:8080 tcp:8080", "tcp:9090 tcp:9090"]


def test_runner_failure_returns_error() -> None:
    """When the critical reverse command raises, setup() returns ok:False."""
    # raise on the 2nd call (the actual reverse command)
    runner = FakeRunner(raise_on_call=2)
    mgr = ReverseManager(SERIAL, runner=runner)
    result = mgr.setup(PORT)

    assert result["ok"] is False
    assert "error" in result


def test_commands_use_serial_prefix() -> None:
    """Every emitted command must contain '-s' and the serial string."""
    runner = FakeRunner()
    mgr = ReverseManager(SERIAL, runner=runner)

    mgr.setup(PORT)
    mgr.teardown(PORT)
    mgr.remove_all()
    mgr.list()

    for cmd in runner.calls:
        assert "-s" in cmd, f"Missing '-s' in {cmd}"
        assert SERIAL in cmd, f"Missing serial '{SERIAL}' in {cmd}"
