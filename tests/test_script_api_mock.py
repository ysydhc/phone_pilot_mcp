"""script_api mock 面授权测试 / Tests for script_api mock surface.

测试 mock_server_start/stop/set_rule/load_seed/mock_assert_switched
以及接入 ScriptContext 生命周期（context._mock_server + _finalize_run 自动清理）。
All tests use local loopback; adb reverse is monkeypatched — no real device needed.
"""

from __future__ import annotations

import urllib.request
from typing import Any
from unittest.mock import patch

import pytest

from phone_pilot.script_api.context import ScriptContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> ScriptContext:
    """构造最简 ScriptContext，不触发设备连接。"""
    return ScriptContext(device_serial="fake-serial")


def _fake_reverse_manager_cls(setup_ok: bool = True, teardown_ok: bool = True):
    """返回一个 fake ReverseManager 类，记录 setup/teardown 调用。"""

    class _FakeRM:
        calls: list[tuple[str, Any]] = []

        def __init__(self, serial: str, **kwargs: Any) -> None:
            self._serial = serial

        def setup(self, port: int) -> dict:
            _FakeRM.calls.append(("setup", port))
            if setup_ok:
                return {"ok": True}
            return {"ok": False, "error": "fake_setup_fail"}

        def teardown(self, port: int) -> dict:
            _FakeRM.calls.append(("teardown", port))
            if teardown_ok:
                return {"ok": True}
            return {"ok": False, "error": "fake_teardown_fail"}

    _FakeRM.calls = []
    return _FakeRM


# ---------------------------------------------------------------------------
# test_start_stop_lifecycle
# ---------------------------------------------------------------------------


def test_start_stop_lifecycle() -> None:
    """start 后 ctx._mock_server 非空，有正端口；stop 后为 None。"""
    from phone_pilot.script_api.mock import mock_server_start, mock_server_stop

    FakeRM = _fake_reverse_manager_cls()
    ctx = _make_ctx()

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        result = mock_server_start(ctx)

    assert result["ok"] is True
    assert isinstance(result["ctrl_port"], int) and result["ctrl_port"] > 0
    assert isinstance(result["data_port"], int) and result["data_port"] > 0
    assert ctx._mock_server is not None

    # stop
    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        stop_result = mock_server_stop(ctx)

    assert stop_result["ok"] is True
    assert ctx._mock_server is None

    # verify reverse setup/teardown were called
    setup_calls = [c for c in FakeRM.calls if c[0] == "setup"]
    teardown_calls = [c for c in FakeRM.calls if c[0] == "teardown"]
    assert len(setup_calls) == 1
    assert len(teardown_calls) == 1


# ---------------------------------------------------------------------------
# test_start_with_seed
# ---------------------------------------------------------------------------


def test_start_with_seed() -> None:
    """start 传 seed，规则已加载到 store。"""
    from phone_pilot.script_api.mock import mock_server_start, mock_server_stop

    FakeRM = _fake_reverse_manager_cls()
    ctx = _make_ctx()
    seed = {
        "rules": [
            {"method": "GET", "path": "/api/ping", "status": 200, "body": {"pong": 1}}
        ]
    }

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        result = mock_server_start(ctx, seed=seed)

    assert result["ok"] is True
    # 验证规则已写入 store
    rules = ctx._mock_server.store.list_rules()  # type: ignore[union-attr]
    assert any(r.path == "/api/ping" for r in rules)

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_stop(ctx)


# ---------------------------------------------------------------------------
# test_set_rule_via_ctx
# ---------------------------------------------------------------------------


def test_set_rule_via_ctx() -> None:
    """start 后 mock_set_rule 成功，数据面能命中规则（直接 GET data_port）。"""
    from phone_pilot.script_api.mock import (
        mock_server_start,
        mock_server_stop,
        mock_set_rule,
    )

    FakeRM = _fake_reverse_manager_cls()
    ctx = _make_ctx()

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        start_result = mock_server_start(ctx)

    assert start_result["ok"] is True
    data_port = start_result["data_port"]

    rule_result = mock_set_rule(ctx, "GET", "/hello", {"msg": "world"}, status=200)
    assert rule_result["ok"] is True

    # 数据面验证：直接 HTTP GET
    url = f"http://127.0.0.1:{data_port}/hello"
    with urllib.request.urlopen(url, timeout=5) as resp:
        assert resp.status == 200

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_stop(ctx)


# ---------------------------------------------------------------------------
# test_assert_switched_pass
# ---------------------------------------------------------------------------


def test_assert_switched_pass() -> None:
    """先打一次 data_port 产生流量，assert_switched min_hits=1 通过。"""
    from phone_pilot.script_api.mock import (
        mock_server_start,
        mock_server_stop,
        mock_set_rule,
        mock_assert_switched,
    )

    FakeRM = _fake_reverse_manager_cls()
    ctx = _make_ctx()

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        start_result = mock_server_start(ctx)

    data_port = start_result["data_port"]
    mock_set_rule(ctx, "GET", "/check", {"ok": True})

    # 产生一次流量
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{data_port}/check", timeout=3)
    except Exception:
        pass

    result = mock_assert_switched(ctx, min_hits=1, timeout_s=5)
    assert result["ok"] is True
    assert result["received_count"] >= 1

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_stop(ctx)


