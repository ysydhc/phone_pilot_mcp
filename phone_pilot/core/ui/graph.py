"""UIGraph — DAG 拓扑引擎，将多条 UIChain 拼接为有向无环图。

节点通过 ``after=`` 声明依赖，形成 DAG。图按拓扑排序顺序执行：
- 同级节点（共享同一父节点）失败不互相阻塞
- 父节点失败则子节点标记为 ``skipped``
- 支持多依赖：``g.add("final", fn, after=["a", "b"])``

用法::

    from phone_pilot.core.ui.graph import graph
    from phone_pilot.core.ui.chain import chain

    g = graph(ctx)
    g.add("setup", lambda: chain(ctx).unlock_device().done())
    g.add("navigate", lambda: chain(ctx).find_text("X").tap().done(), after="setup")
    g.add("v_ui", lambda: chain(ctx).assert_text_exists("Y").done(), after="navigate")
    g.add("v_log", lambda: chain(ctx).assert_logcat_contains("ok").done(), after="navigate")
    result = g.run()
"""

from __future__ import annotations

import hashlib
import heapq
import json
import pathlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Union

from phone_pilot.core.ui.page import PageSignature


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class GraphNode:
    """图中的一个节点。"""

    name: str
    fn: Callable[[], dict]
    after: list[str] = field(default_factory=list)
    result: Optional[dict] = None
    status: str = "pending"  # pending | ok | failed | skipped
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# UIGraph
# ---------------------------------------------------------------------------


