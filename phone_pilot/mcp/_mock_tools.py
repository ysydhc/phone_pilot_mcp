"""本地网络打桩 MCP 工具:动态写规则、查命中、判断 App 是否切换到 mock。

Local network mock server MCP tools: set rules, inspect hits, assert app switched.

所有工具返回 {ok: bool, ...},绝不抛出异常。
All tools return {ok: bool, ...} and never raise exceptions.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

__all__ = [
    "phone_mock_start",
    "phone_mock_stop",
    "phone_mock_set_rule",
    "phone_mock_load_seed",
    "phone_mock_get_rules",
    "phone_mock_delete_rule",
    "phone_mock_get_requests",
    "phone_mock_status",
    "phone_mock_assert_switched",
]


import phone_pilot.mcp._helpers as _helpers_mod
from phone_pilot.mcp._state import (
    clear_mock_handle,
    get_mock_handle,
    set_mock_handle,
)
from phone_pilot.mcp.server import mcp
from phone_pilot.mock import MockControlClient, MockServer, RequestLog, RuleStore
from phone_pilot.mock.reverse import ReverseManager

# ---------------------------------------------------------------------------
# Internal sync helpers
# ---------------------------------------------------------------------------


def _mock_start_sync(
    data_port: int,
    ctrl_port: int,
    seed: dict | None,
    device_serial: str | None,
    platform: str,
) -> dict:
    """同步实现 phone_mock_start 核心逻辑。"""
    # 1. 解析设备 serial(reverse 需要)
    resolved_serial, _plat, err = _helpers_mod._resolve_device_serial(
        device_serial if device_serial and str(device_serial).strip() else None,
        platform,
    )
    if err:
        return err

    # 2. 若已有 handle,先清理
    existing = get_mock_handle()
    if existing:
        _cleanup_handle(existing)

    # 3. 创建 RuleStore / RequestLog / MockServer
    store = RuleStore()
    log = RequestLog()
    server = MockServer(store, log, ctrl_port=ctrl_port, data_port=data_port)

    try:
        start_result = server.start()
        if not start_result.get("ok"):
            return {
                "ok": False,
                "error": start_result.get("error", "server_start_failed"),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    actual_ctrl_port = server.ctrl_port
    actual_data_port = server.data_port

    # 4. 建 adb reverse 隧道
    rev = ReverseManager(resolved_serial)
    rev_result = rev.setup(actual_data_port)
    if not rev_result.get("ok"):
        server.stop()
        return {
            "ok": False,
            "error": f"reverse_setup_failed: {rev_result.get('error', 'unknown')}",
        }

    # 5. 创建控制客户端
    client = MockControlClient(actual_ctrl_port)

    # 6. 存单实例句柄
    set_mock_handle(
        {
            "server": server,
            "client": client,
            "reverse": rev,
            "serial": resolved_serial,
            "data_port": actual_data_port,
        }
    )

    # 7. 加载 seed(可选)
    if seed:
        load_result = client.load_seed(seed)
        if not load_result.get("ok"):
            return {
                "ok": False,
                "error": f"seed_load_failed: {load_result.get('error', 'unknown')}",
                "ctrl_port": actual_ctrl_port,
                "data_port": actual_data_port,
            }

    return {
        "ok": True,
        "ctrl_port": actual_ctrl_port,
        "data_port": actual_data_port,
        "serial": resolved_serial,
    }


def _mock_stop_sync() -> dict:
    """同步实现 phone_mock_stop 核心逻辑(幂等)。"""
    handle = get_mock_handle()
    if not handle:
        return {"ok": True}
    clear_mock_handle()
    _cleanup_handle(handle)
    return {"ok": True}


def _cleanup_handle(handle: dict) -> None:
    """拆除 reverse + 停止 server,异常静默吞掉。"""
    rev: ReverseManager | None = handle.get("reverse")
    server: MockServer | None = handle.get("server")
    data_port: int | None = handle.get("data_port")

    if rev is not None and data_port:
        try:
            rev.teardown(data_port)
        except Exception:
            pass

    if server is not None:
        try:
            server.stop()
        except Exception:
            pass


def _require_client() -> tuple[MockControlClient | None, dict | None]:
    """从 handle 取出 client;若未启动则返回 (None, error_dict)。"""
    handle = get_mock_handle()
    client: MockControlClient | None = handle.get("client")
    if client is None:
        return None, {"ok": False, "error": "mock_not_running"}
    return client, None


# ---------------------------------------------------------------------------
# MCP Tool implementations
# ---------------------------------------------------------------------------


@mcp.tool()
async def phone_mock_start(
    data_port: int = 0,
    ctrl_port: int = 0,
    seed: dict | None = None,
    device_serial: str | None = None,
    platform: str = "android",
) -> dict:
    """启动本地 mock server 并建立 adb reverse 隧道。
    Start local mock server and set up adb reverse tunnel.

    Parameters / 参数:
        data_port: 数据面端口(0=随机) / Data plane port (0=random)
        ctrl_port: 控制面端口(0=随机) / Control plane port (0=random)
        seed: 初始规则 dict,格式 {"rules": [{method,path,status,body},...]} / Initial seed rules
        device_serial: 设备序列号(用于建 reverse 隧道) / Device serial (for reverse tunnel)
        platform: 平台类型 / Platform type

    Returns / 返回值:
        dict: {"ok": True, "ctrl_port": int, "data_port": int, "serial": str}
              或 {"ok": False, "error": str}
    """
    return await asyncio.to_thread(
        _mock_start_sync, data_port, ctrl_port, seed, device_serial, platform
    )


@mcp.tool()
async def phone_mock_stop() -> dict:
    """停止 mock server 并拆除 adb reverse 隧道(幂等)。
    Stop mock server and remove adb reverse tunnel (idempotent).

    Returns / 返回值:
        dict: {"ok": True}
    """
    return await asyncio.to_thread(_mock_stop_sync)


@mcp.tool()
async def phone_mock_set_rule(
    method: str,
    path: str,
    body: Any = None,
    status: int = 200,
) -> dict:
    """添加或覆盖一条 mock 匹配规则。
    Add or overwrite a mock matching rule.

    Parameters / 参数:
        method: HTTP 方法 (GET/POST/PUT/...) / HTTP method
        path: URL 路径,精确匹配 / URL path, exact match
        body: 响应体,dict 序列化为 JSON,str 原样返回 / Response body
        status: HTTP 状态码 / HTTP status code

    Returns / 返回值:
        dict: {"ok": True, "rule_id": str} 或 {"ok": False, "error": str}
    """
    client, err = _require_client()
    if err:
        return err
    try:
        return await asyncio.to_thread(
            client.set_rule, method, path, body, status=status
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def phone_mock_load_seed(seed: dict) -> dict:
    """批量加载 mock 规则(原子替换或追加,取决于 seed 格式)。
    Bulk-load mock rules atomically.

    Parameters / 参数:
        seed: 规则 dict,格式 {"rules": [{method,path,status,body,headers?,delay_ms?},...]}
              Rule dict with format {"rules": [...]}

    Returns / 返回值:
        dict: {"ok": True, "count": int} 或 {"ok": False, "error": str}
    """
    client, err = _require_client()
    if err:
        return err
    try:
        return await asyncio.to_thread(client.load_seed, seed)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def phone_mock_get_rules() -> dict:
    """获取当前所有 mock 规则。
    Get all current mock rules.

    Returns / 返回值:
        dict: {"ok": True, "rules": [{method,path,status,body,headers,delay_ms,rule_id},...]}
    """
    client, err = _require_client()
    if err:
        return err
    try:
        return await asyncio.to_thread(client.get_rules)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def phone_mock_delete_rule(rule_id: str) -> dict:
    """删除指定 rule_id 的 mock 规则。
    Delete a mock rule by rule_id.

    Parameters / 参数:
        rule_id: 规则 ID(由 phone_mock_set_rule 返回) / Rule ID returned by set_rule

    Returns / 返回值:
        dict: {"ok": True, "deleted": bool} 或 {"ok": False, "error": str}
    """
    client, err = _require_client()
    if err:
        return err
    try:
        return await asyncio.to_thread(client.delete_rule, rule_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def phone_mock_get_requests() -> dict:
    """获取数据面收到的所有请求记录与统计。
    Get all requests received by the data plane with statistics.

    Returns / 返回值:
        dict: {"ok": True, "requests": [...], "received_count": int, "matched_count": int}
    """
    client, err = _require_client()
    if err:
        return err
    try:
        return await asyncio.to_thread(client.get_requests)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def phone_mock_status() -> dict:
    """查询 mock server 运行状态与请求计数。
    Query mock server running status and request counts.

    Returns / 返回值:
        dict: {"ok": True, "running": bool, "ctrl_port": int, "data_port": int,
               "rules_count": int, "received_count": int, "matched_count": int}
    """
    client, err = _require_client()
    if err:
        return err
    try:
        return await asyncio.to_thread(client.status)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool()
async def phone_mock_assert_switched(
    min_hits: int = 1,
    timeout_s: float = 10,
) -> dict:
    """轮询等待 App 发出至少 min_hits 个请求到 mock server。
    Poll until the app sends at least min_hits requests to mock server.

    用于验证 App 已切换 baseURL 指向 mock。超时后给出诊断 note。
    Verifies the app has switched its baseURL to mock. Returns diagnostic note on timeout.

    Parameters / 参数:
        min_hits: 期望的最少请求数 / Minimum expected request count
        timeout_s: 最长等待秒数 / Max wait seconds

    Returns / 返回值:
        dict: {"ok": True, "received_count": int}
              或 {"ok": False, "error": "app_not_switched", "received_count": int, "note": str}
    """
    client, err = _require_client()
    if err:
        return err

    def _poll() -> dict:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        interval = 0.5
        last_count = 0
        while True:
            try:
                st = client.status()
                last_count = st.get("received_count", 0)
                if last_count >= min_hits:
                    return {"ok": True, "received_count": last_count}
            except Exception:
                pass
            if time.monotonic() >= deadline:
                break
            time.sleep(interval)
        return {
            "ok": False,
            "error": "app_not_switched",
            "received_count": last_count,
            "note": (
                "App 未向 mock server 发送请求。请检查:\n"
                "1. App baseURL 已指向 mock(data_port)\n"
                "2. debug 包已在 network_security_config 中允许明文 cleartext 流量\n"
                "3. adb reverse 隧道已生效"
            ),
        }

    try:
        return await asyncio.to_thread(_poll)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
