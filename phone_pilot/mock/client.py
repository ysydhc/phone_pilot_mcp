"""MockControlClient:控制面 HTTP 客户端。

HTTP client for the mock server control plane (rule management & status queries).
Only uses stdlib urllib — no extra dependencies.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class MockControlClient:
    """控制面客户端,通过 HTTP JSON API 读写 MockServer 规则与状态。

    Control plane client that communicates with MockServer over HTTP JSON API.
    All methods return parsed JSON dicts; network errors are caught and returned
    as ``{"ok": False, "error": <str>}`` — never raised.
    """

    def __init__(self, ctrl_port: int, host: str = "127.0.0.1") -> None:
        self._base = f"http://{host}:{ctrl_port}"

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行一次 HTTP 请求,统一捕获错误。"""
        url = self._base + path
        body: bytes | None = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # 非 2xx:尝试解析 server 返回的 JSON 错误体
            try:
                return json.loads(exc.read())
            except (json.JSONDecodeError, ValueError):
                return {"ok": False, "error": f"HTTP {exc.code}: {exc.reason}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_rule(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | str | None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        delay_ms: int = 0,
    ) -> dict[str, Any]:
        """POST /rules — 添加或覆盖一条匹配规则。"""
        payload: dict[str, Any] = {
            "method": method,
            "path": path,
            "body": body,
            "status": status,
            "headers": headers or {},
            "delay_ms": delay_ms,
        }
        return self._request("POST", "/rules", payload)

    def load_seed(self, seed: dict[str, Any]) -> dict[str, Any]:
        """POST /rules/bulk — 批量加载规则(原子操作)。"""
        return self._request("POST", "/rules/bulk", seed)

    def get_rules(self) -> dict[str, Any]:
        """GET /rules — 返回所有规则列表。"""
        return self._request("GET", "/rules")

    def delete_rule(self, rule_id: str) -> dict[str, Any]:
        """DELETE /rules/{id} — 删除指定规则;rule_id 含特殊字符时自动 URL 编码。"""
        # safe=":/" — 服务端用 self.path 原始字符串做 dict 查找,不解码 %xx,
        # 所以冒号和斜杠必须保持字面量传输,否则 rule_id 形如 GET:/path 会查不到。
        encoded_id = urllib.parse.quote(rule_id, safe=":/")
        return self._request("DELETE", f"/rules/{encoded_id}")

    def get_requests(self) -> dict[str, Any]:
        """GET /requests — 返回请求日志与统计。"""
        return self._request("GET", "/requests")

    def status(self) -> dict[str, Any]:
        """GET /status — 返回 server 运行状态与计数。"""
        return self._request("GET", "/status")