# ---------------------------------------------------------------------------
# test_assert_switched_timeout
# ---------------------------------------------------------------------------


def test_assert_switched_timeout() -> None:
    """无流量，timeout_s 设小，返回 ok:False 且 error=app_not_switched 且含 note。"""
    from phone_pilot.script_api.mock import (
        mock_server_start,
        mock_server_stop,
        mock_assert_switched,
    )

    FakeRM = _fake_reverse_manager_cls()
    ctx = _make_ctx()

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_start(ctx)

    result = mock_assert_switched(ctx, min_hits=1, timeout_s=0.5)
    assert result["ok"] is False
    assert result["error"] == "app_not_switched"
    assert "note" in result
    assert "received_count" in result

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_stop(ctx)


# ---------------------------------------------------------------------------
# test_stop_idempotent
# ---------------------------------------------------------------------------


def test_stop_idempotent() -> None:
    """未 start 直接 stop 不抛；start 后 stop 两次不抛。"""
    from phone_pilot.script_api.mock import mock_server_start, mock_server_stop

    FakeRM = _fake_reverse_manager_cls()

    # 未 start 直接 stop
    ctx1 = _make_ctx()
    result = mock_server_stop(ctx1)
    assert isinstance(result, dict)  # ok 或 error 均可，但不抛

    # start 后 stop 两次
    ctx2 = _make_ctx()
    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_start(ctx2)
        mock_server_stop(ctx2)
        result2 = mock_server_stop(ctx2)  # 第二次 stop
    assert isinstance(result2, dict)


# ---------------------------------------------------------------------------
# test_listed_in_public_api
# ---------------------------------------------------------------------------


def test_listed_in_public_api() -> None:
    """mock_server_start 出现在 list_public_api() 结果中。"""
    from phone_pilot.script_api._introspect import list_public_api

    names = [x["name"] for x in list_public_api()]
    assert "mock_server_start" in names
    assert "mock_server_stop" in names
    assert "mock_set_rule" in names
    assert "mock_load_seed" in names
    assert "mock_assert_switched" in names


# ---------------------------------------------------------------------------
# test_finalize_auto_stops
# ---------------------------------------------------------------------------


def test_finalize_auto_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """start 后调用 _finalize_run，断言 mock_server_stop 被调用且 ctx._mock_server 被清理。"""
    from phone_pilot.script_api import mock as mock_module
    from phone_pilot.script_api.runner import _finalize_run

    FakeRM = _fake_reverse_manager_cls()
    ctx = _make_ctx()

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_module.mock_server_start(ctx)

    assert ctx._mock_server is not None

    # spy mock_server_stop
    original_stop = mock_module.mock_server_stop
    called: list[bool] = []

    def spy_stop(c: ScriptContext) -> dict:
        called.append(True)
        with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
            return original_stop(c)

    # runner._finalize_run 用函数内局部 `from .mock import mock_server_stop`,
    # 运行时从 mock 模块取属性,故只需 patch mock 模块即可让 spy 生效。
    monkeypatch.setattr(mock_module, "mock_server_stop", spy_stop)

    _finalize_run(ctx, 0, "")

    assert len(called) >= 1
    assert ctx._mock_server is None


# ---------------------------------------------------------------------------
# test_set_rule_not_started
# ---------------------------------------------------------------------------


def test_set_rule_not_started() -> None:
    """未 start 时调用 mock_set_rule 返回 ok:False，不抛。"""
    from phone_pilot.script_api.mock import mock_set_rule

    ctx = _make_ctx()
    result = mock_set_rule(ctx, "GET", "/foo", None)
    assert result["ok"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# test_load_seed_via_ctx
# ---------------------------------------------------------------------------


def test_load_seed_via_ctx() -> None:
    """start 后 mock_load_seed 成功，规则写入 store。"""
    from phone_pilot.script_api.mock import (
        mock_server_start,
        mock_server_stop,
        mock_load_seed,
    )

    FakeRM = _fake_reverse_manager_cls()
    ctx = _make_ctx()

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_start(ctx)

    seed = {
        "rules": [
            {
                "method": "POST",
                "path": "/api/login",
                "status": 200,
                "body": {"token": "abc"},
            }
        ]
    }
    result = mock_load_seed(ctx, seed)
    assert result["ok"] is True

    rules = ctx._mock_server.store.list_rules()  # type: ignore[union-attr]
    assert any(r.path == "/api/login" for r in rules)

    with patch("phone_pilot.script_api.mock.ReverseManager", FakeRM):
        mock_server_stop(ctx)
