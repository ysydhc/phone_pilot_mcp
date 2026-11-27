"""Mock server 内存数据结构:规则表与请求日志。

In-memory rule store and request log for the local HTTP mock server.
"""

import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Rule:
    """单条匹配规则 / A single mock rule."""

    method: str
    path: str
    status: int = 200
    body: dict | str | None = None
    headers: dict = field(default_factory=dict)
    delay_ms: int = 0
    rule_id: str = ""
    responses: tuple[dict, ...] = ()
    seq_group: str = ""


def _normalize_method(method: str) -> str:
    return method.upper()


def _normalize_path(path: str) -> str:
    if not path.startswith("/"):
        return "/" + path
    return path


def _strip_query(path: str) -> str:
    """去除 query string(? 之后全部去掉)"""
    return urlsplit(path).path


def _make_rule_id(method: str, path: str) -> str:
    return f"{method}:{path}"


class RuleStore:
    """纯内存规则表 / In-memory rule store (thread-safe via RLock)."""

    def __init__(self) -> None:
        # key: rule_id (str) → Rule
        self._rules: dict[str, Rule] = {}
        self._seq_counters: dict[str, int] = {}
        self._lock = threading.RLock()

    def set_rule(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        body: dict | str | None = None,
        headers: dict | None = None,
        delay_ms: int = 0,
        responses: list[dict] | tuple[dict, ...] | None = None,
        seq_group: str = "",
    ) -> str:
        """添加或覆盖规则,返回 rule_id。"""
        norm_method = _normalize_method(method)
        norm_path = _normalize_path(path)
        rule_id = _make_rule_id(norm_method, norm_path)
        seq_tuple = tuple(dict(x) for x in (responses or ()) if isinstance(x, dict))
        rule = Rule(
            method=norm_method,
            path=norm_path,
            status=status,
            body=body,
            headers=headers if headers is not None else {},
            delay_ms=delay_ms,
            rule_id=rule_id,
            responses=seq_tuple,
            seq_group=str(seq_group or ""),
        )
        with self._lock:
            self._rules[rule_id] = rule
            self._seq_counters[rule.seq_group or rule_id] = 0
        return rule_id

    def delete_rule(self, rule_id: str) -> bool:
        """删除规则。存在返回 True,否则 False。"""
        with self._lock:
            if rule_id in self._rules:
                r = self._rules.pop(rule_id)
                self._seq_counters.pop(r.seq_group or rule_id, None)
                return True
            return False

    def match(self, method: str, path: str) -> Rule | None:
        """精确匹配:先剥离 query string,再按 method+path 查找。若配置了按序 responses 则原子推进计数。"""
        norm_method = _normalize_method(method)
        clean_path = _normalize_path(_strip_query(path))
        rule_id = _make_rule_id(norm_method, clean_path)
        with self._lock:
            rule = self._rules.get(rule_id)
            if rule is None:
                return None
            if not rule.responses:
                return rule
            seq_key = rule.seq_group or rule.rule_id
            idx = self._seq_counters.get(seq_key, 0)
            self._seq_counters[seq_key] = idx + 1
            step = rule.responses[min(idx, len(rule.responses) - 1)]
            return Rule(
                method=rule.method,
                path=rule.path,
                status=int(step.get("status", rule.status)),
                body=step.get("body", rule.body),
                headers=dict(step.get("headers") or rule.headers),
                delay_ms=int(step.get("delay_ms", rule.delay_ms)),
                rule_id=rule.rule_id,
                responses=rule.responses,
                seq_group=rule.seq_group,
            )

    def list_rules(self) -> list[Rule]:
        """返回所有规则的快照列表。"""
        with self._lock:
            return list(self._rules.values())

    def load_seed(self, seed: dict) -> int:  # type: ignore[type-arg]
        """批量加载规则。原子操作:全部校验通过后才提交,否则 raise ValueError 且不改变状态。"""
        if not isinstance(seed, dict):
            raise ValueError(f"seed 必须是 dict,got {type(seed).__name__}")
        if "rules" not in seed:
            raise ValueError("seed 缺少 'rules' 键")
        rules_list = seed["rules"]
        if not isinstance(rules_list, list):
            raise ValueError(
                f"seed['rules'] 必须是 list,got {type(rules_list).__name__}"
            )

        # 先全部校验并构造 Rule 对象,不修改任何状态
        pending: list[tuple[str, Rule]] = []
        for i, item in enumerate(rules_list):
            if not isinstance(item, dict):
                raise ValueError(f"rules[{i}] 必须是 dict")
            if "method" not in item:
                raise ValueError(f"rules[{i}] 缺少 'method' 字段")
            if "path" not in item:
                raise ValueError(f"rules[{i}] 缺少 'path' 字段")
            norm_method = _normalize_method(item["method"])
            norm_path = _normalize_path(item["path"])
            rule_id = _make_rule_id(norm_method, norm_path)
            raw_seq = item.get("responses")
            seq_tuple = (
                tuple(dict(x) for x in raw_seq if isinstance(x, dict))
                if isinstance(raw_seq, list)
                else ()
            )
            rule = Rule(
                method=norm_method,
                path=norm_path,
                status=int(item.get("status", 200)),
                body=item.get("body"),
                headers=item.get("headers") or {},
                delay_ms=int(item.get("delay_ms", 0)),
                rule_id=rule_id,
                responses=seq_tuple,
                seq_group=str(item.get("seq_group") or ""),
            )
            pending.append((rule_id, rule))

        # 全部校验通过,一次性提交
        with self._lock:
            for rule_id, rule in pending:
                self._rules[rule_id] = rule
                self._seq_counters[rule.seq_group or rule_id] = 0
        return len(pending)

    def clear(self) -> None:
        """清空所有规则。"""
        with self._lock:
            self._rules.clear()
            self._seq_counters.clear()


@dataclass(frozen=True)
class RequestEntry:
    """单条请求日志条目 / A single request log entry."""

    method: str
    path: str
    matched: bool
    rule_id: str | None
    ts: float
    mono_ts: float = 0.0
    body: str = ""


class RequestLog:
    """纯内存请求日志,线程安全 / Thread-safe in-memory request log."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[RequestEntry] = []
        self._matched: int = 0

    def record(
        self,
        method: str,
        path: str,
        matched: bool,
        rule_id: str | None,
        *,
        body: str = "",
    ) -> None:
        """记录一次请求。同时记录 wall-clock ts、单调时钟 mono_ts 与可选请求体快照。"""
        entry = RequestEntry(
            method=method,
            path=path,
            matched=matched,
            rule_id=rule_id,
            ts=time.time(),
            mono_ts=time.monotonic(),
            body=body,
        )
        with self._lock:
            self._entries.append(entry)
            if matched:
                self._matched += 1

    def entries(self) -> list[RequestEntry]:
        """返回所有条目的快照副本。"""
        with self._lock:
            return list(self._entries)

    def received_count(self) -> int:
        """收到的请求总数。"""
        with self._lock:
            return len(self._entries)

    def matched_count(self) -> int:
        """命中规则的数量。"""
        with self._lock:
            return self._matched

    def clear(self) -> None:
        """清空日志。"""
        with self._lock:
            self._entries.clear()
            self._matched = 0
