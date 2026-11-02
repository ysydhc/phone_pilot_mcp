"""Evolution MCP tools: binary freshness gate, semantic UI projection,
cognitive graph navigation, and ad format verification.

演进架构 MCP 工具集：二进制构建新鲜度校验、UI 语义投影降噪、认知图谱拓扑寻路与自愈、广告格式状态机断言。
"""

from __future__ import annotations

from phone_pilot.core.guard.freshness_checker import check_freshness
from phone_pilot.core.log import _log
from phone_pilot.core.ui.graph import UIKnowledgeGraph
from phone_pilot.core.ui.navigator import UINavigator
from phone_pilot.core.ui.projection import project_page_state
from phone_pilot.mcp._helpers import _resolve_device_serial, get_driver
from phone_pilot.mcp._page_state_tools import phone_get_page_state
from phone_pilot.mcp.server import mcp

__all__ = [
    "phone_check_freshness",
    "phone_get_focused_state",
    "phone_navigate_to",
    "phone_verify_ad_format",
]


@mcp.tool()
async def phone_check_freshness(
    source_dir: str = "",
    binary_path: str = "",
    package_name: str = "",
    pid_mtime: float | None = None,
    install_mtime: float | None = None,
    pid: int = 0,
    symbol_name: str = "",
    tolerance_s: float = 0.0,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """校验构建产物新鲜度与运行中进程一致性（Pre-E2E Binary Freshness Gate）。
    Verify build artifact freshness against source mtime, running process PID timestamp, and optional symbols.
    """
    driver = None
    if device_serial or package_name:
        try:
            resolved_serial, resolved_platform, err = _resolve_device_serial(
                device_serial or None, platform
            )
            if not err and resolved_serial:
                driver = get_driver(resolved_serial, resolved_platform or "android")
        except Exception as exc:
            _log(f"[phone_check_freshness] driver resolution skipped: {exc}")

    try:
        report = check_freshness(
            source_dir=source_dir if source_dir else None,
            binary_path=binary_path if binary_path else None,
            package_name=package_name,
            pid_mtime=pid_mtime,
            install_mtime=install_mtime,
            pid=pid,
            symbol_name=symbol_name if symbol_name else None,
            driver=driver,
            tolerance_s=tolerance_s,
        )
        res = report.to_dict()
        if not report.ok:
            res["error"] = report.reason or "freshness_check_failed"
        return res
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "reason": "exception",
        }


@mcp.tool()
async def phone_get_focused_state(
    focus: str = "",
    max_elements: int = 25,
    include_neighbors: int = 1,
    page_state: dict | None = None,
    device_serial: str = "",
    platform: str = "auto",
    include_invisible: bool = False,
) -> dict:
    """获取聚焦裁剪与语义压缩后的页面状态快照（大幅降低 LLM/Subagent 上下文 Token 开销）。
    Get semantically projected and focus-pruned page state snapshot for high-density LLM consumption.
    """
    try:
        raw_state = page_state
        if raw_state is None:
            raw_state = await phone_get_page_state(
                device_serial=device_serial,
                platform=platform,
                include_invisible=include_invisible,
            )
        if not isinstance(raw_state, dict):
            return {"ok": False, "error": "invalid_page_state"}
        if not raw_state.get("ok", True):
            if "error" not in raw_state:
                return {**raw_state, "error": str(raw_state.get("reason") or "page_state_failed")}
            return raw_state

        projected = project_page_state(
            raw_state,
            focus=focus,
            max_elements=max_elements,
            include_neighbors=include_neighbors,
            compact=True,
        )
        if "device_serial" in raw_state and "device_serial" not in projected:
            projected["device_serial"] = raw_state["device_serial"]
        if "platform" in raw_state and "platform" not in projected:
            projected["platform"] = raw_state["platform"]
        return projected
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_navigate_to(
    target_node_id: str,
    graph_path: str = "",
    graph_data: dict | None = None,
    current_state: dict | None = None,
    max_hops: int = 10,
    max_heals: int = 2,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """基于认知 UI 知识图谱执行拓扑寻路与三级自愈导航（弹窗消除 / 滚动重试 / 动态重规划）。
    Navigate to target_node_id using UIKnowledgeGraph shortest-path planning and 3-tier self-healing.
    """
    if not target_node_id or not str(target_node_id).strip():
        return {"ok": False, "error": "target_node_id is required", "reason": "invalid_target"}

    try:
        if graph_data is not None:
            ui_graph = UIKnowledgeGraph.load_graph_json(graph_data)
        elif graph_path:
            ui_graph = UIKnowledgeGraph.load_graph_json(graph_path)
        else:
            ui_graph = UIKnowledgeGraph()

        driver = None
        if device_serial or current_state is None:
            try:
                resolved_serial, resolved_platform, err = _resolve_device_serial(
                    device_serial or None, platform
                )
                if not err and resolved_serial:
                    driver = get_driver(resolved_serial, resolved_platform or "android")
            except Exception as exc:
                _log(f"[phone_navigate_to] driver resolution skipped: {exc}")

        navigator = UINavigator(ui_graph, driver=driver)
        res = navigator.navigate_to(
            target_node_id,
            current_state=current_state,
            max_hops=max_hops,
            max_heals=max_heals,
        )
        if not res.get("ok"):
            res = {**res, "error": str(res.get("reason") or "navigation_failed")}
        return res
    except Exception as e:
        return {"ok": False, "error": str(e), "reason": "exception"}


@mcp.tool()
async def phone_verify_ad_format(
    ad_format: str,
    log_lines: str | list[str] = "",
    placement_id: str = "",
    required_uts_fields: list[str] | None = None,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """校验指定广告样式（banner/interstitial/rewarded_video/splash/native）的生命周期日志序列与打点字段。
    Verify ad format lifecycle stages and required UTS fields against log lines or device logcat.
    """
    try:
        from phone_pilot.core.actions.ad_domain_actions import verify_ad_format

        lines_input = log_lines
        if not lines_input and device_serial:
            try:
                resolved_serial, resolved_platform, err = _resolve_device_serial(
                    device_serial or None, platform
                )
                if not err and resolved_serial:
                    driver = get_driver(resolved_serial, resolved_platform or "android")
                    if hasattr(driver, "read_session_log"):
                        lines_input = driver.read_session_log(
                            include_current=True, current_lines=2500
                        )
                    else:
                        try:
                            lines_input = driver.read_log(lines=2500)
                        except TypeError:
                            lines_input = driver.read_log()
            except Exception as exc:
                _log(f"[phone_verify_ad_format] driver log read skipped: {exc}")

        res = verify_ad_format(
            ad_format,
            log_lines=lines_input,
            placement_id=placement_id,
            required_uts_fields=required_uts_fields,
        )
        if not res.get("ok"):
            res = {**res, "error": str(res.get("reason") or "ad_verification_failed")}
        return res
    except Exception as e:
        return {"ok": False, "error": str(e), "reason": "exception"}
