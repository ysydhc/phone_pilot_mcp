"""UIKnowledgeBase — UI 状态图持久化知识库管理。

封装 ``UIKnowledgeGraph`` 的文件持久化、转移记录、最短路径查询与摘要统计。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional, Union

from phone_pilot.core.log import _log
from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph, UINode
from phone_pilot.core.ui.page import PageSignature


class UIKnowledgeBase:
    """持久化认知 UI 知识库。"""

    def __init__(
        self,
        storage_path: Optional[Union[str, pathlib.Path]] = None,
        graph: Optional[UIKnowledgeGraph] = None,
        auto_save: bool = True,
    ) -> None:
        self.storage_path: Optional[pathlib.Path] = (
            pathlib.Path(storage_path).expanduser().resolve()
            if storage_path is not None
            else None
        )
        self.auto_save = auto_save
        if graph is not None:
            self.graph = graph
        elif self.storage_path is not None and self.storage_path.exists():
            try:
                self.graph = UIKnowledgeGraph.load_graph_json(self.storage_path)
            except Exception as exc:
                _log(f"[UIKnowledgeBase] Failed to load existing graph from {self.storage_path}: {exc}")
                self.graph = UIKnowledgeGraph()
        else:
            self.graph = UIKnowledgeGraph()

    def record(
        self,
        from_state: Union[dict[str, Any], UINode],
        action: dict[str, Any],
        to_state: Union[dict[str, Any], UINode],
        *,
        from_node_id: Optional[str] = None,
        to_node_id: Optional[str] = None,
        success: bool = True,
    ) -> tuple[UINode, UIEdge, UINode]:
        """记录一次页面转移并按配置自动持久化。"""
        result = self.graph.record_transition(
            from_state=from_state,
            action=action,
            to_state=to_state,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            success=success,
        )
        if self.auto_save and self.storage_path is not None:
            self.save()
        return result

    def save(self, path: Optional[Union[str, pathlib.Path]] = None) -> dict[str, Any]:
        """保存知识图谱至 JSON 文件并返回序列化字典。"""
        target = pathlib.Path(path).expanduser().resolve() if path is not None else self.storage_path
        if target is not None:
            self.storage_path = target
        return self.graph.export_graph_json(target)

    def load(self, path: Optional[Union[str, pathlib.Path]] = None) -> UIKnowledgeGraph:
        """从 JSON 文件加载知识图谱。"""
        target = pathlib.Path(path).expanduser().resolve() if path is not None else self.storage_path
        if target is None:
            raise ValueError("No storage_path specified for UIKnowledgeBase.load()")
        self.storage_path = target
        if target.exists():
            self.graph = UIKnowledgeGraph.load_graph_json(target)
        return self.graph

    def find_path(
        self,
        target: Union[PageSignature, str],
        current_state: Union[dict[str, Any], str],
    ) -> list[UIEdge]:
        """规划从当前状态到目标页面的最优转移边列表。"""
        return self.graph.find_path_to(target=target, current_state=current_state)

    def summary(self) -> dict[str, Any]:
        """返回当前知识库节点与边的统计摘要。"""
        return {
            "node_count": len(self.graph.nodes),
            "edge_count": len(self.graph.edges),
            "nodes": list(self.graph.nodes.keys()),
            "storage_path": str(self.storage_path) if self.storage_path else None,
        }
