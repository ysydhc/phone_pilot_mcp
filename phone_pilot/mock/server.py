"""MockServer:双端口本地 HTTP mock 服务(控制面 + 数据面)。

Dual-port local HTTP mock server: control plane (rule management) + data plane (request stubbing).
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from phone_pilot.mock.rules import RequestLog, RuleStore


def _make_ctrl_handler(
    store: RuleStore, log: RequestLog, server_ref: "MockServer"
) -> type:
    """工厂函数:生成绑定了 store/log 的控制面 handler 类。"""

    class CtrlHandler(BaseHTTPRequestHandler):
        """控制面请求处理器 / Control plane request handler."""

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN401
            # 写到 stderr,避免污染 stdout(MCP stdio 协议通道)
            msg = fmt % args
            if len(msg) > 140:
                msg = msg[:100] + "...[truncated]..." + msg[-20:]
            sys.stderr.write(f"[ctrl] {msg}\n")

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> dict | None:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            try:
                return json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, ValueError):
                return None

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/rules":
                rules = store.list_rules()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "rules": [
                            {
                                "method": r.method,
                                "path": r.path,
                                "status": r.status,
                                "body": r.body,
                                "headers": r.headers,
                                "delay_ms": r.delay_ms,
                                "rule_id": r.rule_id,
                            }
                            for r in rules
                        ],
                    },
                )
            elif path == "/requests":
                entries = log.entries()
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "requests": [
                            {
                                "method": e.method,
                                "path": e.path,
                                "matched": e.matched,
                                "rule_id": e.rule_id,
                                "ts": e.ts,
                            }
                            for e in entries
                        ],
                        "received_count": log.received_count(),
                        "matched_count": log.matched_count(),
                    },
                )
            elif path == "/status":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "running": server_ref.is_running,
                        "ctrl_port": server_ref.ctrl_port,
                        "data_port": server_ref.data_port,
                        "rules_count": len(store.list_rules()),
                        "received_count": log.received_count(),
                        "matched_count": log.matched_count(),
                    },
                )
            else:
                self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            data = self._read_json_body()
            if data is None:
                self._send_json(400, {"ok": False, "error": "invalid JSON body"})
                return

            if path == "/rules":
                rule_id = store.set_rule(
                    data.get("method", "GET"),
                    data.get("path", "/"),
                    status=int(data.get("status", 200)),
                    body=data.get("body"),
                    headers=data.get("headers") or {},
                    delay_ms=int(data.get("delay_ms", 0)),
                )
                self._send_json(200, {"ok": True, "rule_id": rule_id})
            elif path == "/rules/bulk":
                try:
                    count = store.load_seed(data)
                    self._send_json(200, {"ok": True, "count": count})
                except ValueError as exc:
                    self._send_json(400, {"ok": False, "error": str(exc)})
            else:
                self._send_json(404, {"ok": False, "error": "not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            # 匹配 /rules/{id}
            prefix = "/rules/"
            if path.startswith(prefix):
                rule_id = path[len(prefix) :]
                deleted = store.delete_rule(rule_id)
                self._send_json(200, {"ok": True, "deleted": deleted})
            else:
                self._send_json(404, {"ok": False, "error": "not found"})

    return CtrlHandler


def _make_data_handler(store: RuleStore, log: RequestLog) -> type:
    """工厂函数:生成绑定了 store/log 的数据面 handler 类。"""

    class DataHandler(BaseHTTPRequestHandler):
        """数据面请求处理器:所有路径都走 match → 命中返回 stub,未命中 404。"""

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN401
            msg = fmt % args
            if len(msg) > 140:
                msg = msg[:100] + "...[truncated]..." + msg[-20:]
            sys.stderr.write(f"[data] {msg}\n")

        def _handle(self) -> None:
            method = self.command
            path = self.path

            rule = store.match(method, path)
            if rule is not None:
                if rule.delay_ms > 0:
                    time.sleep(rule.delay_ms / 1000.0)

                if isinstance(rule.body, dict):
                    body_bytes = json.dumps(rule.body).encode()
                    content_type = "application/json"
                elif isinstance(rule.body, str):
                    body_bytes = rule.body.encode()
                    content_type = "text/plain"
                else:
                    body_bytes = b""
                    content_type = "text/plain"

                self.send_response(rule.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body_bytes)))
                for k, v in rule.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                if body_bytes:
                    self.wfile.write(body_bytes)

                log.record(method, path, matched=True, rule_id=rule.rule_id)
            else:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                log.record(method, path, matched=False, rule_id=None)

        def do_GET(self) -> None:  # noqa: N802
            self._handle()

        def do_POST(self) -> None:  # noqa: N802
            self._handle()

        def do_PUT(self) -> None:  # noqa: N802
            self._handle()

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle()

        def do_PATCH(self) -> None:  # noqa: N802
            self._handle()

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle()

    return DataHandler


class MockServer:
    """双端口本地 HTTP mock 服务 / Dual-port local HTTP mock server.

    ctrl_port: 控制面(规则管理、状态查询),只绑 127.0.0.1。
    data_port: 数据面(App 请求打桩),绑 127.0.0.1,设备经 adb reverse 访问。
    """

    def __init__(
        self,
        store: RuleStore,
        log: RequestLog,
        *,
        ctrl_port: int = 0,
        data_port: int = 0,
    ) -> None:
        self._store = store
        self._log = log
        self._ctrl_port_req = ctrl_port
        self._data_port_req = data_port

        self._ctrl_server: ThreadingHTTPServer | None = None
        self._data_server: ThreadingHTTPServer | None = None
        self._ctrl_thread: threading.Thread | None = None
        self._data_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> dict:
        """启动两个 ThreadingHTTPServer,各自在守护线程里 serve_forever。"""
        with self._lock:
            if self._running:
                return {
                    "ok": True,
                    "ctrl_port": self.ctrl_port,
                    "data_port": self.data_port,
                }

            ctrl_handler = _make_ctrl_handler(self._store, self._log, self)
            data_handler = _make_data_handler(self._store, self._log)

            self._ctrl_server = ThreadingHTTPServer(
                ("127.0.0.1", self._ctrl_port_req), ctrl_handler
            )
            self._ctrl_server.request_queue_size = 128
            self._data_server = ThreadingHTTPServer(
                ("127.0.0.1", self._data_port_req), data_handler
            )
            self._data_server.request_queue_size = 128

            self._ctrl_thread = threading.Thread(
                target=self._ctrl_server.serve_forever, daemon=True
            )
            self._data_thread = threading.Thread(
                target=self._data_server.serve_forever, daemon=True
            )
            self._ctrl_thread.start()
            self._data_thread.start()
            self._running = True

        return {
            "ok": True,
            "ctrl_port": self.ctrl_port,
            "data_port": self.data_port,
        }

    def stop(self) -> dict:
        """幂等关闭两个 server,join 线程。重复调用不抛异常。"""
        with self._lock:
            if not self._running:
                return {"ok": True}
            self._running = False
            ctrl = self._ctrl_server
            data = self._data_server
            ct = self._ctrl_thread
            dt = self._data_thread
            self._ctrl_server = None
            self._data_server = None
            self._ctrl_thread = None
            self._data_thread = None

        if ctrl is not None:
            ctrl.shutdown()
            ctrl.server_close()
        if data is not None:
            data.shutdown()
            data.server_close()
        if ct is not None:
            ct.join(timeout=5.0)
        if dt is not None:
            dt.join(timeout=5.0)

        return {"ok": True}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def ctrl_port(self) -> int:
        """返回控制面实际绑定端口(start 后有效)。"""
        if self._ctrl_server is not None:
            return self._ctrl_server.server_address[1]
        return self._ctrl_port_req

    @property
    def data_port(self) -> int:
        """返回数据面实际绑定端口(start 后有效)。"""
        if self._data_server is not None:
            return self._data_server.server_address[1]
        return self._data_port_req
