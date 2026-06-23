"""MockServer HTTP 服务层集成测试 / Integration tests for MockServer HTTP service layer."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from phone_pilot.mock import RequestLog, RuleStore
from phone_pilot.mock.server import MockServer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def server() -> "MockServer":
    """启动一个临时端口 MockServer,测试结束后自动 stop。"""
    store = RuleStore()
    log = RequestLog()
    srv = MockServer(store, log, ctrl_port=0, data_port=0)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture()
def server_with_parts() -> "tuple[MockServer, RuleStore, RequestLog]":
    """返回 (server, store, log) 三元组,方便测试直接操作 store/log。"""
    store = RuleStore()
    log = RequestLog()
    srv = MockServer(store, log, ctrl_port=0, data_port=0)
    srv.start()
    yield srv, store, log
    srv.stop()


def _get(url: str, timeout: float = 5.0) -> tuple[int, bytes, dict]:
    """发 GET 请求,返回 (status, body_bytes, headers_dict)。"""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def _post(url: str, data: dict, timeout: float = 5.0) -> tuple[int, bytes]:
    """发 POST 请求,返回 (status, body_bytes)。"""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _delete(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    """发 DELETE 请求,返回 (status, body_bytes)。"""
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def _get_404(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    """发 GET 请求,期待 404,返回 (status, body_bytes)。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post_raw(url: str, data: dict, timeout: float = 5.0) -> tuple[int, bytes]:
    """发 POST 请求,允许 4xx 响应,返回 (status, body_bytes)。"""
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---------------------------------------------------------------------------
# 基础端口测试
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_ephemeral_ports_assigned(server: MockServer) -> None:
    """port=0 启动后 ctrl_port/data_port 为正整数且不相等。"""
    assert server.ctrl_port > 0
    assert server.data_port > 0
    assert server.ctrl_port != server.data_port


# ---------------------------------------------------------------------------
# 数据面测试
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_data_hit_returns_body(server_with_parts: tuple) -> None:
    """写规则后打 data_port 拿到 body。"""
    srv, store, log = server_with_parts
    store.set_rule("GET", "/api/hello", status=200, body={"message": "hi"})

    url = f"http://127.0.0.1:{srv.data_port}/api/hello"
    status, body_bytes, headers = _get(url)

    assert status == 200
    payload = json.loads(body_bytes)
    assert payload == {"message": "hi"}
    assert "application/json" in headers.get("Content-Type", "")


@pytest.mark.integration
def test_data_miss_returns_404_and_logged(server_with_parts: tuple) -> None:
    """未命中 404 且 received_count 增加。"""
    srv, store, log = server_with_parts

    url = f"http://127.0.0.1:{srv.data_port}/no/such/path"
    status, _ = _get_404(url)

    assert status == 404
    assert log.received_count() == 1
    assert log.matched_count() == 0


@pytest.mark.integration
def test_empty_store_logs_requests(server_with_parts: tuple) -> None:
    """空规则表,打几次,received_count 准确。"""
    srv, store, log = server_with_parts

    for i in range(3):
        _get_404(f"http://127.0.0.1:{srv.data_port}/path/{i}")

    assert log.received_count() == 3
    assert log.matched_count() == 0


@pytest.mark.integration
def test_data_port_cannot_mutate_rules(server_with_parts: tuple) -> None:
    """向 data_port 发 POST /rules,断言 store 规则数不变、返回 404。"""
    srv, store, log = server_with_parts
    # 预置一条规则
    store.set_rule("GET", "/existing", status=200)
    initial_count = len(store.list_rules())

    # 向数据面发控制请求 — 应 404,不改 store
    status, _ = _post_raw(
        f"http://127.0.0.1:{srv.data_port}/rules",
        {"method": "GET", "path": "/injected", "status": 200},
    )

    assert status == 404
    assert len(store.list_rules()) == initial_count


@pytest.mark.integration
def test_concurrent_requests_threadsafe(server_with_parts: tuple) -> None:
    """并发打 data_port N 次,最终 received_count==N。"""
    srv, store, log = server_with_parts
    n_threads = 10
    n_per_thread = 10
    total = n_threads * n_per_thread

    def worker() -> None:
        for _ in range(n_per_thread):
            # 重试以应对偶发连接重置
            for _attempt in range(5):
                try:
                    _get_404(f"http://127.0.0.1:{srv.data_port}/concurrent")
                    break
                except (urllib.error.URLError, ConnectionResetError):
                    time.sleep(0.01)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert log.received_count() == total


@pytest.mark.integration
def test_delay_response(server_with_parts: tuple) -> None:
    """设 delay_ms,测响应耗时 >= delay。"""
    srv, store, log = server_with_parts
    delay_ms = 200
    store.set_rule("GET", "/slow", status=200, body="ok", delay_ms=delay_ms)

    url = f"http://127.0.0.1:{srv.data_port}/slow"
    start = time.monotonic()
    status, _, _headers = _get(url, timeout=5.0)
    elapsed_ms = (time.monotonic() - start) * 1000

    assert status == 200
    assert elapsed_ms >= delay_ms


@pytest.mark.integration
def test_data_str_body_returned_as_is(server_with_parts: tuple) -> None:
    """规则 body 为 str 时原样返回。"""
    srv, store, log = server_with_parts
    store.set_rule("GET", "/text", status=200, body="hello world")

    url = f"http://127.0.0.1:{srv.data_port}/text"
    status, body_bytes, _ = _get(url)
    assert status == 200
    assert body_bytes == b"hello world"


