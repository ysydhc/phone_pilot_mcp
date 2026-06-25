"""script_api mock 面 / Mock server script API surface.

将底层 MockServer/MockControlClient/ReverseManager 封装成脚本友好的 API，
并接入 ScriptContext 生命周期（start/stop 自动管理 adb reverse 隧道）。

Wraps MockServer/MockControlClient/ReverseManager into a script-friendly API
that integrates with ScriptContext lifecycle (adb reverse tunnel auto-managed).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from phone_pilot.mock import MockControlClient, MockServer, RequestLog, RuleStore
from phone_pilot.mock.reverse import ReverseManager

from .context import ScriptContext


# ---------------------------------------------------------------------------
# Internal handle
# ---------------------------------------------------------------------------


@dataclass
class _MockHandle:
    """启动后持有 server/client/reverse 的内部句柄。"""

    server: MockServer
    store: RuleStore
    log: RequestLog
    client: MockControlClient
    reverse: ReverseManager
    ctrl_port: int
    data_port: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def mock_server_start(
    ctx: ScriptContext,
    *,
    data_port: int = 0,
    ctrl_port: int = 0,
    seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """启动本地 HTTP mock server 并建立 adb reverse 隧道。

    Start local HTTP mock server and establish adb reverse tunnel.
    Stores handle on ctx._mock_server; subsequent calls via mock_set_rule use it.

    Parameters
    ----------
    ctx:        脚本上下文 / Script execution context
    data_port:  数据面端口，0=自动分配 / Data-plane port, 0=auto-assign
    ctrl_port:  控制面端口，0=自动分配 / Control-plane port, 0=auto-assign
    seed:       初始规则集（符合 RuleStore.load_seed 格式）/ Initial rule seed dict

    Returns
    -------
    dict
        ``{"ok": True, "ctrl_port": int, "data_port": int}`` on success,
        ``{"ok": False, "error": str}`` on failure.
    """
    try:
        if ctx._mock_server is not None:
            h: _MockHandle = ctx._mock_server
            return {"ok": True, "ctrl_port": h.ctrl_port, "data_port": h.data_port}

        store = RuleStore()
        log = RequestLog()
        server = MockServer(store, log, ctrl_port=ctrl_port, data_port=data_port)

        start_result = server.start()
        if not start_result.get("ok"):
            return {
                "ok": False,
                "error": start_result.get("error", "server_start_failed"),
            }

        actual_data_port: int = server.data_port
        actual_ctrl_port: int = server.ctrl_port

        reverse = ReverseManager(
            ctx.device_serial or "", platform=getattr(ctx, "platform", "android")
        )
        rev_result = reverse.setup(actual_data_port)
        if not rev_result.get("ok"):
            server.stop()
            return {
                "ok": False,
                "error": f"adb_reverse_failed: {rev_result.get('error', '')}",
            }

        client = MockControlClient(actual_ctrl_port)

        if seed is not None:
            try:
                store.load_seed(seed)
            except Exception as exc:
                server.stop()
                return {"ok": False, "error": f"seed_load_failed: {exc}"}

        ctx._mock_server = _MockHandle(
            server=server,
            store=store,
            log=log,
            client=client,
            reverse=reverse,
            ctrl_port=actual_ctrl_port,
            data_port=actual_data_port,
        )
        return {
            "ok": True,
            "ctrl_port": actual_ctrl_port,
            "data_port": actual_data_port,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def mock_server_stop(ctx: ScriptContext) -> dict[str, Any]:
    """停止 mock server 并拆除 adb reverse 隧道。幂等，不抛异常。

    Stop mock server and tear down adb reverse tunnel.
    Idempotent — safe to call when not started.

    Returns
    -------
    dict
        ``{"ok": True}`` or ``{"ok": False, "error": str}``.
    """
    try:
        handle: _MockHandle | None = getattr(ctx, "_mock_server", None)
        if handle is None:
            return {"ok": True}

        try:
            handle.reverse.teardown(handle.data_port)
        except Exception:
            pass

        try:
            handle.server.stop()
        except Exception:
            pass

        ctx._mock_server = None
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def mock_set_rule(
    ctx: ScriptContext,
    method: str,
    path: str,
    body: dict[str, Any] | str | None,
    *,
    status: int = 200,
) -> dict[str, Any]:
    """添加或覆盖一条 mock 规则（经控制面客户端）。

    Add or overwrite a mock rule via the control-plane client.

    Returns
    -------
    dict
        ``{"ok": True, "rule_id": str}`` or ``{"ok": False, "error": str}``.
    """
    try:
        handle: _MockHandle | None = getattr(ctx, "_mock_server", None)
        if handle is None:
            return {"ok": False, "error": "mock_server_not_running"}
        return handle.client.set_rule(method, path, body, status=status)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def mock_load_seed(ctx: ScriptContext, seed: dict[str, Any]) -> dict[str, Any]:
    """批量加载规则集（经控制面客户端）。

    Bulk-load a rule seed dict via the control-plane client.

    Returns
    -------
    dict
        ``{"ok": True, "count": int}`` or ``{"ok": False, "error": str}``.
    """
    try:
        handle: _MockHandle | None = getattr(ctx, "_mock_server", None)
        if handle is None:
            return {"ok": False, "error": "mock_server_not_running"}
        return handle.client.load_seed(seed)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def mock_assert_switched(
    ctx: ScriptContext,
    *,
    min_hits: int = 1,
    timeout_s: float = 10,
) -> dict[str, Any]:
    """轮询 mock server，断言 App 已将网络请求切换到 mock（收到 >= min_hits 个请求）。

    Poll mock server status until received_count >= min_hits or timeout.
    Returns diagnostic note on timeout to help debug network configuration issues.

    ADR-6 诊断角例：App baseURL 未指向 mock 或 debug 包未允许明文流量时给出提示。

    Parameters
    ----------
    ctx:        脚本上下文 / Script execution context
    min_hits:   最小命中次数阈值 / Minimum received request count
    timeout_s:  超时秒数 / Timeout in seconds

    Returns
    -------
    dict
        ``{"ok": True, "received_count": int}`` on success,
        ``{"ok": False, "error": "app_not_switched", "received_count": int, "note": str}``
        on timeout.
    """
    try:
        handle: _MockHandle | None = getattr(ctx, "_mock_server", None)
        if handle is None:
            return {"ok": False, "error": "mock_server_not_running"}

        deadline = time.monotonic() + max(0.0, float(timeout_s))
        poll_interval = 0.2

        while True:
            status = handle.client.status()
            received = int(status.get("received_count", 0)) if status.get("ok") else 0
            if received >= min_hits:
                return {"ok": True, "received_count": received}

            if time.monotonic() >= deadline:
                return {
                    "ok": False,
                    "error": "app_not_switched",
                    "received_count": received,
                    "note": (
                        "检查 App baseURL 是否指向 mock、"
                        "debug 包是否允许明文(cleartext)流量"
                    ),
                }
            time.sleep(poll_interval)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
