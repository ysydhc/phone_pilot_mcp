"""TestCase/EnvError/SkipCase/run_cases — 用例编排框架 / test-case orchestration.

设计文档 / Design doc: docs/design/broadcast-testcase.md（3.3 状态流转、4.4 API 契约、
ADR-6/8/9）。

- ADR-6: 环境级失败以 ``EnvError`` 异常为通用信号（script_api "返回 dict 不抛异常"
  惯例的显式例外，先例 ``RetryExhausted``）。
  Env-level failure signals via the ``EnvError`` exception (an explicit exception to
  the "return dict, never raise" convention; precedent: ``RetryExhausted``).
- ADR-8: teardown 抛异常 → 结果降级 ``env_error``，``original_result`` 留痕；
  配套契约：setup 必须自足重建环境。
  A raising teardown downgrades the result to ``env_error`` with ``original_result``
  kept; setup must be self-sufficient in rebuilding the environment.
- ADR-9: 结果写入 RunSession（``add_step``），step_num 复用 ``ctx._step_counter``；
  无 session 静默跳过。
  Results are written to RunSession reusing ``ctx._step_counter``; silently skipped
  when no session is attached.

本模块刻意不依赖 broadcast 模块，纯 Python、平台无关。
This module deliberately does not import the broadcast module; pure Python,
platform-agnostic.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional, Sequence

__all__ = ["EnvError", "SkipCase", "TestCase", "run_cases"]

#: run() 的五种结果 / the five possible results of run()
_RESULTS = ("pass", "fail", "env_error", "error", "skip")


class EnvError(Exception):
    """环境级失败（可重试）/ Environment-level failure (retriable).

    reason 示例 / example reasons: ``ack_timeout`` / ``app_dead`` /
    ``mock_unreachable``。setup 中抛出会触发清场重试（ADR-6）。
    Raised from setup it triggers teardown-then-retry (ADR-6).
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


