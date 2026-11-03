"""Evolution MCP tools: binary freshness gate, semantic UI projection,
cognitive graph navigation, and ad format verification.

演进架构 MCP 工具集：二进制构建新鲜度校验、UI 语义投影降噪、认知图谱拓扑寻路与自愈、广告格式状态机断言。
"""

from __future__ import annotations

from phone_pilot.core.guard.freshness_checker import check_freshness
from phone_pilot.core.log import _log
from phone_pilot.core.ui.explorer import AppExplorer
from phone_pilot.core.ui.graph import UIKnowledgeGraph
from phone_pilot.core.ui.navigator import UINavigator
from phone_pilot.core.ui.projection import project_page_state
from phone_pilot.mcp._helpers import _resolve_device_serial, get_driver
from phone_pilot.mcp._page_state_tools import phone_get_page_state
from phone_pilot.mcp.server import mcp

__all__ = [
    "phone_check_freshness",
    "phone_explore_app",
    "phone_generate_dashboard",
    "phone_get_focused_state",
    "phone_load_ui_graph",
    "phone_navigate_to",
    "phone_record_transition",
    "phone_save_ui_graph",
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


@mcp.tool()
async def phone_explore_app(
    package_or_bundle: str = "",
    max_depth: int = 3,
    max_pages: int = 15,
    max_branches_per_page: int = 4,
    save_path: str = "",
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """一键启发式探索指定应用或当前前台应用，自动遍历页面并构建生成页面知识图谱与脑图（Mindmap/Mermaid/JSON）。
    One-click heuristic exploration of an app: roam clickable paths, build UIKnowledgeGraph, and generate Mermaid/Mindmap.
    """
    try:
        driver = None
        resolved_serial, resolved_platform, err = _resolve_device_serial(
            device_serial or None, platform
        )
        if not err and resolved_serial:
            driver = get_driver(resolved_serial, resolved_platform or "android")

        if driver is None:
            return {"ok": False, "error": "device_driver_unavailable"}

        if package_or_bundle and hasattr(driver, "app"):
            try:
                driver.app.start(package_or_bundle)
            except Exception as exc:
                _log(f"[phone_explore_app] start app {package_or_bundle} failed: {exc}")

        explorer = AppExplorer(driver=driver)
        result = explorer.explore(
            max_depth=max_depth,
            max_pages=max_pages,
            max_branches_per_page=max_branches_per_page,
        )

        if save_path and result.get("ok"):
            explorer.graph.export_graph_json(save_path)
            result["saved_to"] = save_path

        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "reason": "exception"}


@mcp.tool()
async def phone_record_transition(
    action: dict,
    from_state: dict | None = None,
    to_state: dict | None = None,
    graph_path: str = "",
    from_node_id: str = "",
    to_node_id: str = "",
    success: bool = True,
    device_serial: str = "",
    platform: str = "auto",
) -> dict:
    """记录一次从源状态经动作转移到目标状态的图谱边并持久化。
    Record a directed UI state transition edge with action criteria and outcome into UIKnowledgeGraph.
    """
    try:
        import pathlib

        ui_graph: UIKnowledgeGraph
        if graph_path and pathlib.Path(graph_path).expanduser().exists():
            ui_graph = UIKnowledgeGraph.load_graph_json(graph_path)
        else:
            ui_graph = UIKnowledgeGraph()

        driver = None
        if device_serial or (from_state is None and to_state is None):
            try:
                resolved_serial, resolved_platform, err = _resolve_device_serial(
                    device_serial or None, platform
                )
                if not err and resolved_serial:
                    driver = get_driver(resolved_serial, resolved_platform or "android")
            except Exception as exc:
                _log(f"[phone_record_transition] driver resolve skipped: {exc}")

        src_st = from_state
        if src_st is None and driver is not None and hasattr(driver, "get_page_state"):
            src_st = driver.get_page_state()
        if not src_st:
            src_st = {"node_id": from_node_id or "source_page"}

        dst_st = to_state or {"node_id": to_node_id or "target_page"}

        src, edge, dst = ui_graph.record_transition(
            src_st,
            action,
            dst_st,
            from_node_id=from_node_id or None,
            to_node_id=to_node_id or None,
            success=success,
        )

        if graph_path:
            ui_graph.export_graph_json(graph_path)

        return {
            "ok": True,
            "source_id": src.node_id,
            "target_id": dst.node_id,
            "action_type": edge.action_type,
            "success_rate": edge.success_rate,
            "total_nodes": len(ui_graph.nodes),
            "total_edges": len(ui_graph.edges),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "reason": "exception"}


@mcp.tool()
async def phone_save_ui_graph(
    path: str,
    graph_data: dict | None = None,
) -> dict:
    """保存或校验页面认知知识图谱到指定 JSON 文件路径。
    Export UIKnowledgeGraph to a local JSON file path.
    """
    if not path or not path.strip():
        return {"ok": False, "error": "path is required"}
    try:
        import pathlib

        if graph_data:
            ui_graph = UIKnowledgeGraph.load_graph_json(graph_data)
        elif pathlib.Path(path).expanduser().exists():
            ui_graph = UIKnowledgeGraph.load_graph_json(path)
        else:
            ui_graph = UIKnowledgeGraph()
        payload = ui_graph.export_graph_json(path)
        return {
            "ok": True,
            "path": path,
            "nodes_count": len(ui_graph.nodes),
            "edges_count": len(ui_graph.edges),
            "mermaid": ui_graph.to_mermaid(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_load_ui_graph(
    path: str = "",
    graph_data: dict | None = None,
) -> dict:
    """加载本地知识图谱文件，并导出 Mermaid 结构与脑图数据字典。
    Load UIKnowledgeGraph from file or dict, returning Mermaid markup and mindmap tree structure.
    """
    try:
        if graph_data:
            ui_graph = UIKnowledgeGraph.load_graph_json(graph_data)
        elif path:
            ui_graph = UIKnowledgeGraph.load_graph_json(path)
        else:
            return {"ok": False, "error": "either path or graph_data must be provided"}

        return {
            "ok": True,
            "nodes_count": len(ui_graph.nodes),
            "edges_count": len(ui_graph.edges),
            "mermaid": ui_graph.to_mermaid(),
            "mindmap": ui_graph.to_mindmap_dict(),
            "graph": ui_graph.export_graph_json(),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
async def phone_generate_dashboard(
    output_path: str = ".recordings/pp_dashboard.html",
    telemetry_path: str = "",
    graph_path: str = "",
) -> dict:
    """生成端侧执行质量打分与可视化数据交互看板（HTML），联动展示三端表现、工具健康度与页面脑图。
    Generate interactive HTML dashboard for tool execution quality scoring, cross-platform metrics, and UI mindmaps.
    """
    try:
        from phone_pilot.core.telemetry.dashboard import DashboardGenerator

        gen = DashboardGenerator(telemetry_path=telemetry_path or None)
        out_file = gen.generate_html(
            output_path=output_path,
            graph_path=graph_path or None,
        )
        metrics = gen.engine.aggregate_metrics()
        return {
            "ok": True,
            "dashboard_path": out_file,
            "total_calls": metrics.get("total_calls", 0),
            "average_score": metrics.get("average_score", 0.0),
            "grade": metrics.get("grade", "N/A"),
            "success_rate": metrics.get("success_rate", 0.0),
        }
    except Exception as e:
        return {"ok": False, "error": str(e), "reason": "exception"}


