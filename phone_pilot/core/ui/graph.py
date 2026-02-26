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

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence, Union


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
                result = {"ok": False, "error": {"code": "exception", "message": str(exc)}}
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
