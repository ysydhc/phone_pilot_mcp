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
import html
import json
import pathlib
import re
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
    """认知 UI 状态图中的页面节点（支持三层层次化状态机：page / sub_tab / modal）。"""

    node_id: str
    page_signature: PageSignature
    activity_or_vc: str = ""
    interactive_elements: list[dict[str, Any]] = field(default_factory=list)
    last_seen_timestamp: float = field(default_factory=time.time)
    semantic_name: str = ""
    node_type: str = "page"  # "page" | "sub_tab" | "modal"
    host_node_id: str = ""
    active_fragment: str = ""
    structure_hash: str = ""
    id_skeleton: list[str] = field(default_factory=list)
    scroll_pages_stitched: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "semantic_name": self.semantic_name,
            "node_type": self.node_type,
            "host_node_id": self.host_node_id,
            "active_fragment": self.active_fragment,
            "structure_hash": self.structure_hash,
            "id_skeleton": list(self.id_skeleton),
            "scroll_pages_stitched": self.scroll_pages_stitched,
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
            semantic_name=str(data.get("semantic_name", "")),
            node_type=str(data.get("node_type", "page")),
            host_node_id=str(data.get("host_node_id", "")),
            active_fragment=str(data.get("active_fragment", "")),
            structure_hash=str(data.get("structure_hash", "")),
            id_skeleton=list(data.get("id_skeleton", [])),
            scroll_pages_stitched=int(data.get("scroll_pages_stitched", 1)),
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
    """页面认知与导航知识图谱引擎（支持层次化状态机、结构骨架 Jaccard 匹配与语义命名）。"""

    def __init__(
        self,
        nodes: Optional[dict[str, UINode]] = None,
        edges: Optional[list[UIEdge]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.nodes: dict[str, UINode] = dict(nodes) if nodes else {}
        self.edges: list[UIEdge] = list(edges) if edges else []
        self.metadata: dict[str, Any] = dict(metadata) if metadata else {}

    def add_or_update_node(self, node: UINode) -> UINode:
        """添加或更新一个页面节点。"""
        existing = self.nodes.get(node.node_id)
        if existing is not None:
            existing.page_signature = node.page_signature
            if node.activity_or_vc:
                existing.activity_or_vc = node.activity_or_vc
            if node.interactive_elements:
                existing.interactive_elements = list(node.interactive_elements)
            if node.semantic_name:
                existing.semantic_name = node.semantic_name
            if node.node_type:
                existing.node_type = node.node_type
            if node.host_node_id:
                existing.host_node_id = node.host_node_id
            if node.active_fragment:
                existing.active_fragment = node.active_fragment
            if node.structure_hash:
                existing.structure_hash = node.structure_hash
            if node.id_skeleton:
                existing.id_skeleton = list(node.id_skeleton)
            if node.scroll_pages_stitched > existing.scroll_pages_stitched:
                existing.scroll_pages_stitched = node.scroll_pages_stitched
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

    @staticmethod
    def _extract_active_fragment(page_state: dict[str, Any]) -> str:
        frag = page_state.get("active_fragment")
        if isinstance(frag, str) and frag.strip():
            return frag.strip()
        act = page_state.get("activity")
        if isinstance(act, dict):
            f = act.get("active_fragment") or act.get("fragment")
            if isinstance(f, str) and f.strip():
                return f.strip()
        return ""

    @staticmethod
    def _extract_id_skeleton(page_state: dict[str, Any]) -> tuple[list[str], str]:
        """提取页面的非通用 resource_id 结构骨架列表及哈希值，用于跨动态数据的页面结构等价判定。"""
        generic_ids = {
            "content", "list", "recycler", "recyclerview", "recycler_view",
            "pager", "viewpager", "view_pager", "tabs", "tab_layout", "tab_text",
            "tab_title", "title", "subtitle", "name", "text", "icon", "image",
            "button", "more", "close", "cancel", "search",
        }
        raw_elements = page_state.get("elements") or []
        ids: set[str] = set()
        if isinstance(raw_elements, list):
            for el in raw_elements:
                if not isinstance(el, dict):
                    continue
                rid = str(el.get("resource_id") or el.get("identifier") or "").strip()
                if not rid:
                    continue
                short_id = rid.split("/")[-1].strip().lower()
                if short_id and short_id not in generic_ids and len(short_id) >= 3:
                    ids.add(short_id)
        sorted_ids = sorted(ids)
        if not sorted_ids:
            return [], ""
        skel_str = "|".join(sorted_ids)
        skel_hash = hashlib.sha1(skel_str.encode("utf-8")).hexdigest()[:8]
        return sorted_ids, skel_hash

    @staticmethod
    def _skeleton_jaccard(skel_a: Sequence[str], skel_b: Sequence[str]) -> float:
        set_a = set(skel_a)
        set_b = set(skel_b)
        if not set_a or not set_b:
            return 0.0
        inter = len(set_a & set_b)
        union = len(set_a | set_b)
        return inter / union if union > 0 else 0.0

    @staticmethod
    def _clean_ui_label(txt: str) -> str:
        """清洗无障碍/UI 文本标签，剥离方括号、选中态前缀与控件类型后缀（如 '[已选中，首页，按钮]' -> '首页'）。"""
        import re

        if not txt:
            return ""
        prefix = txt.split("[")[0].strip()
        inner = prefix if prefix else re.sub(r"^\[|\]$", "", txt.strip())
        for token in ("已选中，", "已选中,", "未选中，", "未选中,"):
            if inner.startswith(token):
                inner = inner[len(token) :].strip()
        clean = inner.split(",")[0].split("，")[0].strip()
        if clean in ("按钮", "标签", "图片", "已选中", "未选中"):
            return ""
        return clean

    @classmethod
    def _infer_semantic_metadata(cls, page_state: dict[str, Any]) -> tuple[str, str]:
        """从页面状态通过通用几何与结构特征推断层次化节点类型 (page / sub_tab / modal) 与可读语义名称（零业务硬编码）。"""
        import re

        raw_elements = page_state.get("elements") or []
        screen_info = page_state.get("screen") or {}
        screen_w = int(screen_info.get("width") or 0) if isinstance(screen_info, dict) else 0
        screen_h = int(screen_info.get("height") or 0) if isinstance(screen_info, dict) else 0
        act_str = cls._extract_activity_str(page_state)
        short_act = act_str.split(".")[-1].split("/")[-1] if act_str else "Page"
        frag_str = cls._extract_active_fragment(page_state)

        node_type = str(page_state.get("node_type") or "").strip()
        min_y = screen_h if screen_h > 0 else 99999
        max_y = 0
        bottom_nav_items: list[str] = []
        selected_bottom_tab: str = ""
        top_channel_tabs: list[str] = []
        selected_top_tab: str = ""
        has_modal_close = False
        has_search_input = False
        has_profile_header = False
        has_media_player = False
        res_ids_set: set[str] = set()
        top_titles: list[str] = []
        all_labels: list[str] = []

        for el in raw_elements if isinstance(raw_elements, list) else []:
            if not isinstance(el, dict):
                continue
            txt = str(el.get("text") or el.get("label") or el.get("content_desc") or "").strip()
            rid = str(el.get("resource_id") or el.get("identifier") or "").strip().split("/")[-1].lower()
            el_type = str(el.get("type") or "").strip().lower()
            if rid:
                res_ids_set.add(rid)
            if txt:
                all_labels.append(txt)

            if el_type == "input" and ("search" in rid or "搜索" in txt):
                has_search_input = True
            if any(k in rid for k in ("nick", "vip_label", "fans_count", "follower", "go_to_space", "user_space", "user_avatar")):
                has_profile_header = True
            if any(k in rid for k in ("player", "video_view", "danmaku", "seek_bar", "full_screen")):
                has_media_player = True

            bds = el.get("bounds")
            if isinstance(bds, (list, tuple)) and len(bds) == 4:
                try:
                    x1, y1, x2, y2 = int(bds[0]), int(bds[1]), int(bds[2]), int(bds[3])
                    if y2 > y1:
                        min_y = min(min_y, y1)
                        max_y = max(max_y, y2)
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    bw_el = max(0, x2 - x1)
                    bh_el = max(0, y2 - y1)
                    is_sel_el = bool(el.get("selected") is True or "已选中" in txt)
                    if screen_h > 0 and cy >= screen_h * 0.88:
                        clean_tab = cls._clean_ui_label(txt)
                        is_geom_bottom_tab = (
                            screen_w > 0
                            and 0 < bw_el <= screen_w * 0.36
                            and bh_el <= screen_h * 0.12
                            and clean_tab in ("首页", "朋友", "关注", "动态", "发现", "消息", "商城", "会员购", "我的", "我", "Home", "Me", "Profile")
                        )
                        if "标签" in txt or "tab" in rid or re.search(r",\d+之\d+", txt) or is_geom_bottom_tab:
                            if clean_tab and clean_tab not in bottom_nav_items:
                                bottom_nav_items.append(clean_tab)
                            if is_sel_el and clean_tab:
                                selected_bottom_tab = clean_tab
                    is_pua_or_num = any(0xE000 <= ord(c) <= 0xF8FF for c in txt) or txt.replace(":", "").replace(".", "").isdigit()
                    if screen_h > 0 and 0 < cy <= screen_h * 0.22 and txt:
                        clean_ch = cls._clean_ui_label(txt)
                        is_geom_top_tab = (
                            screen_w > 0
                            and 0 < bw_el <= screen_w * 0.22
                            and bh_el <= screen_h * 0.08
                            and clean_ch in ("直播", "团购", "同城", "关注", "商城", "推荐", "热门", "发现", "视频", "频道")
                        )
                        if "标签" in txt or "tab" in rid or is_geom_top_tab:
                            if clean_ch and clean_ch not in top_channel_tabs:
                                top_channel_tabs.append(clean_ch)
                            if is_sel_el and clean_ch:
                                selected_top_tab = clean_ch
                        elif (
                            2 <= len(txt) <= 12
                            and not txt.startswith("[")
                            and not txt.startswith("搜索")
                            and not is_pua_or_num
                            and txt not in ("转到上一层级", "返回", "关闭", "取消", "设置", "帮助")
                        ):
                            is_title_id = any(k in rid for k in ("title", "toolbar", "action_bar", "header", "nav_bar"))
                            is_centered_top = (
                                cy <= screen_h * 0.14
                                and (screen_w == 0 or (screen_w * 0.28 <= cx <= screen_w * 0.72))
                                and not any(k in rid for k in ("search", "count", "nick", "user"))
                            )
                            if is_title_id or is_centered_top:
                                if txt not in top_titles:
                                    top_titles.append(txt)
                except Exception:
                    pass
            if (
                any(k in rid for k in ("cancel", "quit", "dialog_close", "dismiss", "share_title", "dialog"))
                or txt in ("取消", "知道了", "立即转发")
                or txt.endswith("秒后关闭")
            ) and "toolbar" not in rid:
                has_modal_close = True

        has_bottom_nav = len(bottom_nav_items) >= 2
        has_top_appbar = "toolbar_close" in res_ids_set or any("转到上一层级" in t for t in all_labels)
        is_comment_modal = any(
            k in t for t in all_labels for k in ("条评论", "放大评论区", "留下你的精彩评论", "发条友善的评论", "善语结善缘", "暂无评论")
        )
        is_share_modal = any(
            k in t for t in all_labels for k in ("分享给朋友", "分享到", "日常转发", "复制链接", "建群分享")
        )
        is_side_drawer = bool(
            not has_bottom_nav
            and any(k in all_labels for k in ("常用功能", "更多功能"))
            and ("设置" in all_labels or "[关闭，按钮]" in all_labels)
        )
        is_search_page = bool(has_search_input or ("search_src_text" in res_ids_set) or ("search" in short_act.lower()))

        # 1. 通用几何与系统层级判定 node_type: modal vs sub_tab vs page
        is_main_host = any(k in short_act for k in ("Main", "Home", "Index", "Tab"))
        is_login_page = "login" in short_act.lower() or any(
            m in t for t in all_labels for m in ("验证码登录", "密码登录", "一键登录", "登录后即可", "请输入手机号")
        )
        if not node_type:
            if page_state.get("window_type") == "modal" or is_comment_modal or is_share_modal:
                node_type = "modal"
            elif not has_top_appbar and screen_h > 0 and max_y > min_y and min_y >= screen_h * 0.25 and (has_modal_close or len(raw_elements) <= 15):
                node_type = "modal"
            elif not has_top_appbar and has_modal_close and len(raw_elements) <= 10:
                node_type = "modal"
            elif not is_login_page and not is_search_page and (
                has_bottom_nav or is_side_drawer or len(top_channel_tabs) >= 3 or (is_main_host and (frag_str or has_profile_header))
            ):
                node_type = "sub_tab"
            else:
                node_type = "page"

        # 2. 通用结构推断可读语义名称 semantic_name（若状态仅含类名，将在 record_transition 中由入边触发器名称动态补全）
        explicit_sem = str(page_state.get("semantic_name") or "").strip()
        if explicit_sem:
            return node_type, explicit_sem

        sem_name = ""
        if is_login_page:
            sem_name = "登录页"
        elif is_side_drawer:
            sem_name = "侧边栏"
        elif is_search_page and not has_bottom_nav:
            sem_name = "搜索页"
        elif node_type == "modal":
            if is_comment_modal:
                sem_name = "评论弹窗"
            elif is_share_modal:
                sem_name = "分享弹窗"
            elif any(k in t for t in all_labels for k in ("取消关注", "举报", "不感兴趣")):
                sem_name = "更多操作弹窗"
            elif all_labels:
                first_clean = next(
                    (
                        cls._clean_ui_label(t)
                        for t in all_labels
                        if 1 < len(cls._clean_ui_label(t)) <= 10
                        and cls._clean_ui_label(t) not in ("取消", "确定", "关闭", "知道了", "转到上一层级", "返回")
                        and not t.endswith("秒后关闭")
                    ),
                    "",
                )
                sem_name = f"{first_clean}弹窗" if first_clean else f"{short_act}弹窗"
            else:
                sem_name = f"{short_act}弹窗"
        else:
            if selected_bottom_tab and (selected_bottom_tab != "首页" or not selected_top_tab or selected_top_tab == "推荐"):
                sem_name = selected_bottom_tab
            elif selected_top_tab and selected_top_tab != "推荐":
                sem_name = selected_top_tab
            elif selected_bottom_tab:
                sem_name = selected_bottom_tab
            elif has_search_input or ("search_src_text" in res_ids_set):
                sem_name = "搜索页"
            elif node_type == "sub_tab" and len(top_channel_tabs) >= 3:
                sem_name = "首页"
            elif node_type == "sub_tab" and has_profile_header and not top_titles:
                sem_name = "个人中心"
            elif top_titles:
                sem_name = top_titles[0]
            elif node_type == "sub_tab":
                sem_name = "首页"
            elif has_media_player:
                sem_name = "媒体播放详情页"
            elif has_profile_header and node_type == "page":
                sem_name = "个人主页"
            elif frag_str:
                sem_name = frag_str.replace("Fragment", "")
            else:
                sem_name = short_act

        return node_type, sem_name

    @staticmethod
    def _selected_labels(elements: Any) -> tuple[str, ...]:
        """选中态是状态证据，不能用可能来自入边的显示语义名替代。"""
        if not isinstance(elements, list):
            return ()
        return tuple(sorted({
            UIKnowledgeGraph._clean_ui_label(str(
                el.get("text") or el.get("label") or el.get("content_desc")
                or el.get("resource_id") or el.get("identifier") or ""
            ))
            for el in elements if isinstance(el, dict)
            and (el.get("selected") is True or "已选中" in str(el.get("text") or el.get("label") or ""))
        } - {""}))

    @classmethod
    def _nodes_structurally_equivalent(cls, left: UINode, right: UINode) -> bool:
        """同语义只是候选组；非空签名一致、且已有骨架不冲突才可归并。"""
        a, b = left.page_signature, right.page_signature
        if a.extra_match_fn is not b.extra_match_fn:
            return False
        if set(a.forbidden_elements) != set(b.forbidden_elements):
            return False
        if cls._selected_labels(left.interactive_elements) != cls._selected_labels(right.interactive_elements):
            return False
        if set(a.required_elements) != set(b.required_elements):
            return False
        if left.id_skeleton and right.id_skeleton:
            if cls._skeleton_jaccard(left.id_skeleton, right.id_skeleton) < 0.80:
                return False
            return bool(a.required_elements) or min(len(left.id_skeleton), len(right.id_skeleton)) >= 3
        return bool(a.required_elements)

    def consolidate_equivalent_nodes(self) -> dict[str, str]:
        """归并相同 Activity/Fragment/宿主/语义组内有结构等价证据的节点。

        仅折叠完整 trigger_criteria 相同的边，保留不同定位器及成功统计。
        返回 {被合并旧节点ID: 代表节点ID} 映射。
        """
        groups: dict[tuple[str, str, str, str, str], list[UINode]] = {}
        for nd in self.nodes.values():
            short_act = nd.activity_or_vc.split(".")[-1].split("/")[-1] if nd.activity_or_vc else ""
            sem = (nd.semantic_name or "").strip()
            ntype = nd.node_type or "page"
            if sem and sem != short_act:
                key = (nd.activity_or_vc, ntype, sem, nd.active_fragment, nd.host_node_id)
            else:
                key = (nd.activity_or_vc, ntype, nd.node_id, nd.active_fragment, nd.host_node_id)
            groups.setdefault(key, []).append(nd)

        id_map: dict[str, str] = {}
        new_nodes: dict[str, UINode] = {}
        for _key, nd_list in groups.items():
            # 选择视口拼接屏数最多、可交互组件最丰富的节点作为 Canonical 代表
            nd_list.sort(
                key=lambda x: (x.scroll_pages_stitched, len(x.interactive_elements or [])),
                reverse=True,
            )
            representatives: list[UINode] = []
            for other in nd_list:
                canonical = next((rep for rep in representatives
                                  if self._nodes_structurally_equivalent(rep, other)), None)
                if canonical is None:
                    representatives.append(other)
                    new_nodes[other.node_id] = other
                else:
                    id_map[other.node_id] = canonical.node_id

        self.nodes = new_nodes
        for node in self.nodes.values():
            node.host_node_id = id_map.get(node.host_node_id, node.host_node_id)
        deduped_edges: list[UIEdge] = []
        seen_edges: dict[tuple[str, str, str, str], UIEdge] = {}

        for ed in self.edges:
            ed.source_id = id_map.get(ed.source_id, ed.source_id)
            ed.target_id = id_map.get(ed.target_id, ed.target_id)
            if ed.source_id not in self.nodes or ed.target_id not in self.nodes:
                continue
            crit = ed.trigger_criteria or {}

            ekey = (ed.source_id, ed.target_id, ed.action_type,
                    json.dumps(crit, sort_keys=True, ensure_ascii=False))
            if ekey not in seen_edges:
                seen_edges[ekey] = ed
                deduped_edges.append(ed)
            else:
                prev_ed = seen_edges[ekey]
                prev_ed.attempts += ed.attempts
                prev_ed.successes += ed.successes
                prev_ed.success_rate = round(prev_ed.successes / max(1, prev_ed.attempts), 4)
        self.edges = deduped_edges
        return id_map

    def _node_match_score(self, state: dict[str, Any], node: UINode) -> Optional[tuple[int, int, float]]:
        """规划与到达校验共享判定；显示语义不是身份约束。"""
        sig = node.page_signature
        state_host = str(state.get("host_node_id") or "")
        # 缺宿主元数据兼容旧状态；显式不同宿主不能被文本或骨架命中绕过。
        if state_host and node.host_node_id and state_host != node.host_node_id:
            return None
        state_act = self._extract_activity_str(state)
        state_frag = self._extract_active_fragment(state)
        if node.active_fragment and state_frag and node.active_fragment != state_frag:
            return None
        if node.activity_or_vc and state_act and node.activity_or_vc != state_act:
            # 兼容旧图谱仅保存类名的活动约束，但完整活动名不能跨包误命中。
            if "/" in node.activity_or_vc:
                return None
            if node.activity_or_vc not in state_act and state_act not in node.activity_or_vc:
                return None
        live_selected = self._selected_labels(state.get("elements"))
        recorded_selected = self._selected_labels(node.interactive_elements)
        if live_selected and recorded_selected and live_selected != recorded_selected:
            return None
        if state.get("semantic_name") and node.semantic_name and state["semantic_name"] != node.semantic_name:
            return None
        inferred_type, _ = self._infer_semantic_metadata(state)
        # v1 节点无语义/骨架元数据；缺尺寸时几何推断也不足以否决文本签名。
        modern = bool(node.semantic_name or node.id_skeleton or node.structure_hash)
        screen = state.get("screen") or {}
        has_geometry = isinstance(screen, dict) and bool(screen.get("height"))
        if modern and (state.get("node_type") or state.get("window_type") == "modal" or has_geometry):
            if node.node_type and node.node_type != inferred_type:
                return None
        normalized = dict(state)
        if not state.get("elements") and isinstance(state.get("all_texts"), list):
            normalized["elements"] = [{"text": text} for text in state["all_texts"] if isinstance(text, str)]
        if isinstance(state.get("activity"), dict):
            normalized["activity"] = state_act
        has_criteria = bool(sig.required_elements or sig.forbidden_elements
                            or sig.activity_or_controller or sig.extra_match_fn or node.activity_or_vc)
        if has_criteria and sig.matches(normalized):
            return (2, len(sig.required_elements), float(bool(state_frag and node.active_fragment)))
        # 骨架不能绕过 forbidden、自定义回调、Activity 或缺失的 Fragment 证据。
        if node.active_fragment and not state_frag:
            return None
        guards = PageSignature(activity_or_controller=sig.activity_or_controller,
                               forbidden_elements=sig.forbidden_elements, extra_match_fn=sig.extra_match_fn)
        if not guards.matches(normalized):
            return None
        state_skel, _ = self._extract_id_skeleton(state)
        if not state_act or not node.activity_or_vc or min(len(state_skel), len(node.id_skeleton)) < 3:
            return None
        if node.node_type == "modal":
            return None  # 宿主 DOM 骨架不足以证明某一个弹窗的身份。
        if node.node_type == "sub_tab" and not (live_selected and recorded_selected) and not state_frag:
            return None
        # 骨架仅允许绕过已录制为易变的特征；稳定标题/按钮缺失仍须拒绝。
        for required in sig.required_elements:
            if PageSignature(required_elements=[required]).matches(normalized):
                continue
            recorded = [el for el in node.interactive_elements if isinstance(el, dict)
                        and any(el.get(key) == required for key in
                                ("text", "label", "content_desc", "resource_id", "identifier", "name"))]
            if not recorded or not all(self._is_volatile_signature_element(el, required) for el in recorded):
                return None
        jaccard = self._skeleton_jaccard(state_skel, node.id_skeleton)
        return (1, 0, jaccard) if jaccard >= 0.80 else None

    def state_matches_node(self, state: dict[str, Any], node: UINode) -> bool:
        """校验指定节点，不依赖图谱内候选节点的插入顺序。"""
        state_host = str(state.get("host_node_id") or "")
        if state_host and node.host_node_id and state_host != node.host_node_id:
            return False
        explicit_id = state.get("node_id")
        if isinstance(explicit_id, str):
            if explicit_id != node.node_id:
                return False
            if not any(state.get(k) for k in ("elements", "all_texts", "activity", "view_controller")):
                return True
        return self._node_match_score(state, node) is not None

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
                node = self.nodes[explicit_id]
                return node if self.state_matches_node(page_state, node) else None

            candidates = [(score, node) for node in self.nodes.values()
                          if (score := self._node_match_score(page_state, node)) is not None]
            if candidates:
                return max(candidates, key=lambda item: item[0])[1]

        return None

    @staticmethod
    def _is_volatile_signature_element(el: dict[str, Any], label_val: str) -> bool:
        """判断元素是否属于易变动态内容（如轮播搜索词、输入框、图片文件名、动态计数、时间戳、瀑布流卡片标题），避免污染页面签名。"""
        import re

        el_type = str(el.get("type") or "").strip().lower()
        if el_type in ("input", "scroll_container", "list", "pager"):
            return True

        res_id = str(el.get("resource_id") or el.get("identifier") or "").strip().lower()
        volatile_id_tokens = (
            "search_text",
            "search_hint",
            "search_edit",
            "edit_",
            "_edit",
            "watched",
            "liked",
            "count",
            "tvtitle",
            "video_title",
            "live_title",
            "nickname",
            "user_name",
            "tvarea",
            "text_corner",
            "danmaku",
            "duration",
            "timestamp",
            "play_time",
            "text_time",
        )
        if res_id and any(tok in res_id for tok in volatile_id_tokens):
            return True

        clean = label_val.strip()
        if "search" in res_id and clean not in ("搜索", "Search") and not clean.startswith("[搜索"):
            return True
        if not clean or clean == res_id:
            if el_type in ("view", "image", "scroll_container", "list", "pager"):
                return True
        # 过滤图片 URL / 哈希文件名或 @用户名
        if clean.startswith("@") or "@" in clean or any(ext in clean.lower() for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
            return True
        # 过滤纯数字、金额、百分比与动态计数文本
        if re.match(r"^[\d\.\+\-\:\s/万亿kKwW%¥￥$]+$", clean):
            return True
        if re.match(r"^[\d\.]+[万亿kKwW]?(关注|粉丝|获赞|赞|播放|浏览|阅读|评论|弹幕|人气|人正在看|人付款|件).*$", clean):
            return True
        if any(
            tok in clean
            for tok in (
                "分钟前",
                "小时前",
                "天前",
                "秒后关闭",
                "IP属地",
                "用户名",
                "点赞",
                "喜欢",
                "评论",
                "收藏",
                "分享",
                "音乐，",
                "原声",
                "作者声明",
                "展开>",
            )
        ):
            return True
        return False

    def infer_node_from_state(
        self, page_state: dict[str, Any], node_id: Optional[str] = None
    ) -> UINode:
        """从实时页面状态字典推断并生成 UINode（自动提取语义名、层次化类型、Fragment 与结构骨架）。"""
        activity_or_vc = self._extract_activity_str(page_state)
        active_fragment = self._extract_active_fragment(page_state)
        id_skeleton, structure_hash = self._extract_id_skeleton(page_state)
        node_type, semantic_name = self._infer_semantic_metadata(page_state)
        host_node_id = str(page_state.get("host_node_id") or "")
        scroll_pages_stitched = int(page_state.get("scroll_pages_stitched") or 1)

        raw_elements = page_state.get("elements") or []
        interactive_elements: list[dict[str, Any]] = []
        stable_labels: list[str] = []
        fallback_labels: list[str] = []

        if isinstance(raw_elements, list):
            for el in raw_elements:
                if isinstance(el, dict):
                    interactive_elements.append(dict(el))
                    for key in (
                        "text",
                        "label",
                        "content_desc",
                        "resource_id",
                        "identifier",
                        "name",
                    ):
                        val = el.get(key)
                        if isinstance(val, str) and val.strip():
                            clean = val.strip()
                            if self._is_volatile_signature_element(el, clean):
                                if clean not in fallback_labels and clean not in stable_labels:
                                    fallback_labels.append(clean)
                            else:
                                if clean not in stable_labels:
                                    stable_labels.append(clean)
                            break

        all_texts = page_state.get("all_texts") or []
        if isinstance(all_texts, list):
            for txt in all_texts:
                if isinstance(txt, str) and txt.strip():
                    clean = txt.strip()
                    if clean not in stable_labels and clean not in fallback_labels:
                        fallback_labels.append(clean)

        extracted_labels = stable_labels + [
            x for x in fallback_labels if x not in stable_labels
        ]
        required_elements = extracted_labels[:4]

        resolved_id = node_id or (
            page_state.get("node_id")
            if isinstance(page_state.get("node_id"), str)
            else None
        )
        if not resolved_id:
            fingerprint_raw = json.dumps([
                activity_or_vc, active_fragment, host_node_id, node_type, semantic_name,
                self._selected_labels(raw_elements), required_elements, id_skeleton,
            ], ensure_ascii=False)
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
            semantic_name=semantic_name,
            node_type=node_type,
            host_node_id=host_node_id,
            active_fragment=active_fragment,
            structure_hash=structure_hash,
            id_skeleton=id_skeleton,
            scroll_pages_stitched=scroll_pages_stitched,
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
            stitched_cnt = int(state.get("scroll_pages_stitched") or 1)
            if stitched_cnt > matched.scroll_pages_stitched:
                matched.scroll_pages_stitched = stitched_cnt
                raw_els = state.get("elements")
                if isinstance(raw_els, list) and len(raw_els) > len(matched.interactive_elements):
                    matched.interactive_elements = [dict(e) for e in raw_els if isinstance(e, dict)]
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
        src_node = self._resolve_or_infer_node(
            from_state, explicit_node_id=from_node_id
        )
        dst_node = self._resolve_or_infer_node(to_state, explicit_node_id=to_node_id)

        action_type = str(action.get("action_type") or action.get("type") or "tap")
        if "trigger_criteria" in action and isinstance(
            action["trigger_criteria"], dict
        ):
            trigger_criteria = dict(action["trigger_criteria"])
        else:
            trigger_criteria = {
                k: v for k, v in action.items() if k not in ("action_type", "type")
            }

        # 入边触发器语义传播（Incoming Trigger Semantic Propagation）：
        # 当目标页面的 semantic_name 仍为原始 Activity 类名（如通用容器 Activity / WebViewActivity）或底栏/顶栏 Tab 切换时，
        # 自动从触发跳转的按钮/标签文本继承可读语义名，无需任何业务硬编码。
        if action_type == "tap" and src_node.node_id != dst_node.node_id and dst_node.semantic_name != "登录页":
            import re

            raw_trig = str(
                trigger_criteria.get("text")
                or trigger_criteria.get("label")
                or trigger_criteria.get("content_desc")
                or ""
            ).strip()
            clean_trig = self._clean_ui_label(raw_trig)
            clean_trig = re.sub(r"^(评论|分享|收藏|点赞|转发)[\d\.]+[万亿kKwW]?$", r"\1", clean_trig)
            if (
                1 <= len(clean_trig) <= 10
                and not clean_trig.replace(".", "").replace(":", "").isdigit()
            ):
                dst_short_act = (
                    dst_node.activity_or_vc.split(".")[-1].split("/")[-1]
                    if dst_node.activity_or_vc
                    else ""
                )
                is_tab_trigger = bool(
                    action.get("is_bottom_tab")
                    or action.get("is_tab")
                    or "标签" in raw_trig
                    or re.search(r",\d+之\d+", raw_trig)
                )
                if not dst_node.semantic_name or dst_node.semantic_name in (
                    dst_short_act,
                    f"{dst_short_act}弹窗",
                ):
                    if dst_node.node_type == "modal" and not clean_trig.endswith("弹窗"):
                        dst_node.semantic_name = f"{clean_trig}弹窗"
                    else:
                        dst_node.semantic_name = clean_trig
                elif dst_node.node_type == "sub_tab" and (is_tab_trigger or clean_trig == "侧边栏"):
                    dst_node.semantic_name = clean_trig

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
            "version": "1.1",
            "metadata": dict(self.metadata),
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

    def to_mermaid(
        self,
        root_node_id: Optional[str] = None,
        *,
        mode: str = "compact",
        detailed: Optional[bool] = None,
        max_elements_per_node: int = 55,
    ) -> str:
        """导出为 Mermaid 有向图 / 流程图描述文本（支持语义名、层次化节点形态、拼接滚屏标注与等价边展示）。"""
        is_detailed = (
            detailed
            if detailed is not None
            else str(mode).strip().lower() in ("detailed", "detail", "full", "verbose")
        )
        lines = ["graph TD"]
        if not self.nodes:
            lines.append('    EmptyGraph["空图谱 (无页面节点)"]')
            return "\n".join(lines)

        def _sanitize(text: str) -> str:
            return html.escape(str(text).replace("\r", " ").replace("\n", " ")
                               .replace("|", "/").strip(), quote=True)

        reserved = {"end", "subgraph", "graph", "style", "default", "flowchart"}
        used_ids: set[str] = set()
        mermaid_ids: dict[str, str] = {}
        for index, nid in enumerate(sorted(self.nodes)):
            safe = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nid)) and nid.lower() not in reserved
            mid = nid if safe else f"ui_node_{index}"
            while mid in used_ids or (mid in self.nodes and mid != nid):
                mid += "_"
            mermaid_ids[nid] = mid
            used_ids.add(mid)

        # 节点定义
        for nid, node in sorted(self.nodes.items(), key=lambda x: x[0]):
            act = (
                node.activity_or_vc.split(".")[-1].split("/")[-1]
                if node.activity_or_vc
                else ""
            )
            mid = mermaid_ids[nid]
            sem = node.semantic_name or ""
            ntype = node.node_type or "page"
            type_tag = "[弹窗]" if ntype == "modal" else ("[Tab]" if ntype == "sub_tab" else "")

            if not is_detailed:
                if sem and sem != act:
                    label = f"{sem} {type_tag} ({nid})".replace("  ", " ")
                else:
                    label = f"{nid} ({act})" if act else nid
                safe_label = _sanitize(label)
                if ntype == "modal":
                    lines.append(f'    {mid}{{{{"{safe_label}"}}}}')
                else:
                    lines.append(f'    {mid}["{safe_label}"]')
            else:
                title_prefix = f"{sem} {type_tag} — " if sem else ""
                header = f"<b>{_sanitize(title_prefix)}{_sanitize(nid)}</b>"
                sub_lines: list[str] = [header]
                if node.activity_or_vc:
                    act_line = f"Activity/VC: {_sanitize(node.activity_or_vc)}"
                    if node.active_fragment:
                        act_line += f" (Fragment: {_sanitize(node.active_fragment)})"
                    sub_lines.append(act_line)
                if node.scroll_pages_stitched > 1:
                    sub_lines.append(f"视口拼接: 已合成 {node.scroll_pages_stitched} 屏控件")
                if node.page_signature and node.page_signature.required_elements:
                    reqs = ", ".join(
                        _sanitize(r) for r in node.page_signature.required_elements[:4]
                    )
                    sub_lines.append(f"特征: [{reqs}]")

                page0_lines: list[str] = []
                scrolled_lines: list[str] = []
                seen_el: set[str] = set()
                for el in node.interactive_elements or []:
                    if not isinstance(el, dict):
                        continue
                    el_type = _sanitize(
                        el.get("type") or el.get("action_type") or "view"
                    )
                    el_text = _sanitize(
                        el.get("text")
                        or el.get("label")
                        or el.get("content_desc")
                        or ""
                    )
                    el_id = _sanitize(
                        el.get("resource_id") or el.get("identifier") or ""
                    )
                    if not el_text and not el_id:
                        continue
                    if len(el_text) > 36:
                        el_text = el_text[:33] + "..."
                    dedup_key = f"{el_type}|{el_text}|{el_id}"
                    if dedup_key in seen_el:
                        continue
                    seen_el.add(dedup_key)

                    parts = [f"[{el_type}]"]
                    scroll_pg = int(el.get("scroll_page") or 0)
                    if scroll_pg > 0:
                        parts.append(f"[第{scroll_pg + 1}屏]")
                    if el_text:
                        parts.append(el_text)
                    if el_id:
                        parts.append(f"(id: {el_id})")
                    bds = el.get("bounds")
                    if isinstance(bds, (list, tuple)) and len(bds) == 4:
                        parts.append(f"@{list(bds)}")
                    line_item = "• " + " ".join(parts)
                    if scroll_pg > 0:
                        scrolled_lines.append(line_item)
                    else:
                        page0_lines.append(line_item)

                p0_quota = max(15, max_elements_per_node - min(len(scrolled_lines), 15))
                elem_lines = page0_lines[:p0_quota] + scrolled_lines[:15]

                if elem_lines:
                    sub_lines.append("--- 页面组件清单 ---")
                    sub_lines.extend(elem_lines)
                full_label = "<br/>".join(sub_lines)
                if ntype == "modal":
                    lines.append(f'    {mid}{{{{"{full_label}"}}}}')
                else:
                    lines.append(f'    {mid}["{full_label}"]')

        # 边定义
        for edge in self.edges:
            crit = edge.trigger_criteria or {}
            label_parts: list[str] = []
            for k in ("text", "label", "resource_id", "content_desc", "key"):
                if k in crit and crit[k]:
                    label_parts.append(_sanitize(str(crit[k])))
                    break
            action_desc = (
                f"{_sanitize(edge.action_type)}: {label_parts[0]}"
                if label_parts
                else _sanitize(edge.action_type)
            )
            equiv = crit.get("equivalent_triggers")
            if isinstance(equiv, list) and equiv:
                equiv_str = "/".join(_sanitize(str(x)) for x in equiv[:3])
                action_desc = f"{action_desc} (等价:{equiv_str})"
            if is_detailed:
                res_id = _sanitize(
                    str(crit.get("resource_id") or crit.get("identifier") or "")
                )
                if res_id and (not label_parts or res_id != label_parts[0]):
                    action_desc = f"{action_desc} [id={res_id}]"
                scroll_pg = int(crit.get("scroll_page") or 0)
                if scroll_pg > 0:
                    action_desc = f"{action_desc} [scroll={scroll_pg}]"
            rate_pct = f"{int(round(edge.success_rate * 100))}%"
            edge_label = f"{action_desc} ({rate_pct})"
            if edge.source_id in mermaid_ids and edge.target_id in mermaid_ids:
                lines.append(f'    {mermaid_ids[edge.source_id]} -->|"{edge_label}"| {mermaid_ids[edge.target_id]}')

        return "\n".join(lines)

    def to_mindmap_dict(self, root_node_id: Optional[str] = None) -> dict[str, Any]:
        """导出为脑图/树形结构的字典（可供 ECharts Tree、Markmap 或前端渲染）。"""
        if not self.nodes:
            return {"id": "root", "name": "应用页面脑图 (空)", "children": []}

        # 选取根节点
        root_id = root_node_id
        if not root_id or root_id not in self.nodes:
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
            act = (
                node.activity_or_vc.split(".")[-1].split("/")[-1]
                if node and node.activity_or_vc
                else ""
            )
            sem = node.semantic_name if node and node.semantic_name else ""
            if sem and sem != act:
                display_name = f"{sem} ({current_id})"
            else:
                display_name = f"{current_id} ({act})" if act else current_id

            tree_node: dict[str, Any] = {
                "id": current_id,
                "name": display_name,
                "semantic_name": sem,
                "node_type": node.node_type if node else "page",
                "activity": node.activity_or_vc if node else "",
                "active_fragment": node.active_fragment if node else "",
                "interactive_count": len(node.interactive_elements) if node else 0,
                "scroll_pages_stitched": node.scroll_pages_stitched if node else 1,
                "children": [],
            }
            if current_id in visited or depth > 10:
                tree_node["is_loop"] = True
                return tree_node

            visited.add(current_id)
            for edge in adj.get(current_id, []):
                child_tree = build_tree(edge.target_id, depth + 1)
                crit = edge.trigger_criteria or {}
                crit_text = str(
                    crit.get("text")
                    or crit.get("label")
                    or crit.get("resource_id")
                    or ""
                )
                child_tree["edge"] = {
                    "action_type": edge.action_type,
                    "target_text": crit_text,
                    "success_rate": edge.success_rate,
                }
                tree_node["children"].append(child_tree)

            return tree_node

        tree_root = build_tree(root_id)

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
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return cls(nodes=nodes, edges=edges, metadata=metadata)