class SkipCase(Exception):
    """前置条件缺失，跳过用例 / Missing precondition, skip the case.

    reason 示例 / example reason: ``no_apid``。
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


class TestCase:
    """用例基类：setup → act → check → teardown / test-case base class.

    子类覆写四个阶段方法；``run(ctx)`` 按设计文档 3.3 状态机执行并返回结果 dict。
    Subclasses override the four phase methods; ``run(ctx)`` executes the state
    machine of design doc section 3.3 and returns a result dict.

    Attributes / 属性:
        name: 用例名，缺省用类名 / case name, defaults to the class name
        setup_retries: setup 总尝试次数（EnvError 触发清场重试）
            / total setup attempts (EnvError triggers teardown-then-retry)
    """

    name: str = ""
    setup_retries: int = 3

    # ---- 生命周期钩子 / lifecycle hooks -------------------------------------

    def setup(self, ctx: Any) -> None:
        """环境准备；EnvError → 清场后自动重试 / env prep; EnvError → retry."""

    def act(self, ctx: Any) -> None:
        """真机操作 / device actions under test."""

    def check(self, ctx: Any) -> Any:
        """判定：None/True/{"ok": True} → pass / verdict; None means pass."""
        return None

    def teardown(self, ctx: Any) -> None:
        """复位；必执行；抛异常 → 降级 env_error（ADR-8）
        / cleanup; always runs; raising downgrades to env_error (ADR-8)."""

    # ---- 执行 / execution ---------------------------------------------------

    @property
    def _display_name(self) -> str:
        return self.name or type(self).__name__

    def _safe_teardown(self, ctx: Any) -> Optional[BaseException]:
        """执行 teardown 并吞掉异常，返回被吞的异常（若有）。
        Run teardown swallowing exceptions; return the swallowed one, if any.
        """
        try:
            self.teardown(ctx)
            return None
        except Exception as exc:  # noqa: BLE001 - 必须兜住任意用户异常
            return exc

    def run(self, ctx: Any) -> dict:
        """执行完整生命周期，严格按设计 3.3 状态机 / run the full lifecycle.

        Returns / 返回:
            {"name", "result": "pass|fail|env_error|error|skip",
             "retriable": bool, "detail": str, "elapsed_s": float,
             "original_result": str | None}  # teardown 降级时留痕（ADR-8）
        """
        start = time.monotonic()
        result, retriable, detail = "pass", False, ""

        setup_ok = False
        for attempt in range(self.setup_retries):
            try:
                self.setup(ctx)
                setup_ok = True
                break
            except EnvError as exc:
                detail = repr(exc)
                if attempt + 1 < self.setup_retries:
                    # 重试前 teardown 清半成品状态（吞异常）
                    # teardown between retries to clear half-built state
                    self._safe_teardown(ctx)
                    continue
                result, retriable = "env_error", True
            except SkipCase as exc:
                result, retriable, detail = "skip", False, repr(exc)
                break
            except Exception as exc:  # noqa: BLE001 - 真代码 bug，单列 error
                result, retriable, detail = "error", False, repr(exc)
                break

        if setup_ok:
            result, retriable, detail = self._run_act_and_check(ctx)

        original_result: Optional[str] = None
        teardown_exc = self._safe_teardown(ctx)
        if teardown_exc is not None:
            # ADR-8: teardown 失败 = 环境未复位，结果降级 env_error 留痕
            original_result = result
            result, retriable = "env_error", True
            detail = f"teardown failed: {teardown_exc!r}; original detail: {detail}"

        outcome = {
            "name": self._display_name,
            "result": result,
            "retriable": retriable,
            "detail": detail,
            "elapsed_s": round(time.monotonic() - start, 3),
            "original_result": original_result,
        }
        _write_session_step(ctx, outcome)
        return outcome

    def _run_act_and_check(self, ctx: Any) -> tuple[str, bool, str]:
        """act + check 阶段判定 / act + check phase verdict."""
        try:
            self.act(ctx)
        except EnvError as exc:
            return "env_error", True, repr(exc)
        except SkipCase as exc:
            return "skip", False, repr(exc)
        except Exception as exc:  # noqa: BLE001 - act 一般异常视作被测缺陷
            return "fail", False, repr(exc)

        try:
            verdict = self.check(ctx)
        except SkipCase as exc:
            return "skip", False, repr(exc)
        except Exception as exc:  # noqa: BLE001 - check 异常即 fail 信号
            return "fail", False, repr(exc)

        if verdict is None or verdict is True:
            return "pass", False, ""
        if isinstance(verdict, dict):
            # dict 全文进 detail（证据留痕）/ full dict kept as evidence
            if verdict.get("ok"):
                return "pass", False, repr(verdict)
            return "fail", False, repr(verdict)
        if verdict is False:
            return "fail", False, "check returned False"
        # 其他真值按 pass、假值按 fail / other truthy → pass, falsy → fail
        if verdict:
            return "pass", False, repr(verdict)
        return "fail", False, repr(verdict)


def _write_session_step(ctx: Any, outcome: dict) -> None:
    """结果写入 RunSession（ADR-9）；session 缺失或写入失败均不影响返回（B19）。
    Write the outcome to RunSession (ADR-9); missing session or a raising
    ``add_step`` never affects the returned result (B19).
    """
    session = getattr(ctx, "session", None)
    if session is None:
        return
    try:
        ctx._step_counter += 1  # 复用主计数器防截图撞号（ADR-9）
        session.add_step(
            ctx._step_counter,
            f"testcase:{outcome['name']}",
            status="ok" if outcome["result"] == "pass" else outcome["result"],
            detail=outcome["detail"],
            extra={
                "result": outcome["result"],
                "retriable": outcome["retriable"],
                "original_result": outcome["original_result"],
            },
        )
    except Exception:  # noqa: BLE001 - 记录失败不得影响用例结果
        pass


def _budget_skip(case: TestCase) -> dict:
    """预算耗尽时的占位结果 / placeholder result when budget is exceeded."""
    return {
        "name": case.name or type(case).__name__,
        "result": "skip",
        "retriable": False,
        "detail": "budget_exceeded",
        "elapsed_s": 0.0,
        "original_result": None,
        "attempts": [],
    }


def run_cases(
    ctx: Any,
    cases: Sequence[TestCase],
    *,
    retry_env_errors: int = 1,
    max_total_s: Optional[float] = None,
) -> dict:
    """跑批入口：逐例 run；env_error 例整例重跑 / batch runner.

    - env_error 的用例整例重跑最多 ``retry_env_errors`` 次；**末次结果为准**，
      每次 result 记入 ``attempts`` 数组留痕（审计两次不一致，B31）。
      Cases ending in env_error are re-run up to ``retry_env_errors`` times;
      the last result wins, every attempt is recorded in ``attempts`` (B31).
    - ``max_total_s`` 为总时长预算；超出后剩余用例直接记
      ``skip(detail="budget_exceeded")``，防重试叠加无上限（B32）。
      ``max_total_s`` is the total time budget; once exceeded the remaining
      cases are recorded as budget-exceeded skips (B32).

    Returns / 返回:
        {"ok", "total", "pass", "fail", "env_error", "error", "skip",
         "results": [{...run 结果, "attempts": [各次 result]}]}
    """
    start = time.monotonic()

    def _over_budget() -> bool:
        return max_total_s is not None and time.monotonic() - start >= max_total_s

    results: List[dict] = []
    for case in cases:
        if _over_budget():
            results.append(_budget_skip(case))
            continue

        attempts: List[str] = []
        outcome = case.run(ctx)
        attempts.append(outcome["result"])
        for _ in range(retry_env_errors):
            if outcome["result"] != "env_error" or _over_budget():
                break
            outcome = case.run(ctx)
            attempts.append(outcome["result"])

        results.append({**outcome, "attempts": attempts})

    counts = {key: sum(1 for r in results if r["result"] == key) for key in _RESULTS}
    ok = counts["fail"] == 0 and counts["error"] == 0 and counts["env_error"] == 0
    return {"ok": ok, "total": len(results), **counts, "results": results}
