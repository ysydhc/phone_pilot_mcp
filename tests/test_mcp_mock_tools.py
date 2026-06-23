"""MCP mock tools 集成测试。

Integration tests for phone_mock_* MCP tools.

测试策略:
- 直接调用 server 模块命名空间里的 phone_mock_* 工具函数(不经 tool manager 分发),
  因此无需开启 PHONE_PILOT_LOAD_ALL_TOOLS;避免该环境变量泄漏污染其它测试的分层断言。
- reverse 用 monkeypatch fake,避免真机
- _resolve_device_serial monkeypatch 返回固定 serial,避免真机扫描
- mock server 使用本地回环,端口随机分配
"""

from __future__ import annotations

import asyncio
import urllib.request
from typing import Any

import pytest

from phone_pilot.mcp.server import (
    phone_mock_start,
    phone_mock_stop,
    phone_mock_set_rule,
    phone_mock_load_seed,
    phone_mock_get_rules,
    phone_mock_delete_rule,
    phone_mock_get_requests,
    phone_mock_status,
    phone_mock_assert_switched,
)
from phone_pilot.mcp._tool_registry import EXTENDED_CATEGORIES

# ---------------------------------------------------------------------------
# 固定测试 serial
# ---------------------------------------------------------------------------

FAKE_SERIAL = "emulator-5554"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro) -> Any:
    """在同步测试中运行协程。"""
    return asyncio.run(coro)


def _start(monkeypatch, **kwargs) -> dict:
    """启动 mock server,monkeypatch 掉 reverse + serial 解析。"""
    _patch_all(monkeypatch)
    result = _run(
        phone_mock_start(
            device_serial=FAKE_SERIAL,
            platform="android",
            **kwargs,
        )
    )
    return result


def _patch_all(monkeypatch) -> None:
    """统一 monkeypatch:fake reverse + fake serial resolution。"""
    # 1. fake ReverseManager.setup / teardown / remove_all / list
    import phone_pilot.mcp._mock_tools as mt

    class FakeReverseManager:
        def __init__(self, serial, **kw):
            pass

        def setup(self, port: int) -> dict:
            return {"ok": True}

        def teardown(self, port: int) -> dict:
            return {"ok": True}

        def remove_all(self) -> dict:
            return {"ok": True}

        def list(self) -> list:
            return []

    monkeypatch.setattr(mt, "ReverseManager", FakeReverseManager)

    # 2. fake _resolve_device_serial → 直接返回 FAKE_SERIAL / android
    import phone_pilot.mcp._helpers as helpers

    def fake_resolve(serial, platform="auto"):
        return FAKE_SERIAL, "android", None

    monkeypatch.setattr(helpers, "_resolve_device_serial", fake_resolve)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stop_after(monkeypatch):
    """每个测试结束后都 stop,保证句柄清理。"""
    _patch_all(monkeypatch)
    yield
    _run(phone_mock_stop())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mock_start_status_stop(monkeypatch):
    """start → status 含端口 → stop 全流程测试。"""
    r = _start(monkeypatch)
    assert r["ok"] is True
    assert r["ctrl_port"] > 0
    assert r["data_port"] > 0

    ctrl_port = r["ctrl_port"]
    data_port = r["data_port"]

    # status
    s = _run(phone_mock_status())
    assert s["ok"] is True
    assert s["ctrl_port"] == ctrl_port
    assert s["data_port"] == data_port
    assert s["running"] is True

    # stop
    stop_r = _run(phone_mock_stop())
    assert stop_r["ok"] is True

    # status after stop → not running
    s2 = _run(phone_mock_status())
    assert s2["ok"] is False
    assert s2["error"] == "mock_not_running"


def test_set_rule_then_get_rules(monkeypatch):
    """start → set_rule → get_rules 包含该条规则。"""
    _start(monkeypatch)

    sr = _run(phone_mock_set_rule(method="GET", path="/api/hello", body={"msg": "hi"}))
    assert sr["ok"] is True
    assert "rule_id" in sr

    gr = _run(phone_mock_get_rules())
    assert gr["ok"] is True
    rules = gr["rules"]
    paths = [r["path"] for r in rules]
    assert "/api/hello" in paths


