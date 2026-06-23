"""Mock server 数据结构层单元测试 / Unit tests for mock server data structures."""

import threading

import pytest

from phone_pilot.mock import RequestEntry, RequestLog, Rule, RuleStore


# ---------------------------------------------------------------------------
# RuleStore 测试
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule_exact_match() -> None:
    store = RuleStore()
    store.set_rule("GET", "/api/users", status=200, body={"ok": True})
    rule = store.match("GET", "/api/users")
    assert rule is not None
    assert rule.status == 200
    assert rule.body == {"ok": True}


@pytest.mark.unit
def test_query_string_stripped() -> None:
    """match("GET", "/cfg?ver=2") 应命中 set_rule("GET", "/cfg")"""
    store = RuleStore()
    store.set_rule("GET", "/cfg", status=204)
    rule = store.match("GET", "/cfg?ver=2")
    assert rule is not None
    assert rule.status == 204


@pytest.mark.unit
def test_rule_method_isolation() -> None:
    """GET 规则不命中 POST"""
    store = RuleStore()
    store.set_rule("GET", "/api/data", status=200)
    assert store.match("POST", "/api/data") is None


@pytest.mark.unit
def test_rule_override() -> None:
    """同 method+path 第二次 set 覆盖,list_rules 仍只有 1 条且为新值"""
    store = RuleStore()
    store.set_rule("GET", "/api/item", status=200, body="first")
    store.set_rule("GET", "/api/item", status=404, body="second")
    rules = store.list_rules()
    assert len(rules) == 1
    assert rules[0].status == 404
    assert rules[0].body == "second"


@pytest.mark.unit
def test_match_miss_returns_none() -> None:
    store = RuleStore()
    store.set_rule("GET", "/api/known", status=200)
    assert store.match("GET", "/api/unknown") is None


@pytest.mark.unit
def test_load_seed_count() -> None:
    store = RuleStore()
    seed = {
        "rules": [
            {"method": "GET", "path": "/a", "status": 200},
            {"method": "POST", "path": "/b", "status": 201, "body": {"created": True}},
            {"method": "DELETE", "path": "/c"},
        ]
    }
    count = store.load_seed(seed)
    assert count == 3
    assert len(store.list_rules()) == 3


@pytest.mark.unit
def test_invalid_seed_rejected() -> None:
    """非法结构 raise ValueError,且原有规则不变"""
    store = RuleStore()
    store.set_rule("GET", "/existing", status=200)

    # 缺少 path 的规则 → 整个 seed 应被拒绝
    bad_seed = {"rules": [{"method": "GET"}]}
    with pytest.raises(ValueError):
        store.load_seed(bad_seed)

    # 原有规则未被破坏
    rules = store.list_rules()
    assert len(rules) == 1
    assert rules[0].path == "/existing"

    # 非 dict
    with pytest.raises(ValueError):
        store.load_seed("not a dict")  # type: ignore[arg-type]

    # 没有 rules 键
    with pytest.raises(ValueError):
        store.load_seed({"other": []})

    # rules 不是列表
    with pytest.raises(ValueError):
        store.load_seed({"rules": "bad"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_delete_rule() -> None:
    store = RuleStore()
    rule_id = store.set_rule("GET", "/del", status=200)
    assert store.delete_rule(rule_id) is True
    assert store.match("GET", "/del") is None
    # 删除不存在的 id 返回 False
    assert store.delete_rule(rule_id) is False


@pytest.mark.unit
def test_method_uppercase_and_path_slash() -> None:
    """method 统一大写,path 统一有前导 /"""
    store = RuleStore()
    store.set_rule("get", "api/items", status=200)
    rule = store.match("GET", "/api/items")
    assert rule is not None
    assert rule.method == "GET"
    assert rule.path.startswith("/")


# ---------------------------------------------------------------------------
# RequestLog 测试
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_received_vs_matched_count() -> None:
    """record 几条命中几条未命中,断言两个 count"""
    log = RequestLog()
    log.record("GET", "/a", matched=True, rule_id="GET:/a")
    log.record("GET", "/b", matched=True, rule_id="GET:/b")
    log.record("GET", "/c", matched=False, rule_id=None)
    assert log.received_count() == 3
    assert log.matched_count() == 2


@pytest.mark.unit
def test_clear() -> None:
    store = RuleStore()
    store.set_rule("GET", "/x", status=200)
    store.set_rule("POST", "/y", status=201)

    log = RequestLog()
    log.record("GET", "/x", matched=True, rule_id="GET:/x")

    store.clear()
    log.clear()

    assert store.list_rules() == []
    assert log.entries() == []
    assert log.received_count() == 0
    assert log.matched_count() == 0


@pytest.mark.unit
def test_request_log_thread_safety() -> None:
    """并发 record 不崩溃,总数正确"""
    log = RequestLog()
    n = 100

    def worker() -> None:
        for _ in range(n):
            log.record("GET", "/t", matched=True, rule_id="GET:/t")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert log.received_count() == n * 10


@pytest.mark.unit
def test_entries_returns_copy() -> None:
    """entries() 返回的列表是副本,修改不影响内部状态"""
    log = RequestLog()
    log.record("GET", "/e", matched=False, rule_id=None)
    entries = log.entries()
    entries.clear()
    assert log.received_count() == 1


@pytest.mark.unit
def test_request_entry_fields() -> None:
    """RequestEntry 字段类型正确"""
    log = RequestLog()
    log.record("POST", "/submit", matched=True, rule_id="POST:/submit")
    entry = log.entries()[0]
    assert isinstance(entry, RequestEntry)
    assert entry.method == "POST"
    assert entry.path == "/submit"
    assert entry.matched is True
    assert entry.rule_id == "POST:/submit"
    assert isinstance(entry.ts, float)


# ---------------------------------------------------------------------------
# Rule dataclass 测试
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rule_defaults() -> None:
    rule = Rule(method="GET", path="/ping")
    assert rule.status == 200
    assert rule.body is None
    assert rule.headers == {}
    assert rule.delay_ms == 0
    assert rule.rule_id == ""


@pytest.mark.unit
def test_rule_is_frozen() -> None:
    """frozen dataclass — 赋值应抛出 FrozenInstanceError"""
    rule = Rule(method="GET", path="/ping")
    with pytest.raises(Exception):
        rule.status = 500  # type: ignore[misc]
