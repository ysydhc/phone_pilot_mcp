"""AppExplorer — 启发式自动化应用页面探索与脑图拓扑建图引擎。

提供一键探索（One-Click App Exploration）能力：
1. 自动抓取当前或启动目标应用，以当前主页作为根节点。
2. 启发式识别可交互组件（Tab、按钮、列表项），自动过滤破坏性敏感动作（如退出/删除/支付等）。
3. 动态触发点击转移并捕捉前后页面状态（Activity/VC、关键标题与元素），自动建立 UIKnowledgeGraph。
4. 结合拓扑回溯（Back 键或定向自愈导航）支持多分支页面深度遍历。
5. 一键输出标准化页面知识图谱、Mermaid 有向图与交互式脑图数据结构。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Union

from phone_pilot.core.log import _log
from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph, UINode
from phone_pilot.core.ui.navigator import UINavigator
from phone_pilot.core.ui.page import PageSignature


DEFAULT_DESTRUCTIVE_BLACKLIST: tuple[str, ...] = (
    "退出",
    "注销",
    "登出",
    "解绑",
    "删除",
    "清空",
    "清除数据",
    "卸载",
    "销户",
    "支付",
    "充值",
    "购买",
    "立即付款",
    "格式化",
    "重置",
    "Logout",
    "Log out",
    "Sign out",
    "Delete",
    "Clear",
    "Uninstall",
    "Pay",
    "Purchase",
    "Reset",
    "Format",
)


@dataclass
class ExploreStep:
    from_node_id: str
    action: dict[str, Any]
    to_node_id: str
    is_new_page: bool
    timestamp: float = field(default_factory=time.time)


class AppExplorer:
    """启发式自动化应用页面探索引擎。"""

    def __init__(
        self,
        driver: Any = None,
        graph: Optional[UIKnowledgeGraph] = None,
        *,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        action_executor: Callable[[dict[str, Any]], bool] | None = None,
        back_executor: Callable[[], bool] | None = None,
        destructive_blacklist: Sequence[str] | None = None,
    ) -> None:
        self.driver = driver
        self.graph = graph if graph is not None else UIKnowledgeGraph()
        self.state_provider = state_provider
        self.action_executor = action_executor
        self.back_executor = back_executor
        self.blacklist = tuple(destructive_blacklist) if destructive_blacklist is not None else DEFAULT_DESTRUCTIVE_BLACKLIST
        self.history: list[ExploreStep] = []

    def get_current_state(self) -> dict[str, Any]:
        """获取当前页面状态字典。"""
        if self.state_provider is not None:
            res = self.state_provider()
            return res if isinstance(res, dict) else {}

        if self.driver is not None and hasattr(self.driver, "get_page_state") and callable(self.driver.get_page_state):
            res = self.driver.get_page_state()
            return res if isinstance(res, dict) else {}

        if self.driver is not None and hasattr(self.driver, "ui"):
            try:
                from phone_pilot.mcp._page_state_tools import _collect_page_elements

                act_info = self.driver.ui.get_current_activity()
                try:
                    w, h = self.driver.screen.get_screen_size()
                except Exception:
                    w, h = 0, 0
                elements, scrollables, all_texts = _collect_page_elements(
                    self.driver, include_invisible=False
                )
                return {
                    "ok": True,
                    "activity": act_info,
                    "screen": {"width": w, "height": h},
                    "elements": elements,
                    "element_count": len(elements),
                    "scrollable_areas": scrollables,
                    "all_texts": all_texts,
                }
            except Exception as exc:
                _log(f"[AppExplorer] collect page state failed: {exc}")

        return {"ok": False, "elements": []}

    def execute_action(self, action: dict[str, Any]) -> bool:
        """执行单一动作。"""
        if self.action_executor is not None:
            return bool(self.action_executor(action))

        action_type = str(action.get("action_type") or action.get("type") or "tap")
        bounds = action.get("bounds") or action.get("trigger_criteria", {}).get("bounds")
        
        if action_type == "tap":
            x = action.get("x")
            y = action.get("y")
            if x is None or y is None:
                if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
                    x = (bounds[0] + bounds[2]) // 2
                    y = (bounds[1] + bounds[3]) // 2
            if x is not None and y is not None and self.driver is not None and hasattr(self.driver, "input"):
                try:
                    self.driver.input.tap(int(x), int(y))
                    return True
                except Exception as exc:
                    _log(f"[AppExplorer] tap ({x}, {y}) failed: {exc}")
                    return False

        return False

    def execute_back(self) -> bool:
        """执行返回上一级页面动作。"""
        if self.back_executor is not None:
            return bool(self.back_executor())

        if self.driver is not None and hasattr(self.driver, "go_back"):
            try:
                self.driver.go_back()
                return True
            except Exception as exc:
                _log(f"[AppExplorer] go_back failed: {exc}")
                return False
        return False

    def _is_destructive(self, text: str) -> bool:
        """检查文本是否命中破坏性动作黑名单。"""
        clean = text.strip().lower()
        if not clean:
            return False
        for bl in self.blacklist:
            if bl.lower() in clean:
                return True
        return False

    def filter_interactive_candidates(
        self, state: dict[str, Any], max_per_page: int = 5
    ) -> list[dict[str, Any]]:
        """从页面状态中筛选可作为探索转移触发器的候选元素。"""
        raw_elements = state.get("elements") or []
        candidates: list[dict[str, Any]] = []
        seen_identifiers: set[str] = set()

        for el in raw_elements:
            if not isinstance(el, dict):
                continue

            # 提取可辨识文本与标识
            text = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
            res_id = str(el.get("resource_id") or el.get("identifier") or "").strip()

            if not text and not res_id:
                continue

            if self._is_destructive(text) or self._is_destructive(res_id):
                continue

            # 排除超长文本（通常是正文/文章而不是按钮）
            if len(text) > 20:
                continue

            # 去重识别符
            ident = f"{text}|{res_id}"
            if ident in seen_identifiers:
                continue
            seen_identifiers.add(ident)

            # 构造触发动作
            bounds = el.get("bounds")
            candidates.append({
                "action_type": "tap",
                "text": text,
                "resource_id": res_id,
                "bounds": bounds,
                "trigger_criteria": {
                    "text": text,
                    "resource_id": res_id,
                    "bounds": bounds,
                },
            })

            if len(candidates) >= max_per_page:
                break

        return candidates

    def explore(
        self,
        max_depth: int = 3,
        max_pages: int = 15,
        max_branches_per_page: int = 4,
        step_delay_s: float = 0.8,
        timeout_s: float = 120.0,
    ) -> dict[str, Any]:
        """对当前应用开展启发式 BFS 自动漫游并构建知识图谱与脑图。"""
        start_time = time.time()
        self.history.clear()

        init_state = self.get_current_state()
        if not init_state or not init_state.get("ok", True):
            return {
                "ok": False,
                "error": "failed_to_get_initial_page_state",
                "nodes_count": len(self.graph.nodes),
                "edges_count": len(self.graph.edges),
            }

        root_node = self.graph._resolve_or_infer_node(init_state)
        visited_nodes: set[str] = {root_node.node_id}

        nav_action_executor = None
        if self.action_executor is not None:
            def _nav_exec(edge: UIEdge, state: dict[str, Any]) -> dict[str, Any]:
                crit = edge.trigger_criteria or {}
                act_dict = {
                    "action_type": edge.action_type,
                    "trigger_criteria": crit,
                    **crit,
                }
                ok = self.execute_action(act_dict)
                return {"ok": ok}
            nav_action_executor = _nav_exec

        def _recover_to_node(target_id: str, path: list[UIEdge]) -> bool:
            curr_st = self.get_current_state()
            c_node = self.graph.match_node(curr_st)
            if c_node is not None and c_node.node_id == target_id:
                return True

            nav = UINavigator(
                self.graph,
                driver=self.driver,
                state_provider=self.state_provider,
                action_executor=nav_action_executor,
            )
            nav_res = nav.navigate_to(target_id, current_state=curr_st, max_hops=4)
            if nav_res.get("ok"):
                return True

            # 启发式重放 path_edges
            for edge in path:
                crit = edge.trigger_criteria or {}
                self.execute_action({
                    "action_type": edge.action_type,
                    "trigger_criteria": crit,
                    **crit,
                })
                if step_delay_s > 0:
                    time.sleep(step_delay_s)
            curr_st = self.get_current_state()
            c_node = self.graph.match_node(curr_st)
            return bool(c_node is not None and c_node.node_id == target_id)

        # 队列元素: (node_id, depth, path_from_root)
        queue: deque[tuple[str, int, list[UIEdge]]] = deque([(root_node.node_id, 0, [])])
        explored_transitions: set[tuple[str, str, str]] = set()

        while queue and (time.time() - start_time) < timeout_s and len(self.graph.nodes) < max_pages:
            current_node_id, depth, path_edges = queue.popleft()

            if depth >= max_depth:
                continue

            # 若当前端侧页面不是 current_node_id，尝试恢复
            if not _recover_to_node(current_node_id, path_edges):
                continue
            curr_state = self.get_current_state()

            candidates = self.filter_interactive_candidates(curr_state, max_per_page=max_branches_per_page)

            for cand in candidates:
                if len(self.graph.nodes) >= max_pages or (time.time() - start_time) >= timeout_s:
                    break

                cand_key = (current_node_id, str(cand.get("text")), str(cand.get("resource_id")))
                if cand_key in explored_transitions:
                    continue
                explored_transitions.add(cand_key)

                # 执行动作
                act_ok = self.execute_action(cand)
                if not act_ok:
                    continue

                if step_delay_s > 0:
                    time.sleep(step_delay_s)

                next_state = self.get_current_state()
                next_node = self.graph._resolve_or_infer_node(next_state)

                is_new = next_node.node_id not in self.graph.nodes or next_node.node_id not in visited_nodes
                # 记录转移边
                src, edge, dst = self.graph.record_transition(
                    curr_state, cand, next_state, from_node_id=current_node_id, to_node_id=next_node.node_id, success=True
                )

                self.history.append(
                    ExploreStep(
                        from_node_id=current_node_id,
                        action=cand,
                        to_node_id=next_node.node_id,
                        is_new_page=is_new,
                    )
                )

                # 若跳转到了不同页面，将新页面入队并尝试返回
                if next_node.node_id != current_node_id:
                    if next_node.node_id not in visited_nodes and depth + 1 < max_depth:
                        visited_nodes.add(next_node.node_id)
                        queue.append((next_node.node_id, depth + 1, path_edges + [edge]))

                    # 尝试返回父页面以继续测试其他候选按钮
                    self.execute_back()
                    if step_delay_s > 0:
                        time.sleep(step_delay_s)

                    back_state = self.get_current_state()
                    back_node = self.graph.match_node(back_state)
                    # 记录逆向回退边
                    if back_node is not None:
                        self.graph.record_transition(
                            next_state,
                            {"action_type": "back", "trigger_criteria": {"key": "BACK"}},
                            back_state,
                            from_node_id=next_node.node_id,
                            to_node_id=back_node.node_id,
                            success=True,
                        )
                    # 如果回退后没有回到 current_node_id，使用导航器复位
                    if back_node is None or back_node.node_id != current_node_id:
                        nav = UINavigator(self.graph, driver=self.driver, state_provider=self.state_provider)
                        nav.navigate_to(current_node_id, current_state=back_state, max_hops=3)

        elapsed = round(time.time() - start_time, 2)
        mermaid_code = self.graph.to_mermaid(root_node_id=root_node.node_id)
        mindmap_data = self.graph.to_mindmap_dict(root_node_id=root_node.node_id)

        return {
            "ok": True,
            "root_node_id": root_node.node_id,
            "nodes_count": len(self.graph.nodes),
            "edges_count": len(self.graph.edges),
            "explored_steps": len(self.history),
            "elapsed_seconds": elapsed,
            "mermaid": mermaid_code,
            "mindmap": mindmap_data,
            "graph": self.graph.export_graph_json(),
        }