@pytest.mark.integration
def test_data_none_body_returns_empty(server_with_parts: tuple) -> None:
    """规则 body 为 None 时返回空体。"""
    srv, store, log = server_with_parts
    store.set_rule("GET", "/empty", status=204, body=None)

    url = f"http://127.0.0.1:{srv.data_port}/empty"
    status, body_bytes, _ = _get(url)
    assert status == 204
    assert body_bytes == b""


# ---------------------------------------------------------------------------
# 控制面测试
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_control_set_and_get_rules(server: MockServer) -> None:
    """经控制面 POST /rules 再 GET /rules 往返。"""
    ctrl = f"http://127.0.0.1:{server.ctrl_port}"

    # POST 新规则
    post_status, post_body = _post(
        f"{ctrl}/rules",
        {"method": "GET", "path": "/api/ping", "status": 200, "body": {"pong": True}},
    )
    assert post_status == 200
    post_payload = json.loads(post_body)
    assert post_payload["ok"] is True
    rule_id = post_payload["rule_id"]
    assert rule_id

    # GET 规则列表
    get_status, get_body, _ = _get(f"{ctrl}/rules")
    assert get_status == 200
    get_payload = json.loads(get_body)
    assert get_payload["ok"] is True
    rules = get_payload["rules"]
    assert len(rules) == 1
    assert rules[0]["rule_id"] == rule_id


@pytest.mark.integration
def test_control_delete_rule(server: MockServer) -> None:
    """经控制面删除规则。"""
    ctrl = f"http://127.0.0.1:{server.ctrl_port}"

    _, post_body = _post(
        f"{ctrl}/rules",
        {"method": "GET", "path": "/todelete"},
    )
    rule_id = json.loads(post_body)["rule_id"]

    del_status, del_body = _delete(f"{ctrl}/rules/{rule_id}")
    assert del_status == 200
    del_payload = json.loads(del_body)
    assert del_payload["ok"] is True
    assert del_payload["deleted"] is True

    # 再次删除 → deleted=False
    del2_status, del2_body = _delete(f"{ctrl}/rules/{rule_id}")
    assert del2_status == 200
    assert json.loads(del2_body)["deleted"] is False


@pytest.mark.integration
def test_control_bulk_load(server: MockServer) -> None:
    """POST /rules/bulk 批量加载。"""
    ctrl = f"http://127.0.0.1:{server.ctrl_port}"

    seed = {
        "rules": [
            {"method": "GET", "path": "/a", "status": 200},
            {"method": "POST", "path": "/b", "status": 201},
        ]
    }
    status, body = _post(f"{ctrl}/rules/bulk", seed)
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["count"] == 2


@pytest.mark.integration
def test_control_bulk_load_invalid_returns_400(server: MockServer) -> None:
    """非法 seed → 400 + ok:False。"""
    ctrl = f"http://127.0.0.1:{server.ctrl_port}"

    status, body = _post_raw(f"{ctrl}/rules/bulk", {"rules": [{"method": "GET"}]})
    assert status == 400
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "error" in payload


@pytest.mark.integration
def test_status_payload(server_with_parts: tuple) -> None:
    """GET /status 字段齐全。"""
    srv, store, log = server_with_parts
    store.set_rule("GET", "/s", status=200)
    _get_404(f"http://127.0.0.1:{srv.data_port}/s")

    status, body, _ = _get(f"http://127.0.0.1:{srv.ctrl_port}/status")
    assert status == 200
    payload = json.loads(body)

    assert payload["ok"] is True
    assert payload["running"] is True
    assert payload["ctrl_port"] == srv.ctrl_port
    assert payload["data_port"] == srv.data_port
    assert "rules_count" in payload
    assert "received_count" in payload
    assert "matched_count" in payload


@pytest.mark.integration
def test_get_requests_log(server_with_parts: tuple) -> None:
    """GET /requests 返回日志字段齐全。"""
    srv, store, log = server_with_parts
    _get_404(f"http://127.0.0.1:{srv.data_port}/logged")

    status, body, _ = _get(f"http://127.0.0.1:{srv.ctrl_port}/requests")
    assert status == 200
    payload = json.loads(body)

    assert payload["ok"] is True
    assert payload["received_count"] >= 1
    assert "matched_count" in payload
    assert isinstance(payload["requests"], list)
    entry = payload["requests"][0]
    assert "method" in entry
    assert "path" in entry
    assert "matched" in entry
    assert "ts" in entry


# ---------------------------------------------------------------------------
# 停止与状态测试
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_stop_idempotent() -> None:
    """连续 stop 两次不抛。"""
    store = RuleStore()
    log = RequestLog()
    srv = MockServer(store, log, ctrl_port=0, data_port=0)
    srv.start()
    assert srv.is_running is True

    result1 = srv.stop()
    assert result1 == {"ok": True}
    assert srv.is_running is False

    result2 = srv.stop()
    assert result2 == {"ok": True}


@pytest.mark.integration
def test_is_running_before_start() -> None:
    """未 start 时 is_running 为 False。"""
    store = RuleStore()
    log = RequestLog()
    srv = MockServer(store, log)
    assert srv.is_running is False
