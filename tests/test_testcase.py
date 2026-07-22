"""Tests for TestCase orchestration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from phone_pilot.script_api.testcase import EnvError, SkipCase, TestCase, run_cases


def _ctx(session=None):
    return SimpleNamespace(
        driver=MagicMock(), platform="android", session=session, _step_counter=0
    )


class _Recorder(TestCase):
    """可编程用例：按列表脚本驱动各阶段行为。"""

    def __init__(
        self,
        setup_effects=None,
        act_effect=None,
        check_result=True,
        teardown_effect=None,
    ):
        self._setup_effects = list(setup_effects or [])
        self._act_effect = act_effect
        self._check_result = check_result
        self._teardown_effect = teardown_effect
        self.calls: list[str] = []

    def setup(self, ctx):
        self.calls.append("setup")
        if self._setup_effects:
            eff = self._setup_effects.pop(0)
            if eff is not None:
                raise eff

    def act(self, ctx):
        self.calls.append("act")
        if self._act_effect is not None:
            raise self._act_effect

    def check(self, ctx):
        self.calls.append("check")
        return self._check_result

    def teardown(self, ctx):
        self.calls.append("teardown")
        if self._teardown_effect is not None:
            raise self._teardown_effect


def test_setup_enverror_retried_then_pass():
    # B13: 前 2 次 EnvError 第 3 次成功；重试间 teardown 清场
    tc = _Recorder(setup_effects=[EnvError("x"), EnvError("x"), None])
    r = tc.run(_ctx())
    assert r["result"] == "pass"
    assert tc.calls.count("setup") == 3
    # 2 次重试前各 1 次清场 + 结尾 1 次 = 3
    assert tc.calls.count("teardown") == 3


def test_setup_enverror_exhausted():
    # B14
    tc = _Recorder(setup_effects=[EnvError("x")] * 3)
    r = tc.run(_ctx())
    assert r["result"] == "env_error" and r["retriable"] is True


def test_check_false_is_fail_no_retry():
    # B15
    tc = _Recorder(check_result=False)
    r = tc.run(_ctx())
    assert r["result"] == "fail" and r["retriable"] is False
    assert tc.calls.count("setup") == 1


def test_check_none_is_pass():
    # B28
    tc = _Recorder(check_result=None)
    assert tc.run(_ctx())["result"] == "pass"


def test_skipcase_in_act():
    # B16
    tc = _Recorder(act_effect=SkipCase("no_apid"))
    r = tc.run(_ctx())
    assert r["result"] == "skip" and "no_apid" in r["detail"]


def test_act_enverror_is_env_error():
    # B29
    tc = _Recorder(act_effect=EnvError("device gone"))
    assert tc.run(_ctx())["result"] == "env_error"


def test_setup_typeerror_is_error_not_retriable():
    # B30
    tc = _Recorder(setup_effects=[TypeError("bug")])
    r = tc.run(_ctx())
    assert r["result"] == "error" and r["retriable"] is False
    assert tc.calls.count("setup") == 1  # 不重试


def test_teardown_error_downgrades_pass():
    # B17 / ADR-8
    tc = _Recorder(teardown_effect=RuntimeError("mock reset failed"))
    r = tc.run(_ctx())
    assert r["result"] == "env_error" and r["original_result"] == "pass"


def test_teardown_runs_on_fail():
    # B18
    tc = _Recorder(check_result=False)
    tc.run(_ctx())
    assert tc.calls.count("teardown") == 1


def test_no_session_ok():
    # B19
    tc = _Recorder()
    assert tc.run(_ctx(session=None))["result"] == "pass"


def test_session_step_written():
    # ADR-9: 写 RunSession 且用 ctx._step_counter
    session = MagicMock()
    ctx = _ctx(session=session)
    _Recorder().run(ctx)
    assert session.add_step.called
    assert ctx._step_counter == 1  # 计数器递增


def test_run_cases_retry_env_error_last_wins():
    # B31: env_error 例重跑一次，末次为准，attempts 留痕
    tc = _Recorder(setup_effects=[EnvError("x")] * 3 + [None])  # 首轮耗尽,重跑成功
    tc.setup_retries = 3
    summary = run_cases(_ctx(), [tc], retry_env_errors=1)
    assert summary["pass"] == 1
    assert summary["results"][0]["attempts"] == ["env_error", "pass"]


def test_run_cases_max_total_s():
    # B32: 超预算剩余用例 skip
    import time as _t

    class _Slow(TestCase):
        def act(self, ctx):
            _t.sleep(0.2)

    summary = run_cases(_ctx(), [_Slow(), _Slow()], max_total_s=0.1)
    assert summary["skip"] >= 1
