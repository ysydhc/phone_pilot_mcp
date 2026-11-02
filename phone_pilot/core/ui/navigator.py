"""UINavigator — 基于认知 UI 知识图谱的拓扑寻路与自愈导航引擎。

提供从任意当前页面状态到目标页面（PageSignature 或 node_id）的自动寻路与执行能力，
并在发生页面偏移（Page Drift）时按三级自愈策略自动恢复：
1. Level 1 (Popup Dismissal): 自动识别并关闭遮挡弹窗（如“关闭”、“跳过”、“Allow”等）后恢复执行。
2. Level 2 (Scroll & Retry): 针对点击目标未露出的情况执行动态比例滚动并重试当前边。
3. Level 3 (Dynamic Re-pathing): 记录失败边并降低其成功率权重，基于当前漂移落点重新规划最短路径。
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Optional, Sequence, Union

from phone_pilot.core.log import _log
from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph, UINode
from phone_pilot.core.ui.page import PageSignature


DEFAULT_POPUP_DISMISS_LABELS: tuple[str, ...] = (
    "关闭",
    "跳过",
    "允许",
    "始终允许",
    "我知道了",
    "以后再说",
    "取消",
    "Close",
    "Skip",
    "Allow",
    "Dismiss",
    "Cancel",
    "Not Now",
    "OK",
    "×",
    "X",
)


class UINavigator:
    """拓扑寻路与三级自愈 UI 导航器。"""

    def __init__(
        self,
        graph: UIKnowledgeGraph,
        ctx: Any = None,
        *,
        driver: Any = None,
        state_provider: Callable[[], dict[str, Any]] | None = None,
        action_executor: Callable[[UIEdge, dict[str, Any]], dict[str, Any]] | None = None,
        popup_labels: Sequence[str] | None = None,
    ) -> None:
        self.graph = graph
        self.ctx = ctx
        self.driver = driver if driver is not None else getattr(ctx, "driver", None)
        self.state_provider = state_provider
        self.action_executor = action_executor
        self.popup_labels: tuple[str, ...] = (
            tuple(popup_labels) if popup_labels is not None else DEFAULT_POPUP_DISMISS_LABELS
        )

    def get_current_state(self) -> dict[str, Any]:
        """获取当前设备页面状态字典。"""
        if self.state_provider is not None:
            state = self.state_provider()
            return state if isinstance(state, dict) else {}

        if self.ctx is not None and hasattr(self.ctx, "get_page_state") and callable(self.ctx.get_page_state):
            state = self.ctx.get_page_state()
            return state if isinstance(state, dict) else {}

        if self.driver is not None and hasattr(self.driver, "get_page_state") and callable(self.driver.get_page_state):
            state = self.driver.get_page_state()
            return state if isinstance(state, dict) else {}

        # Real AndroidDriver / HarmonyDriver fallback via _collect_page_elements
        if self.driver is not None and hasattr(self.driver, "ui"):
            try:
                from phone_pilot.mcp._page_state_tools import _collect_page_elements

                activity_info = self.driver.ui.get_current_activity()
                try:
                    w, h = self.driver.screen.get_screen_size()
                except Exception:
                    w, h = 0, 0
                elements, scrollable_areas, all_texts = _collect_page_elements(
                    self.driver, include_invisible=False
                )
                return {
                    "ok": True,
                    "activity": activity_info,
                    "screen": {"width": w, "height": h},
                    "elements": elements,
                    "element_count": len(elements),
                    "scrollable_areas": scrollable_areas,
                    "all_texts": all_texts,
                }
            except Exception as exc:
                _log(f"[UINavigator] driver _collect_page_elements error: {exc}")

        if self.ctx is not None:
            try:
                from phone_pilot import script_api as api

                if hasattr(api, "get_page_state"):
                    state = api.get_page_state(self.ctx)
                    return state if isinstance(state, dict) else {}
            except Exception as exc:
                _log(f"[UINavigator] get_page_state fallback error: {exc}")

        return {}

    # ------------------------------------------------------------------
    # 状态与元素匹配辅助方法（零硬编码坐标）
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_state_for_signature(state: dict[str, Any], sig: PageSignature) -> dict[str, Any]:
        """若 state['activity'] 为字典结构，归一化为字符串以兼容 PageSignature.matches。"""
        if not isinstance(state, dict):
            return {}
        if isinstance(state.get("activity"), dict) and sig.activity_or_controller:
            normalized = dict(state)
            normalized["activity"] = UIKnowledgeGraph._extract_activity_str(state)
            return normalized
        return state

    def _state_matches_node(self, state: dict[str, Any], node: Optional[UINode]) -> bool:
        """判断当前页面状态是否匹配指定 UINode。"""
        if not state or node is None:
            return False

        explicit_id = state.get("node_id")
        if isinstance(explicit_id, str) and explicit_id == node.node_id:
            # 若同时提供了页面元素/特征，校验特征未违背
            sig = node.page_signature
            if sig.required_elements or sig.forbidden_elements or sig.activity_or_controller or sig.extra_match_fn:
                norm_state = self._normalize_state_for_signature(state, sig)
                if state.get("elements") or state.get("all_texts") or state.get("activity") or state.get("view_controller"):
                    return sig.matches(norm_state)
            return True

        sig = node.page_signature
        has_sig_criteria = bool(
            sig.required_elements
            or sig.forbidden_elements
            or sig.activity_or_controller
            or sig.extra_match_fn
        )
        if has_sig_criteria:
            norm_state = self._normalize_state_for_signature(state, sig)
            if not sig.matches(norm_state):
                return False
            if node.activity_or_vc and not sig.activity_or_controller:
                state_act = UIKnowledgeGraph._extract_activity_str(state)
                if state_act and node.activity_or_vc not in state_act and state_act not in node.activity_or_vc:
                    return False
            return True

        if node.activity_or_vc:
            state_act = UIKnowledgeGraph._extract_activity_str(state)
            return bool(state_act and (node.activity_or_vc in state_act or state_act in node.activity_or_vc))

        return False

    def _matches_target(
        self,
        state: dict[str, Any],
        target: Union[PageSignature, str],
        target_node: Optional[UINode],
    ) -> tuple[bool, Optional[str]]:
        """检查 state 是否已到达目标，返回 (是否匹配, 命中的 node_id)。"""
        if not state:
            return False, None

        if isinstance(target, PageSignature):
            norm_state = self._normalize_state_for_signature(state, target)
            if target.matches(norm_state):
                matched = self.graph.match_node(state) or target_node
                resolved_id = matched.node_id if matched is not None else (target.name or None)
                return True, resolved_id
            if target_node is not None and self._state_matches_node(state, target_node):
                return True, target_node.node_id
            return False, None

        # target 为字符串（node_id 或页面名）
        if target_node is not None and self._state_matches_node(state, target_node):
            return True, target_node.node_id

        matched_node = self.graph.match_node(state)
        if matched_node is not None and (
            matched_node.node_id == target or matched_node.page_signature.name == target
        ):
            return True, matched_node.node_id

        if state.get("node_id") == target:
            return True, target

        return False, None

    @staticmethod
    def _compute_element_center(element: dict[str, Any]) -> Optional[tuple[int, int]]:
        """从元素字典的 center 或 bounds 动态计算中心坐标，严禁硬编码像素坐标。"""
        center = element.get("center")
        if isinstance(center, (list, tuple)) and len(center) == 2:
            try:
                return int(center[0]), int(center[1])
            except (TypeError, ValueError):
                pass
        elif isinstance(center, dict) and "x" in center and "y" in center:
            try:
                return int(center["x"]), int(center["y"])
            except (TypeError, ValueError):
                pass

        bounds = element.get("bounds")
        if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
            try:
                x1, y1, x2, y2 = bounds
                return int((x1 + x2) // 2), int((y1 + y2) // 2)
            except (TypeError, ValueError):
                pass
        elif isinstance(bounds, dict):
            try:
                x1 = bounds.get("x", bounds.get("left", 0))
                y1 = bounds.get("y", bounds.get("top", 0))
                x2 = bounds.get("right", x1 + bounds.get("width", 0))
                y2 = bounds.get("bottom", y1 + bounds.get("height", 0))
                return int((x1 + x2) // 2), int((y1 + y2) // 2)
            except (TypeError, ValueError):
                pass

        return None

    @staticmethod
    def _find_element_for_criteria(
        criteria: dict[str, Any], state: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """在当前页面 state['elements'] 中根据 trigger_criteria 查找匹配元素。"""
        elements = state.get("elements")
        if not isinstance(elements, list) or not criteria:
            return None

        match_keys = ("text", "label", "resource_id", "content_desc", "identifier", "name")
        target_queries: list[tuple[str, str]] = []
        for k in match_keys:
            val = criteria.get(k)
            if isinstance(val, str) and val.strip():
                target_queries.append((k, val.strip()))

        if not target_queries:
            return None

        # 优先精确匹配，其次包含匹配
        for exact in (True, False):
            for el in elements:
                if not isinstance(el, dict):
                    continue
                for key, query in target_queries:
                    candidate_vals = [
                        el.get(key),
                        el.get("text"),
                        el.get("label"),
                        el.get("content_desc"),
                        el.get("resource_id"),
                        el.get("identifier"),
                        el.get("name"),
                    ]
                    for cval in candidate_vals:
                        if not isinstance(cval, str) or not cval.strip():
                            continue
                        clean = cval.strip()
                        if exact and clean == query:
                            return el
                        if not exact and (query in clean or clean in query):
                            return el
        return None

    def _tap_coordinates_or_text(
        self,
        center: Optional[tuple[int, int]],
        text: Optional[str] = None,
    ) -> dict[str, Any]:
        """委托给 ctx 或 driver 执行动态中心点点击或文本点击。"""
        if center is not None:
            cx, cy = center
            for target in (self.ctx, self.driver):
                if target is not None and hasattr(target, "tap") and callable(target.tap):
                    res = target.tap(cx, cy)
                    return res if isinstance(res, dict) else {"ok": True, "center": (cx, cy)}
                inp = getattr(target, "input", None) if target is not None else None
                if inp is not None and hasattr(inp, "tap") and callable(inp.tap):
                    res = inp.tap(cx, cy)
                    if hasattr(target, "ui"):
                        import time as _time
                        _time.sleep(0.8)
                    return res if isinstance(res, dict) else {"ok": True, "center": (cx, cy)}

        if text:
            for target in (self.ctx, self.driver):
                if target is None:
                    continue
                for method_name in ("tap_text", "tap_when_ready"):
                    method = getattr(target, method_name, None)
                    if callable(method):
                        res = method(text)
                        return res if isinstance(res, dict) else {"ok": bool(res), "text": text}

        return {"ok": True, "center": center, "text": text}

    def _execute_edge(self, edge: UIEdge, state: dict[str, Any]) -> dict[str, Any]:
        """在当前状态上执行 UIEdge 定义的交互动作。"""
        if self.action_executor is not None:
            res = self.action_executor(edge, state)
            return res if isinstance(res, dict) else {"ok": bool(res)}

        action_type = edge.action_type.lower()
        criteria = edge.trigger_criteria or {}

        if action_type == "tap":
            matched_el = self._find_element_for_criteria(criteria, state)
            center = self._compute_element_center(matched_el) if matched_el else None
            label = (
                criteria.get("text")
                or criteria.get("label")
                or criteria.get("resource_id")
                or criteria.get("content_desc")
            )
            return self._tap_coordinates_or_text(center, str(label) if label else None)

        if action_type == "back":
            for target in (self.ctx, self.driver):
                if target is None:
                    continue
                for method_name in ("press_back", "go_back"):
                    method = getattr(target, method_name, None)
                    if callable(method):
                        res = method()
                        return res if isinstance(res, dict) else {"ok": True, "action": "back"}
            return {"ok": True, "action": "back"}

        if action_type == "swipe":
            self._perform_relative_swipe(state, direction=str(criteria.get("direction", "up")))
            return {"ok": True, "action": "swipe"}

        if action_type == "input":
            text = str(criteria.get("text", ""))
            for target in (self.ctx, self.driver):
                if target is None:
                    continue
                if hasattr(target, "input_text") and callable(target.input_text):
                    res = target.input_text(text)
                    return res if isinstance(res, dict) else {"ok": True, "action": "input"}
                inp = getattr(target, "input", None)
                if inp is not None and hasattr(inp, "input_text") and callable(inp.input_text):
                    res = inp.input_text(text)
                    return res if isinstance(res, dict) else {"ok": True, "action": "input"}
            return {"ok": True, "action": "input"}

        return {"ok": True, "action": action_type}

    # ------------------------------------------------------------------
    # 三级自愈机制 (Popup Dismissal / Scroll & Retry / Dynamic Re-pathing)
    # ------------------------------------------------------------------

    def _find_popup_element(
        self,
        state: dict[str, Any],
        expected_node: Optional[UINode] = None,
    ) -> Optional[tuple[str, Optional[dict[str, Any]]]]:
        """检查 state 中是否存在弹窗关闭/跳过元素（排除目标页面本身的合法特征元素）。"""
        if not isinstance(state, dict):
            return None

        expected_reqs: set[str] = set()
        if expected_node is not None:
            expected_reqs.update(expected_node.page_signature.required_elements)

        # 1. 检查显式 popup 字段（若测试或驱动提供）
        popup_info = state.get("popup")
        if isinstance(popup_info, dict):
            label = str(popup_info.get("text") or popup_info.get("label") or "关闭").strip()
            return label, popup_info
        if isinstance(popup_info, str) and popup_info.strip():
            label = popup_info.strip()
            return label, {"text": label}

        # 2. 遍历 elements 列表查找匹配 popup_labels 的元素
        elements = state.get("elements")
        if isinstance(elements, list):
            for el in elements:
                if not isinstance(el, dict):
                    continue
                for key in ("text", "label", "content_desc", "name", "title", "value"):
                    raw_val = el.get(key)
                    if not isinstance(raw_val, str) or not raw_val.strip():
                        continue
                    clean = raw_val.strip()
                    if clean in expected_reqs:
                        continue
                    if self._matches_popup_label(clean):
                        return clean, el

        # 3. 遍历 all_texts 列表
        all_texts = state.get("all_texts")
        if isinstance(all_texts, list):
            for txt in all_texts:
                if not isinstance(txt, str) or not txt.strip():
                    continue
                clean = txt.strip()
                if clean in expected_reqs:
                    continue
                if self._matches_popup_label(clean):
                    return clean, {"text": clean}

        return None

    def _matches_popup_label(self, text: str) -> bool:
        """判断文本是否命中弹窗关闭词表。"""
        text_lower = text.lower()
        for label in self.popup_labels:
            label_lower = label.lower()
            if text == label or text_lower == label_lower:
                return True
            # 对长度 >= 2 的中文标签（如“关闭”、“跳过”、“允许”）支持前缀/包含匹配（如“跳过广告”、“关闭弹窗”）
            if len(label) >= 2 and not label.isascii() and label in text:
                return True
        return False

    def _dismiss_popup(
        self,
        popup_label: str,
        popup_el: Optional[dict[str, Any]],
        state: dict[str, Any],
    ) -> dict[str, Any]:
        """执行弹窗消除动作。"""
        if self.action_executor is not None:
            criteria: dict[str, Any] = {"text": popup_label, "is_popup_dismiss": True}
            if isinstance(popup_el, dict):
                center = self._compute_element_center(popup_el)
                if center is not None:
                    criteria["center"] = center
            dismiss_edge = UIEdge(
                source_id=str(state.get("node_id") or "popup"),
                target_id="popup_dismissed",
                action_type="tap",
                trigger_criteria=criteria,
            )
            res = self.action_executor(dismiss_edge, state)
            return res if isinstance(res, dict) else {"ok": bool(res)}

        center = self._compute_element_center(popup_el) if isinstance(popup_el, dict) else None
        return self._tap_coordinates_or_text(center, popup_label)

    def _perform_relative_swipe(self, state: dict[str, Any], direction: str = "up") -> bool:
        """基于屏幕几何尺寸比例动态计算滑动起止点，严禁写死像素坐标。"""
        screen_w: Optional[int] = None
        screen_h: Optional[int] = None

        screen_size = state.get("screen_size") or state.get("screen")
        if isinstance(screen_size, (list, tuple)) and len(screen_size) == 2:
            screen_w, screen_h = int(screen_size[0]), int(screen_size[1])
        elif isinstance(screen_size, dict):
            screen_w = int(screen_size.get("width", 0)) or None
            screen_h = int(screen_size.get("height", 0)) or None
        elif isinstance(state.get("width"), (int, float)) and isinstance(state.get("height"), (int, float)):
            screen_w, screen_h = int(state["width"]), int(state["height"])

        if (not screen_w or not screen_h) and self.driver is not None:
            get_size = getattr(self.driver, "get_screen_size", None)
            if not callable(get_size) and hasattr(self.driver, "screen"):
                get_size = getattr(self.driver.screen, "get_screen_size", None)
            if callable(get_size):
                size = get_size()
                if isinstance(size, (list, tuple)) and len(size) == 2:
                    screen_w, screen_h = int(size[0]), int(size[1])

        if screen_w and screen_h:
            cx = int(screen_w * 0.5)
            if direction == "down":
                y_start, y_end = int(screen_h * 0.3), int(screen_h * 0.75)
            else:
                y_start, y_end = int(screen_h * 0.75), int(screen_h * 0.25)

            for target in (self.ctx, self.driver):
                if target is not None and hasattr(target, "swipe") and callable(target.swipe):
                    target.swipe(cx, y_start, cx, y_end)
                    return True
                inp = getattr(target, "input", None) if target is not None else None
                if inp is not None and hasattr(inp, "swipe") and callable(inp.swipe):
                    inp.swipe(cx, y_start, cx, y_end)
                    return True

        for target in (self.ctx, self.driver):
            if target is not None and hasattr(target, "scroll") and callable(target.scroll):
                target.scroll(direction=direction)
                return True

        return False

    def _try_scroll(self, state: dict[str, Any]) -> bool:
        """尝试在当前页面上执行滚动以露出未见元素。"""
        if self._perform_relative_swipe(state, direction="up"):
            return True

        if self.action_executor is not None and state.get("scrollable"):
            scroll_edge = UIEdge(
                source_id=str(state.get("node_id") or "current"),
                target_id=str(state.get("node_id") or "current"),
                action_type="swipe",
                trigger_criteria={"direction": "up", "is_scroll_retry": True},
            )
            self.action_executor(scroll_edge, state)
            return True

        return False

    def _find_alternative_path(
        self,
        target: Union[PageSignature, str],
        current_state: dict[str, Any],
        failed_edges: set[int],
    ) -> list[UIEdge]:
        """在排除本次已失败边的前提下优先寻找替代路径；若无替代路径则回退至标准 Dijkstra。"""
        if failed_edges:
            original_edges = list(self.graph.edges)
            try:
                self.graph.edges = [e for e in original_edges if id(e) not in failed_edges]
                alt_path = self.graph.find_path_to(target, current_state)
                if alt_path:
                    return alt_path
            finally:
                self.graph.edges = original_edges

        path = self.graph.find_path_to(target, current_state)
        # 若返回的路径首条边就是刚刚失败的同一条边，说明无真正可用的重规划路径
        if path and id(path[0]) in failed_edges:
            return []
        return path

    # ------------------------------------------------------------------
    # 主导航入口
    # ------------------------------------------------------------------

    def navigate_to(
        self,
        target: PageSignature | str,
        *,
        current_state: dict[str, Any] | None = None,
        max_hops: int = 10,
        max_heals: int = 2,
    ) -> dict[str, Any]:
        """自动规划并执行从当前状态到 target 的拓扑路径，内置三级自愈机制。"""
        target_name = target.name if isinstance(target, PageSignature) else str(target)
        target_node = self.graph.match_node(target)

        state = current_state if current_state is not None else self.get_current_state()
        hops_executed = 0
        heals_used = 0
        path_taken: list[dict[str, Any]] = []
        healing_events: list[dict[str, Any]] = []
        failed_edge_ids: set[int] = set()

        # 1. 检查是否已经处于目标页面
        at_target, final_node_id = self._matches_target(state, target, target_node)
        if at_target:
            return {
                "ok": True,
                "target": target_name,
                "hops_executed": 0,
                "heals_used": 0,
                "path_taken": [],
                "healing_events": [],
                "final_node_id": final_node_id,
                "reason": "already_at_target",
            }

        # 2. 规划初始最短路径；若因弹窗遮挡导致起点未识别，先尝试 1 次弹窗自愈
        edges = self.graph.find_path_to(target, state)
        if not edges and heals_used < max_heals:
            popup_match = self._find_popup_element(state, expected_node=target_node)
            if popup_match is not None:
                popup_label, popup_el = popup_match
                heals_used += 1
                self._dismiss_popup(popup_label, popup_el, state)
                healing_events.append(
                    {
                        "type": "popup_dismiss",
                        "label": popup_label,
                        "stage": "initial_state",
                    }
                )
                state = self.get_current_state()
                at_target, final_node_id = self._matches_target(state, target, target_node)
                if at_target:
                    return {
                        "ok": True,
                        "target": target_name,
                        "hops_executed": hops_executed,
                        "heals_used": heals_used,
                        "path_taken": path_taken,
                        "healing_events": healing_events,
                        "final_node_id": final_node_id,
                        "reason": "reached_target",
                    }
                edges = self.graph.find_path_to(target, state)

        if not edges:
            return {
                "ok": False,
                "target": target_name,
                "hops_executed": 0,
                "heals_used": heals_used,
                "path_taken": [],
                "healing_events": healing_events,
                "final_node_id": None,
                "reason": "no_path_found",
            }

        # 3. 逐步执行路径边，监控页面漂移并触发三级自愈
        edge_queue: deque[UIEdge] = deque(edges)

        while edge_queue and hops_executed < max_hops:
            edge = edge_queue.popleft()
            expected_node = self.graph.get_node(edge.target_id)

            self._execute_edge(edge, state)
            hops_executed += 1
            next_state = self.get_current_state()

            # 校验是否按预期到达 edge.target_id
            if self._state_matches_node(next_state, expected_node):
                edge.record_outcome(True)
                path_taken.append(
                    {
                        "source_id": edge.source_id,
                        "target_id": edge.target_id,
                        "action_type": edge.action_type,
                        "trigger_criteria": dict(edge.trigger_criteria),
                    }
                )
                state = next_state
                continue

            # --- 检测到页面漂移 (Page Drift) ---
            if heals_used >= max_heals:
                edge.record_outcome(False)
                drifted_node = self.graph.match_node(next_state)
                return {
                    "ok": False,
                    "target": target_name,
                    "hops_executed": hops_executed,
                    "heals_used": heals_used,
                    "path_taken": path_taken,
                    "healing_events": healing_events,
                    "final_node_id": drifted_node.node_id if drifted_node else None,
                    "reason": f"drift_unrecovered_at_{edge.target_id}",
                }

            heals_used += 1
            recovered = False

            # Level 1: Popup Dismissal (弹窗遮挡消除)
            popup_match = self._find_popup_element(next_state, expected_node=expected_node)
            if popup_match is not None:
                popup_label, popup_el = popup_match
                self._dismiss_popup(popup_label, popup_el, next_state)
                healing_events.append(
                    {
                        "type": "popup_dismiss",
                        "label": popup_label,
                        "edge": f"{edge.source_id}->{edge.target_id}",
                    }
                )
                post_popup_state = self.get_current_state()
                if self._state_matches_node(post_popup_state, expected_node):
                    edge.record_outcome(True)
                    path_taken.append(
                        {
                            "source_id": edge.source_id,
                            "target_id": edge.target_id,
                            "action_type": edge.action_type,
                            "trigger_criteria": dict(edge.trigger_criteria),
                        }
                    )
                    state = post_popup_state
                    recovered = True
                else:
                    # 弹窗关闭后回到源页面，重新执行当前边
                    self._execute_edge(edge, post_popup_state)
                    retry_state = self.get_current_state()
                    if self._state_matches_node(retry_state, expected_node):
                        edge.record_outcome(True)
                        path_taken.append(
                            {
                                "source_id": edge.source_id,
                                "target_id": edge.target_id,
                                "action_type": edge.action_type,
                                "trigger_criteria": dict(edge.trigger_criteria),
                            }
                        )
                        state = retry_state
                        recovered = True
                    else:
                        next_state = retry_state

            if recovered:
                continue

            # Level 2: Scroll & Retry (滚动并重试当前点击边)
            if edge.action_type.lower() == "tap" and self._try_scroll(next_state):
                healing_events.append(
                    {
                        "type": "scroll_retry",
                        "edge": f"{edge.source_id}->{edge.target_id}",
                    }
                )
                post_scroll_state = self.get_current_state()
                self._execute_edge(edge, post_scroll_state)
                retry_state = self.get_current_state()
                if self._state_matches_node(retry_state, expected_node):
                    edge.record_outcome(True)
                    path_taken.append(
                        {
                            "source_id": edge.source_id,
                            "target_id": edge.target_id,
                            "action_type": edge.action_type,
                            "trigger_criteria": dict(edge.trigger_criteria),
                        }
                    )
                    state = retry_state
                    continue
                next_state = retry_state

            # Level 3: Dynamic Re-pathing (记录失败边，从当前漂移节点动态重规划路径)
            edge.record_outcome(False)
            failed_edge_ids.add(id(edge))
            drifted_node = self.graph.match_node(next_state)
            if drifted_node is not None:
                new_edges = self._find_alternative_path(target, next_state, failed_edge_ids)
                if new_edges:
                    healing_events.append(
                        {
                            "type": "dynamic_repath",
                            "from_node": drifted_node.node_id,
                            "failed_edge": f"{edge.source_id}->{edge.target_id}",
                        }
                    )
                    state = next_state
                    edge_queue = deque(new_edges)
                    continue

            return {
                "ok": False,
                "target": target_name,
                "hops_executed": hops_executed,
                "heals_used": heals_used,
                "path_taken": path_taken,
                "healing_events": healing_events,
                "final_node_id": drifted_node.node_id if drifted_node else None,
                "reason": f"drift_unrecovered_at_{edge.target_id}",
            }

        # 4. 校验最终页面状态是否匹配目标
        at_target, final_node_id = self._matches_target(state, target, target_node)
        if at_target:
            return {
                "ok": True,
                "target": target_name,
                "hops_executed": hops_executed,
                "heals_used": heals_used,
                "path_taken": path_taken,
                "healing_events": healing_events,
                "final_node_id": final_node_id,
                "reason": "reached_target",
            }

        matched_end = self.graph.match_node(state)
        return {
            "ok": False,
            "target": target_name,
            "hops_executed": hops_executed,
            "heals_used": heals_used,
            "path_taken": path_taken,
            "healing_events": healing_events,
            "final_node_id": matched_end.node_id if matched_end else None,
            "reason": "max_hops_exceeded" if hops_executed >= max_hops else "target_not_matched",
        }