def test_load_seed(monkeypatch):
    """start → load_seed → get_rules 包含 seed 中的规则。"""
    _start(monkeypatch)

    seed = {
        "rules": [
            {"method": "GET", "path": "/seed/a", "status": 200, "body": "ok"},
            {"method": "POST", "path": "/seed/b", "status": 201, "body": None},
        ]
    }
    lr = _run(phone_mock_load_seed(seed=seed))
    assert lr["ok"] is True

    gr = _run(phone_mock_get_rules())
    assert gr["ok"] is True
    paths = [r["path"] for r in gr["rules"]]
    assert "/seed/a" in paths
    assert "/seed/b" in paths


def test_get_requests(monkeypatch):
    """start → 打 data_port → get_requests received_count 正确。"""
    r = _start(monkeypatch)
    data_port = r["data_port"]

    # 打几个请求到 data 端口
    for _ in range(3):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{data_port}/any/path", timeout=2)
        except Exception:
            pass  # 404 也 OK,只要打到了 data port

    req_r = _run(phone_mock_get_requests())
    assert req_r["ok"] is True
    assert req_r["received_count"] >= 3


def test_assert_switched_timeout(monkeypatch):
    """无流量时 assert_switched 超时返回 ok:False + note。"""
    _start(monkeypatch)

    result = _run(phone_mock_assert_switched(min_hits=1, timeout_s=1))
    assert result["ok"] is False
    assert result["error"] == "app_not_switched"
    assert "received_count" in result
    assert "note" in result
    assert "cleartext" in result["note"] or "baseURL" in result["note"]


def test_stop_idempotent(monkeypatch):
    """未 start 调 stop 不抛;start 后 stop 两次不抛。"""
    # 未 start 先 stop
    r1 = _run(phone_mock_stop())
    assert r1["ok"] is True  # 幂等,不抛

    # start 后 stop 两次
    _start(monkeypatch)
    r2 = _run(phone_mock_stop())
    assert r2["ok"] is True
    r3 = _run(phone_mock_stop())
    assert r3["ok"] is True  # 第二次 stop 也幂等


def test_tools_registered_in_mock_category():
    """mock category 存在且包含全部 9 个工具名。"""
    assert "mock" in EXTENDED_CATEGORIES, "EXTENDED_CATEGORIES 中缺少 'mock' key"

    expected = {
        "phone_mock_start",
        "phone_mock_stop",
        "phone_mock_set_rule",
        "phone_mock_load_seed",
        "phone_mock_get_rules",
        "phone_mock_delete_rule",
        "phone_mock_get_requests",
        "phone_mock_status",
        "phone_mock_assert_switched",
    }
    registered = set(EXTENDED_CATEGORIES["mock"])
    missing = expected - registered
    assert not missing, f"mock category 缺少工具: {missing}"
    extra = registered - expected
    assert not extra, f"mock category 有多余工具: {extra}"


def test_response_envelope(monkeypatch):
    """每个工具都返回 dict 且含 'ok' 键。"""
    _start(monkeypatch)

    results = [
        _run(phone_mock_status()),
        _run(phone_mock_get_rules()),
        _run(phone_mock_get_requests()),
        _run(phone_mock_set_rule(method="GET", path="/env/test", body=None)),
        _run(
            phone_mock_load_seed(
                seed={"rules": [{"method": "GET", "path": "/env/x", "status": 200}]}
            )
        ),
    ]

    # delete_rule:先 set 一条再删
    set_r = _run(phone_mock_set_rule(method="DELETE", path="/del/me", body=None))
    assert "rule_id" in set_r
    del_r = _run(phone_mock_delete_rule(rule_id=set_r["rule_id"]))
    results.append(del_r)

    for res in results:
        assert isinstance(res, dict), f"返回值不是 dict: {type(res)}"
        assert "ok" in res, f"返回值缺少 'ok' 键: {res}"


def test_not_started_returns_error(monkeypatch):
    """未启动时,除 start/stop 外的工具返回 mock_not_running 错误。"""
    tools_to_check = [
        phone_mock_status(),
        phone_mock_get_rules(),
        phone_mock_get_requests(),
        phone_mock_set_rule(method="GET", path="/x", body=None),
        phone_mock_load_seed(seed={"rules": []}),
        phone_mock_delete_rule(rule_id="any"),
        phone_mock_assert_switched(min_hits=1, timeout_s=0.1),
    ]
    for coro in tools_to_check:
        r = _run(coro)
        assert r["ok"] is False
        assert r["error"] == "mock_not_running", f"期望 mock_not_running,得到 {r}"
