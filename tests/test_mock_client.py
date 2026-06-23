"""MockControlClient 集成测试:client ↔ 真实 MockServer 往返验证。

Integration tests for MockControlClient against a real MockServer instance.
"""

import urllib.request

import pytest

from phone_pilot.mock import MockServer, RequestLog, RuleStore
from phone_pilot.mock.client import MockControlClient


@pytest.fixture()
def server():
    """启动一个使用临时端口的 MockServer,测试结束自动 stop。"""
    store = RuleStore()
    log = RequestLog()
    srv = MockServer(store, log, ctrl_port=0, data_port=0)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture()
def client(server: MockServer) -> MockControlClient:
    """用 server 的 ctrl_port 构造 MockControlClient。"""
    return MockControlClient(server.ctrl_port)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _data_get(server: MockServer, path: str) -> tuple[int, bytes]:
    """向 server 的数据面发一个 GET 请求,返回 (status_code, body_bytes)。"""
    url = f"http://127.0.0.1:{server.data_port}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_set_rule_then_data_hit(server: MockServer, client: MockControlClient) -> None:
    """client.set_rule 写入规则后,直接打数据面能命中并拿到正确 body。"""
    result = client.set_rule("GET", "/api/hello", body={"msg": "hi"}, status=200)
    assert result["ok"] is True
    assert "rule_id" in result

    status_code, body = _data_get(server, "/api/hello")
    assert status_code == 200
    assert b"hi" in body


def test_load_seed_roundtrip(client: MockControlClient) -> None:
    """client.load_seed 批量写入规则后,client.get_rules 能查到。"""
    seed = {
        "rules": [
            {"method": "GET", "path": "/a", "body": "aaa", "status": 200},
            {"method": "POST", "path": "/b", "body": "bbb", "status": 201},
        ]
    }
    load_result = client.load_seed(seed)
    assert load_result["ok"] is True
    assert load_result["count"] == 2

    rules_result = client.get_rules()
    assert rules_result["ok"] is True
    paths = {r["path"] for r in rules_result["rules"]}
    assert "/a" in paths
    assert "/b" in paths


def test_get_rules(client: MockControlClient) -> None:
    """初始无规则时 get_rules 返回空列表;写入后能看到。"""
    result = client.get_rules()
    assert result["ok"] is True
    assert isinstance(result["rules"], list)

    client.set_rule("GET", "/ping", body="pong")
    result2 = client.get_rules()
    assert result2["ok"] is True
    assert any(r["path"] == "/ping" for r in result2["rules"])


def test_delete_rule(client: MockControlClient) -> None:
    """set_rule 后 delete_rule,再 get_rules 不再含该规则。"""
    set_result = client.set_rule("GET", "/to-delete", body="bye")
    assert set_result["ok"] is True
    rule_id = set_result["rule_id"]

    del_result = client.delete_rule(rule_id)
    assert del_result["ok"] is True
    assert del_result["deleted"] is True

    rules_result = client.get_rules()
    assert rules_result["ok"] is True
    assert not any(r["rule_id"] == rule_id for r in rules_result["rules"])


def test_get_requests_reflects_traffic(
    server: MockServer, client: MockControlClient
) -> None:
    """向数据面打几次请求后,get_requests 的 received_count 正确反映数量。"""
    client.set_rule("GET", "/counted", body="ok")

    for _ in range(3):
        _data_get(server, "/counted")
    # 额外打一个未命中的路径
    _data_get(server, "/no-such-path")

    req_result = client.get_requests()
    assert req_result["ok"] is True
    assert req_result["received_count"] == 4
    assert req_result["matched_count"] == 3
    assert isinstance(req_result["requests"], list)


def test_status(server: MockServer, client: MockControlClient) -> None:
    """status 响应包含所有预期字段且值合理。"""
    result = client.status()
    assert result["ok"] is True
    for field in (
        "running",
        "ctrl_port",
        "data_port",
        "rules_count",
        "received_count",
        "matched_count",
    ):
        assert field in result, f"缺少字段: {field}"
    assert result["ctrl_port"] == server.ctrl_port
    assert result["data_port"] == server.data_port
    assert result["running"] is True


def test_connection_error_returns_error() -> None:
    """对一个无监听端口的 client,任意方法返回 {"ok": False, ...} 而不抛异常。"""
    # 端口 1 通常无监听且不可连(Unix reserved port)
    bad_client = MockControlClient(1)
    for call in (
        lambda: bad_client.status(),
        lambda: bad_client.get_rules(),
        lambda: bad_client.get_requests(),
        lambda: bad_client.set_rule("GET", "/x", body=None),
        lambda: bad_client.load_seed({"rules": []}),
        lambda: bad_client.delete_rule("GET:/x"),
    ):
        result = call()
        assert result.get("ok") is False, f"期望 ok=False,实际: {result}"
        assert "error" in result


def test_load_seed_invalid_returns_error(client: MockControlClient) -> None:
    """load_seed 传非法 seed(缺少 rules 键)时,返回 {"ok": False, ...}。"""
    result = client.load_seed({"bad_key": []})
    assert result["ok"] is False
    assert "error" in result


def test_delete_rule_with_colon_in_id(client: MockControlClient) -> None:
    """rule_id 含冒号(如 GET:/path)时 URL 编码正确,delete 能成功。"""
    set_result = client.set_rule("GET", "/colon-test", body="x")
    rule_id = set_result["rule_id"]
    assert ":" in rule_id  # 确认 rule_id 确实含冒号

    del_result = client.delete_rule(rule_id)
    assert del_result["ok"] is True
    assert del_result["deleted"] is True