class UIGraph:
    """有向无环图执行引擎。

    Parameters
    ----------
    ctx : Any
        ``ScriptContext`` 或其他上下文对象，仅用于传递给 ``graph()`` 工厂函数，
        不直接参与执行逻辑（节点 callable 自行捕获 ctx）。
    """

    def __init__(self, ctx: Any = None) -> None:
        self._ctx = ctx
        self._nodes: dict[str, GraphNode] = {}
        self._order: list[str] = []  # 插入顺序，用于稳定排序

    # ---- 构建 API --------------------------------------------------------

    def add(
        self,
        name: str,
        fn: Callable[[], dict],
        *,
        after: Union[str, Sequence[str], None] = None,
    ) -> "UIGraph":
        """添加一个节点。

        Parameters
        ----------
        name : str
            唯一节点名。
        fn : Callable[[], dict]
            节点执行体，无参 callable，返回 ``dict``（通常是
            ``chain(ctx)...done()`` 的 lambda）。
        after : str | list[str] | None
            依赖的节点名。``None`` 表示无依赖（根节点）。

        Raises
        ------
        ValueError
            节点名重复、依赖不存在、或形成环。
        """
        if name in self._nodes:
            raise ValueError(f"节点名重复: {name!r}")

        # 归一化 after
        deps: list[str] = []
        if after is not None:
            if isinstance(after, str):
                deps = [after]
            else:
                deps = list(after)

        # 检查依赖是否已注册
        for dep in deps:
            if dep not in self._nodes:
                raise ValueError(f"依赖节点不存在: {dep!r}（在添加 {name!r} 时）")

        node = GraphNode(name=name, fn=fn, after=deps)
        self._nodes[name] = node
        self._order.append(name)

        # 环检测
        if self._has_cycle():
            del self._nodes[name]
            self._order.remove(name)
            raise ValueError(f"添加 {name!r} 后形成环")

        return self

    # ---- 执行 API --------------------------------------------------------

    def run(self) -> dict:
        """按拓扑排序顺序执行所有节点。

        Returns
        -------
        dict
            ``{ok, nodes, passed, failed, skipped, total}``
        """
        order = self._topo_sort()
        passed = 0
        failed = 0
        skipped = 0

        for name in order:
            node = self._nodes[name]

            # 检查所有父节点是否成功
            parent_failed = False
            for dep in node.after:
                dep_node = self._nodes[dep]
                if dep_node.status in ("failed", "skipped"):
                    parent_failed = True
                    break

            if parent_failed:
                node.status = "skipped"
                node.result = {"ok": False, "skipped": True, "reason": "parent_failed"}
                skipped += 1
                continue

            # 执行节点
            t0 = time.monotonic()
            try:
                result = node.fn()
                if not isinstance(result, dict):
                    result = {"ok": bool(result)}
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": {"code": "exception", "message": str(exc)},
                }
            elapsed = (time.monotonic() - t0) * 1000

            node.result = result
            node.elapsed_ms = elapsed

            if result.get("ok", False):
                node.status = "ok"
                passed += 1
            else:
                node.status = "failed"
                failed += 1

        # 汇总
        nodes_out: dict[str, dict] = {}
        for name in order:
            n = self._nodes[name]
            nodes_out[name] = {
                "ok": n.status == "ok",
                "status": n.status,
                "elapsed_ms": round(n.elapsed_ms, 1),
                **(n.result or {}),
            }

        total = passed + failed + skipped
        return {
            "ok": failed == 0 and skipped == 0,
            "nodes": nodes_out,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total": total,
        }

    # ---- 内部方法 --------------------------------------------------------

    def _topo_sort(self) -> list[str]:
        """Kahn 算法拓扑排序，相同优先级时按插入顺序稳定排序。"""
        in_degree: dict[str, int] = {name: 0 for name in self._nodes}
        children: dict[str, list[str]] = {name: [] for name in self._nodes}

        for name, node in self._nodes.items():
            for dep in node.after:
                children[dep].append(name)
                in_degree[name] += 1

        # 初始化队列：入度为 0 的节点，按插入顺序排序
        queue: deque[str] = deque()
        for name in self._order:
            if in_degree[name] == 0:
                queue.append(name)

        result: list[str] = []
        while queue:
            name = queue.popleft()
            result.append(name)
            # 收集所有可释放的子节点，按插入顺序排序
            ready: list[str] = []
            for child in children[name]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    ready.append(child)
            # 按插入顺序稳定排序
            ready.sort(key=lambda n: self._order.index(n))
            queue.extend(ready)

        if len(result) != len(self._nodes):
            raise RuntimeError("拓扑排序失败：图中存在环")

        return result

    def _has_cycle(self) -> bool:
        """检测图中是否有环（DFS 三色标记法）。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in self._nodes}

        def dfs(name: str) -> bool:
            color[name] = GRAY
            for dep_name, dep_node in self._nodes.items():
                # 检查 dep_name 是否是 name 的子节点
                pass
            # 反向：检查 name 的子节点（即 after 包含 name 的节点）
            for other_name, other_node in self._nodes.items():
                if name in other_node.after:
                    if color[other_name] == GRAY:
                        return True
                    if color[other_name] == WHITE and dfs(other_name):
                        return True
            color[name] = BLACK
            return False

        for name in self._nodes:
            if color[name] == WHITE:
                if dfs(name):
                    return True
        return False

    # ---- 信息 API --------------------------------------------------------

    def node_names(self) -> list[str]:
        """返回所有节点名（按插入顺序）。"""
        return list(self._order)

    def __len__(self) -> int:
        return len(self._nodes)

    def __repr__(self) -> str:
        return f"UIGraph(nodes={list(self._nodes.keys())})"


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def graph(ctx: Any = None) -> UIGraph:
    """创建一个新的 UIGraph 实例。

    Parameters
    ----------
    ctx : Any
        上下文对象，通常是 ``ScriptContext``。传递给 graph 仅用于
        语义清晰，节点 callable 自行在闭包中捕获 ctx。
    """
    return UIGraph(ctx=ctx)


# ---------------------------------------------------------------------------
# Cognitive UI Knowledge Graph (UINode / UIEdge / UIKnowledgeGraph)
# ---------------------------------------------------------------------------


@dataclass
class UINode:
    """认知 UI 状态图中的页面节点。"""

    node_id: str
    page_signature: PageSignature
    activity_or_vc: str = ""
    interactive_elements: list[dict[str, Any]] = field(default_factory=list)
    last_seen_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "page_signature": {
                "name": self.page_signature.name,
                "activity_or_controller": self.page_signature.activity_or_controller,
                "required_elements": list(self.page_signature.required_elements),
                "forbidden_elements": list(self.page_signature.forbidden_elements),
            },
            "activity_or_vc": self.activity_or_vc,
            "interactive_elements": list(self.interactive_elements),
            "last_seen_timestamp": self.last_seen_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UINode":
        sig_data = data.get("page_signature") or {}
        if isinstance(sig_data, PageSignature):
            sig = sig_data
        else:
            sig = PageSignature(
                name=str(sig_data.get("name", data.get("node_id", ""))),
                activity_or_controller=str(sig_data.get("activity_or_controller", "")),
                required_elements=list(sig_data.get("required_elements", [])),
                forbidden_elements=list(sig_data.get("forbidden_elements", [])),
            )
        return cls(
            node_id=str(data["node_id"]),
            page_signature=sig,
            activity_or_vc=str(data.get("activity_or_vc", "")),
            interactive_elements=list(data.get("interactive_elements", [])),
            last_seen_timestamp=float(data.get("last_seen_timestamp", time.time())),
        )


CognitiveUINode = UINode


@dataclass
class UIEdge:
    """认知 UI 状态图中的有向转移边。"""

    source_id: str
    target_id: str
    action_type: str = "tap"  # "tap" | "swipe" | "back" | "wait" | "input"
    trigger_criteria: dict[str, Any] = field(default_factory=dict)
    success_rate: float = 1.0
    attempts: int = 1
    successes: int = 1

    def record_outcome(self, success: bool) -> float:
        self.attempts = max(1, self.attempts + 1)
        if success:
            self.successes += 1
        self.success_rate = round(self.successes / self.attempts, 4)
        return self.success_rate

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "action_type": self.action_type,
            "trigger_criteria": dict(self.trigger_criteria),
            "success_rate": self.success_rate,
            "attempts": self.attempts,
            "successes": self.successes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UIEdge":
        return cls(
            source_id=str(data["source_id"]),
            target_id=str(data["target_id"]),
            action_type=str(data.get("action_type", "tap")),
            trigger_criteria=dict(data.get("trigger_criteria", {})),
            success_rate=float(data.get("success_rate", 1.0)),
            attempts=int(data.get("attempts", 1)),
            successes=int(data.get("successes", 1)),
        )


class UIKnowledgeGraph:
    """页面认知与导航知识图谱引擎。"""

    def __init__(
        self,
        nodes: Optional[dict[str, UINode]] = None,
        edges: Optional[list[UIEdge]] = None,
    ) -> None:
        self.nodes: dict[str, UINode] = dict(nodes) if nodes else {}
        self.edges: list[UIEdge] = list(edges) if edges else []

    def add_or_update_node(self, node: UINode) -> UINode:
        """添加或更新一个页面节点。"""
        existing = self.nodes.get(node.node_id)
        if existing is not None:
            existing.page_signature = node.page_signature
            if node.activity_or_vc:
                existing.activity_or_vc = node.activity_or_vc
            if node.interactive_elements:
                existing.interactive_elements = list(node.interactive_elements)
            existing.last_seen_timestamp = node.last_seen_timestamp
            return existing
        self.nodes[node.node_id] = node
        return node

    def get_node(self, node_id: str) -> Optional[UINode]:
        """根据 node_id 获取页面节点。"""
        return self.nodes.get(node_id)

    @staticmethod
    def _extract_activity_str(page_state: dict[str, Any]) -> str:
        act = page_state.get("activity")
        if isinstance(act, dict):
            return str(act.get("activity") or act.get("class_name") or "").strip()
        if isinstance(act, str) and act.strip():
            return act.strip()
        vc = page_state.get("view_controller")
        if isinstance(vc, str) and vc.strip():
            return vc.strip()
        return ""

    def match_node(
        self, page_state: Union[dict[str, Any], str, PageSignature]
    ) -> Optional[UINode]:
        """匹配给定页面状态对应的 UINode。"""
        if isinstance(page_state, str):
            if page_state in self.nodes:
                return self.nodes[page_state]
            for node in self.nodes.values():
                if node.page_signature.name == page_state or (
                    node.activity_or_vc and node.activity_or_vc == page_state
                ):
                    return node
            return None

        if isinstance(page_state, PageSignature):
            if page_state.name and page_state.name in self.nodes:
                return self.nodes[page_state.name]
            for node in self.nodes.values():
                if page_state.name and node.page_signature.name == page_state.name:
                    return node
            if page_state.required_elements:
                req_set = set(page_state.required_elements)
                best_node: Optional[UINode] = None
                best_len = -1
                for node in self.nodes.values():
                    node_reqs = set(node.page_signature.required_elements)
                    if req_set.issubset(node_reqs) or node_reqs == req_set:
                        if len(node_reqs) > best_len:
                            best_len = len(node_reqs)
                            best_node = node
                if best_node is not None:
                    return best_node
            return None

        if isinstance(page_state, dict):
            explicit_id = page_state.get("node_id")
            if isinstance(explicit_id, str) and explicit_id in self.nodes:
                return self.nodes[explicit_id]

            state_act = self._extract_activity_str(page_state)
            candidates: list[tuple[int, int, UINode]] = []

            for node in self.nodes.values():
                sig = node.page_signature
                has_criteria = bool(
                    sig.required_elements
                    or sig.forbidden_elements
                    or sig.activity_or_controller
                    or sig.extra_match_fn
                    or node.activity_or_vc
                )
                if not has_criteria:
                    continue

                # 若 page_state["activity"] 是 dict，兼容 PageSignature.matches 的字符串检查
                normalized_state = page_state
                if isinstance(page_state.get("activity"), dict) and sig.activity_or_controller:
                    normalized_state = dict(page_state)
                    normalized_state["activity"] = state_act

                if not sig.matches(normalized_state):
                    continue

                if node.activity_or_vc and state_act and not sig.activity_or_controller:
                    if node.activity_or_vc not in state_act and state_act not in node.activity_or_vc:
                        continue

                act_bonus = 1 if (
                    (sig.activity_or_controller and state_act)
                    or (node.activity_or_vc and state_act and node.activity_or_vc in state_act)
                ) else 0
                candidates.append((len(sig.required_elements), act_bonus, node))

            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                return candidates[0][2]

        return None

    def infer_node_from_state(
        self, page_state: dict[str, Any], node_id: Optional[str] = None
    ) -> UINode:
        """从实时页面状态字典推断并生成 UINode。"""
        activity_or_vc = self._extract_activity_str(page_state)

        raw_elements = page_state.get("elements") or []
        interactive_elements: list[dict[str, Any]] = []
        extracted_labels: list[str] = []

        if isinstance(raw_elements, list):
            for el in raw_elements:
                if isinstance(el, dict):
                    interactive_elements.append(dict(el))
                    for key in ("text", "label", "content_desc", "resource_id", "identifier", "name"):
                        val = el.get(key)
                        if isinstance(val, str) and val.strip():
                            clean = val.strip()
                            if clean not in extracted_labels:
                                extracted_labels.append(clean)
                            break

        all_texts = page_state.get("all_texts") or []
        if isinstance(all_texts, list):
            for txt in all_texts:
                if isinstance(txt, str) and txt.strip():
                    clean = txt.strip()
                    if clean not in extracted_labels:
                        extracted_labels.append(clean)

        required_elements = extracted_labels[:4]

        resolved_id = (
            node_id
            or (page_state.get("node_id") if isinstance(page_state.get("node_id"), str) else None)
        )
        if not resolved_id:
            fingerprint_raw = f"{activity_or_vc}|{'|'.join(required_elements)}"
            digest = hashlib.sha1(fingerprint_raw.encode("utf-8")).hexdigest()[:8]
            if activity_or_vc:
                short_act = activity_or_vc.split(".")[-1].split("/")[-1] or "page"
                resolved_id = f"{short_act}_{digest}"
            else:
                resolved_id = f"page_{digest}"

        act_for_sig = activity_or_vc

        sig = PageSignature(
            name=str(resolved_id),
            activity_or_controller=act_for_sig,
            required_elements=required_elements,
        )
        return UINode(
            node_id=str(resolved_id),
            page_signature=sig,
            activity_or_vc=activity_or_vc,
            interactive_elements=interactive_elements,
            last_seen_timestamp=time.time(),
        )

    def _resolve_or_infer_node(
        self,
        state: Union[dict[str, Any], UINode],
        explicit_node_id: Optional[str] = None,
    ) -> UINode:
        if isinstance(state, UINode):
            if explicit_node_id:
                state.node_id = explicit_node_id
            return self.add_or_update_node(state)

        if explicit_node_id and explicit_node_id in self.nodes:
            existing = self.nodes[explicit_node_id]
            existing.last_seen_timestamp = time.time()
            return existing

        matched = self.match_node(state)
        if matched is not None and (
            explicit_node_id is None or matched.node_id == explicit_node_id
        ):
            matched.last_seen_timestamp = time.time()
            return matched

        inferred = self.infer_node_from_state(state, node_id=explicit_node_id)
        return self.add_or_update_node(inferred)

    def record_transition(
        self,
        from_state: Union[dict[str, Any], UINode],
        action: dict[str, Any],
        to_state: Union[dict[str, Any], UINode],
        *,
        from_node_id: Optional[str] = None,
        to_node_id: Optional[str] = None,
        success: bool = True,
    ) -> tuple[UINode, UIEdge, UINode]:
        """记录一次从源页面经过动作转移到目标页面的边及统计信息。"""
        src_node = self._resolve_or_infer_node(from_state, explicit_node_id=from_node_id)
        dst_node = self._resolve_or_infer_node(to_state, explicit_node_id=to_node_id)

        action_type = str(action.get("action_type") or action.get("type") or "tap")
        if "trigger_criteria" in action and isinstance(action["trigger_criteria"], dict):
            trigger_criteria = dict(action["trigger_criteria"])
        else:
            trigger_criteria = {
                k: v for k, v in action.items() if k not in ("action_type", "type")
            }

        for edge in self.edges:
            if (
                edge.source_id == src_node.node_id
                and edge.target_id == dst_node.node_id
                and edge.action_type == action_type
                and edge.trigger_criteria == trigger_criteria
            ):
                edge.record_outcome(success)
                return src_node, edge, dst_node

        init_rate = 1.0 if success else 0.0
        new_edge = UIEdge(
            source_id=src_node.node_id,
            target_id=dst_node.node_id,
            action_type=action_type,
            trigger_criteria=trigger_criteria,
            success_rate=init_rate,
            attempts=1,
            successes=1 if success else 0,
        )
        self.edges.append(new_edge)
        return src_node, new_edge, dst_node

    @staticmethod
    def _edge_cost(edge: UIEdge) -> float:
        rate = max(0.05, min(1.0, float(edge.success_rate)))
        return 1.0 - 0.5 * rate + (1.0 / rate - 1.0)

    def find_path_to(
        self,
        target: Union[PageSignature, str],
        current_state: Union[dict[str, Any], str],
    ) -> list[UIEdge]:
        """使用 Dijkstra 最短路径算法规划从当前状态到目标页面的最优动作序列。"""
        start_node = self.match_node(current_state)
        target_node = self.match_node(target)
        if start_node is None or target_node is None:
            return []
        if start_node.node_id == target_node.node_id:
            return []

        adj: dict[str, list[UIEdge]] = {}
        for edge in self.edges:
            adj.setdefault(edge.source_id, []).append(edge)

        counter = 0
        heap: list[tuple[float, int, str, list[UIEdge]]] = [
            (0.0, counter, start_node.node_id, [])
        ]
        best_cost: dict[str, float] = {start_node.node_id: 0.0}

        while heap:
            cost, _, curr_id, path = heapq.heappop(heap)
            if curr_id == target_node.node_id:
                return path
            if cost > best_cost.get(curr_id, float("inf")):
                continue

            for edge in adj.get(curr_id, []):
                next_cost = cost + self._edge_cost(edge)
                if next_cost < best_cost.get(edge.target_id, float("inf")):
                    best_cost[edge.target_id] = next_cost
                    counter += 1
                    heapq.heappush(
                        heap, (next_cost, counter, edge.target_id, path + [edge])
                    )

        return []

    def find_shortest_path(
        self,
        source: Union[dict[str, Any], str],
        target: Union[PageSignature, str],
    ) -> list[UIEdge]:
        """Convenience alias for find_path_to with (source, target) argument order."""
        return self.find_path_to(target=target, current_state=source)

    def export_graph_json(
        self, path: Optional[Union[str, pathlib.Path]] = None
    ) -> dict[str, Any]:
        """将图谱导出为可序列化的字典，若指定 path 则写入 JSON 文件。"""
        payload: dict[str, Any] = {
            "version": "1.0",
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "edges": [edge.to_dict() for edge in self.edges],
        }
        if path is not None:
            file_path = pathlib.Path(path).expanduser().resolve()
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return payload

    def to_mermaid(self, root_node_id: Optional[str] = None) -> str:
        """导出为 Mermaid 有向图 / 流程图描述文本。"""
        lines = ["graph TD"]
        if not self.nodes:
            lines.append("    EmptyGraph[\"空图谱 (无页面节点)\"]")
            return "\n".join(lines)

        # 节点定义
        for nid, node in sorted(self.nodes.items(), key=lambda x: x[0]):
            act = node.activity_or_vc.split(".")[-1].split("/")[-1] if node.activity_or_vc else ""
            label = f"{nid} ({act})" if act else nid
            safe_label = label.replace('"', "'")
            lines.append(f'    {nid}["{safe_label}"]')

        # 边定义
        for edge in self.edges:
            crit = edge.trigger_criteria or {}
            label_parts: list[str] = []
            for k in ("text", "label", "resource_id", "content_desc"):
                if k in crit and crit[k]:
                    label_parts.append(str(crit[k]))
                    break
            action_desc = f"{edge.action_type}: {label_parts[0]}" if label_parts else edge.action_type
            rate_pct = f"{int(round(edge.success_rate * 100))}%"
            edge_label = f"{action_desc} ({rate_pct})".replace('"', "'")
            lines.append(f'    {edge.source_id} -->|"{edge_label}"| {edge.target_id}')

        return "\n".join(lines)

    def to_mindmap_dict(self, root_node_id: Optional[str] = None) -> dict[str, Any]:
        """导出为脑图/树形结构的字典（可供 ECharts Tree、Markmap 或前端渲染）。"""
        if not self.nodes:
            return {"id": "root", "name": "应用页面脑图 (空)", "children": []}

        # 选取根节点
        root_id = root_node_id
        if not root_id or root_id not in self.nodes:
            # 优先寻找入度为 0 的节点或名称包含 main/home 的节点
            target_ids = {e.target_id for e in self.edges}
            candidates = [nid for nid in self.nodes if nid not in target_ids]
            if candidates:
                root_id = candidates[0]
            else:
                for nid in self.nodes:
                    if "main" in nid.lower() or "home" in nid.lower():
                        root_id = nid
                        break
                if not root_id:
                    root_id = next(iter(self.nodes.keys()))

        adj: dict[str, list[UIEdge]] = {}
        for edge in self.edges:
            adj.setdefault(edge.source_id, []).append(edge)

        visited: set[str] = set()

        def build_tree(current_id: str, depth: int = 0) -> dict[str, Any]:
            node = self.nodes.get(current_id)
            act = node.activity_or_vc.split(".")[-1].split("/")[-1] if node and node.activity_or_vc else ""
            display_name = f"{current_id} ({act})" if act else current_id
            
            tree_node: dict[str, Any] = {
                "id": current_id,
                "name": display_name,
                "activity": node.activity_or_vc if node else "",
                "interactive_count": len(node.interactive_elements) if node else 0,
                "children": [],
            }
            if current_id in visited or depth > 10:
                # 出现回路时标明循环引用，截断展开
                tree_node["is_loop"] = True
                return tree_node

            visited.add(current_id)
            for edge in adj.get(current_id, []):
                child_tree = build_tree(edge.target_id, depth + 1)
                crit = edge.trigger_criteria or {}
                crit_text = str(crit.get("text") or crit.get("label") or crit.get("resource_id") or "")
                child_tree["edge"] = {
                    "action_type": edge.action_type,
                    "target_text": crit_text,
                    "success_rate": edge.success_rate,
                }
                tree_node["children"].append(child_tree)

            return tree_node

        tree_root = build_tree(root_id)

        # 检查是否有未连通的孤立子图，挂载到全局虚拟根下（若有多分支）
        unvisited = [nid for nid in self.nodes if nid not in visited]
        if unvisited:
            return {
                "id": "__app_mindmap_root__",
                "name": "应用主导航网络",
                "children": [tree_root] + [build_tree(unid) for unid in unvisited[:5]],
            }

        return tree_root

    @classmethod
    def load_graph_json(
        cls, source: Union[str, pathlib.Path, dict[str, Any]]
    ) -> "UIKnowledgeGraph":
        """从 JSON 文件路径、JSON 字符串或字典反序列化恢复 UIKnowledgeGraph。"""
        if isinstance(source, dict):
            data = source
        elif isinstance(source, pathlib.Path):
            data = json.loads(source.expanduser().resolve().read_text(encoding="utf-8"))
        elif isinstance(source, str):
            stripped = source.strip()
            if stripped.startswith("{"):
                data = json.loads(stripped)
            else:
                file_path = pathlib.Path(source).expanduser().resolve()
                data = json.loads(file_path.read_text(encoding="utf-8"))
        else:
            raise TypeError(f"Unsupported graph source type: {type(source)!r}")

        raw_nodes = data.get("nodes") or {}
        nodes: dict[str, UINode] = {}
        if isinstance(raw_nodes, dict):
            for nid, ndata in raw_nodes.items():
                if isinstance(ndata, dict):
                    node_payload = dict(ndata)
                    node_payload.setdefault("node_id", nid)
                    node = UINode.from_dict(node_payload)
                    nodes[node.node_id] = node
        elif isinstance(raw_nodes, list):
            for ndata in raw_nodes:
                if isinstance(ndata, dict):
                    node = UINode.from_dict(ndata)
                    nodes[node.node_id] = node

        raw_edges = data.get("edges") or []
        edges: list[UIEdge] = [
            UIEdge.from_dict(edata) for edata in raw_edges if isinstance(edata, dict)
        ]
        return cls(nodes=nodes, edges=edges)

