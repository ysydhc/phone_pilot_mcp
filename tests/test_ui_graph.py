"""UIGraph DAG 引擎单元测试。"""

import pytest

from phone_pilot.core.ui.graph import graph


# ---------------------------------------------------------------------------
# 基础功能
# ---------------------------------------------------------------------------


class TestGraphBasic:
    """基本节点添加与执行。"""

    def test_empty_graph(self):
        g = graph()
        result = g.run()
        assert result["ok"] is True
        assert result["total"] == 0

    def test_single_node_ok(self):
        g = graph()
        g.add("a", lambda: {"ok": True, "value": 42})
        result = g.run()
        assert result["ok"] is True
        assert result["passed"] == 1
        assert result["nodes"]["a"]["ok"] is True

    def test_single_node_fail(self):
        g = graph()
        g.add("a", lambda: {"ok": False, "error": "boom"})
        result = g.run()
        assert result["ok"] is False
        assert result["failed"] == 1
        assert result["nodes"]["a"]["ok"] is False


class TestGraphDependencies:
    """依赖关系与拓扑排序。"""

    def test_linear_chain(self):
        """a → b → c 线性依赖。"""
        order = []
        g = graph()
        g.add("a", lambda: (order.append("a"), {"ok": True})[-1])
        g.add("b", lambda: (order.append("b"), {"ok": True})[-1], after="a")
        g.add("c", lambda: (order.append("c"), {"ok": True})[-1], after="b")
        result = g.run()
        assert order == ["a", "b", "c"]
        assert result["ok"] is True
        assert result["passed"] == 3

    def test_fan_out(self):
        """a → [b, c, d] 扇出：b/c/d 都依赖 a。"""
        order = []
        g = graph()
        g.add("a", lambda: (order.append("a"), {"ok": True})[-1])
        g.add("b", lambda: (order.append("b"), {"ok": True})[-1], after="a")
        g.add("c", lambda: (order.append("c"), {"ok": True})[-1], after="a")
        g.add("d", lambda: (order.append("d"), {"ok": True})[-1], after="a")
        result = g.run()
        assert order[0] == "a"
        assert set(order[1:]) == {"b", "c", "d"}
        assert result["passed"] == 4

    def test_fan_in(self):
        """[a, b] → c 扇入：c 依赖 a 和 b。"""
        order = []
        g = graph()
        g.add("a", lambda: (order.append("a"), {"ok": True})[-1])
        g.add("b", lambda: (order.append("b"), {"ok": True})[-1])
        g.add("c", lambda: (order.append("c"), {"ok": True})[-1], after=["a", "b"])
        result = g.run()
        assert "a" in order[:2]
        assert "b" in order[:2]
        assert order[-1] == "c"
        assert result["passed"] == 3

    def test_diamond(self):
        """a → [b, c] → d 钻石依赖。"""
        order = []
        g = graph()
        g.add("a", lambda: (order.append("a"), {"ok": True})[-1])
        g.add("b", lambda: (order.append("b"), {"ok": True})[-1], after="a")
        g.add("c", lambda: (order.append("c"), {"ok": True})[-1], after="a")
        g.add("d", lambda: (order.append("d"), {"ok": True})[-1], after=["b", "c"])
        result = g.run()
        assert order[0] == "a"
        assert order[-1] == "d"
        assert result["passed"] == 4


class TestGraphFailureHandling:
    """失败处理：同级不阻塞 + 父失败跳过子。"""

    def test_sibling_failure_no_block(self):
        """b 失败不阻塞同级 c。"""
        g = graph()
        g.add("a", lambda: {"ok": True})
        g.add("b", lambda: {"ok": False}, after="a")
        g.add("c", lambda: {"ok": True}, after="a")
        result = g.run()
        assert result["nodes"]["b"]["status"] == "failed"
        assert result["nodes"]["c"]["status"] == "ok"
        assert result["failed"] == 1
        assert result["passed"] == 2

    def test_parent_failure_skips_child(self):
        """a 失败 → b 被跳过。"""
        g = graph()
        g.add("a", lambda: {"ok": False})
        g.add("b", lambda: {"ok": True}, after="a")
        result = g.run()
        assert result["nodes"]["a"]["status"] == "failed"
        assert result["nodes"]["b"]["status"] == "skipped"
        assert result["skipped"] == 1
        assert result["failed"] == 1

    def test_exception_counts_as_failure(self):
        """节点抛异常视为失败。"""

        def boom():
            raise RuntimeError("test error")

        g = graph()
        g.add("a", boom)
        result = g.run()
        assert result["nodes"]["a"]["status"] == "failed"
        assert "exception" in str(result["nodes"]["a"])

    def test_cascade_skip(self):
        """a 失败 → b 跳过 → c 跳过（级联）。"""
        g = graph()
        g.add("a", lambda: {"ok": False})
        g.add("b", lambda: {"ok": True}, after="a")
        g.add("c", lambda: {"ok": True}, after="b")
        result = g.run()
        assert result["nodes"]["a"]["status"] == "failed"
        assert result["nodes"]["b"]["status"] == "skipped"
        assert result["nodes"]["c"]["status"] == "skipped"


class TestGraphValidation:
    """输入校验：重名、缺依赖、环。"""

    def test_duplicate_name(self):
        g = graph()
        g.add("a", lambda: {"ok": True})
        with pytest.raises(ValueError, match="重复"):
            g.add("a", lambda: {"ok": True})

    def test_missing_dependency(self):
        g = graph()
        with pytest.raises(ValueError, match="不存在"):
            g.add("a", lambda: {"ok": True}, after="nonexistent")

    def test_self_cycle(self):
        """不允许依赖自身（添加时 after 引用的节点必须已存在，
        而节点自身还未添加，所以会触发 '依赖不存在' 错误）。"""
        g = graph()
        with pytest.raises(ValueError, match="不存在"):
            g.add("a", lambda: {"ok": True}, after="a")


class TestGraphMisc:
    """其他辅助功能。"""

    def test_node_names(self):
        g = graph()
        g.add("x", lambda: {"ok": True})
        g.add("y", lambda: {"ok": True})
        assert g.node_names() == ["x", "y"]

    def test_len(self):
        g = graph()
        assert len(g) == 0
        g.add("a", lambda: {"ok": True})
        assert len(g) == 1

    def test_repr(self):
        g = graph()
        g.add("a", lambda: {"ok": True})
        assert "a" in repr(g)

    def test_elapsed_ms_recorded(self):
        import time as _time

        g = graph()
        g.add("slow", lambda: (_time.sleep(0.05), {"ok": True})[-1])
        result = g.run()
        assert result["nodes"]["slow"]["elapsed_ms"] >= 40

    def test_non_dict_return(self):
        """callable 返回非 dict 时自动包装。"""
        g = graph()
        g.add("a", lambda: True)
        result = g.run()
        assert result["nodes"]["a"]["ok"] is True


# ---------------------------------------------------------------------------
# Cognitive UI Knowledge Graph & Persistence
# ---------------------------------------------------------------------------


class TestUIKnowledgeGraph:
    """UIKnowledgeGraph 节点匹配、边演进、Dijkstra 寻路与 JSON 持久化测试。"""

    def test_node_and_edge_serialization(self):
        from phone_pilot.core.ui.graph import CognitiveUINode, UIEdge, UINode
        from phone_pilot.core.ui.page import PageSignature

        assert CognitiveUINode is UINode

        sig = PageSignature(
            name="home",
            activity_or_controller=".MainActivity",
            required_elements=["首页", "设置"],
            forbidden_elements=["崩溃"],
        )
        node = UINode(
            node_id="home",
            page_signature=sig,
            activity_or_vc=".MainActivity",
            interactive_elements=[{"text": "设置", "center": [100, 200]}],
            last_seen_timestamp=1700000000.0,
        )
        d = node.to_dict()
        restored = UINode.from_dict(d)
        assert restored.node_id == "home"
        assert restored.page_signature.name == "home"
        assert restored.page_signature.required_elements == ["首页", "设置"]
        assert restored.page_signature.forbidden_elements == ["崩溃"]
        assert restored.activity_or_vc == ".MainActivity"
        assert len(restored.interactive_elements) == 1

        edge = UIEdge(
            source_id="home",
            target_id="settings",
            action_type="tap",
            trigger_criteria={"text": "设置"},
        )
        assert edge.success_rate == 1.0
        edge.record_outcome(False)
        assert edge.attempts == 2
        assert edge.successes == 1
        assert edge.success_rate == 0.5
        edge.record_outcome(True)
        assert edge.attempts == 3
        assert edge.successes == 2
        assert edge.success_rate == round(2 / 3, 4)

        edge_restored = UIEdge.from_dict(edge.to_dict())
        assert edge_restored.source_id == "home"
        assert edge_restored.target_id == "settings"
        assert edge_restored.trigger_criteria == {"text": "设置"}
        assert edge_restored.attempts == 3
        assert edge_restored.successes == 2

    def test_infer_and_match_node_specificity(self):
        from phone_pilot.core.ui.graph import UIKnowledgeGraph, UINode
        from phone_pilot.core.ui.page import PageSignature

        kg = UIKnowledgeGraph()
        general_node = UINode(
            node_id="general_home",
            page_signature=PageSignature(name="general_home", required_elements=["首页"]),
        )
        specific_node = UINode(
            node_id="specific_home",
            page_signature=PageSignature(
                name="specific_home", required_elements=["首页", "推荐", "搜索"]
            ),
        )
        kg.add_or_update_node(general_node)
        kg.add_or_update_node(specific_node)

        state = {
            "activity": {"activity": ".MainActivity", "package": "com.demo"},
            "elements": [
                {"text": "首页"},
                {"text": "推荐"},
                {"text": "搜索"},
            ],
        }
        matched = kg.match_node(state)
        assert matched is not None
        assert matched.node_id == "specific_home"

        assert kg.match_node("general_home") is general_node
        assert (
            kg.match_node(PageSignature(name="specific_home", required_elements=["首页"]))
            is specific_node
        )

        inferred = kg.infer_node_from_state(state)
        assert inferred.activity_or_vc == ".MainActivity"
        assert inferred.page_signature.required_elements == ["首页", "推荐", "搜索"]

    def test_record_transition_and_deduplication(self):
        from phone_pilot.core.ui.graph import UIKnowledgeGraph

        kg = UIKnowledgeGraph()
        home_state = {
            "activity": ".MainActivity",
            "elements": [{"text": "首页"}, {"text": "设置"}],
        }
        settings_state = {
            "activity": ".SettingsActivity",
            "elements": [{"text": "通用设置"}, {"text": "关于我们"}],
        }

        n1, edge1, n2 = kg.record_transition(
            home_state,
            {"action_type": "tap", "text": "设置"},
            settings_state,
            from_node_id="home",
            to_node_id="settings",
            success=True,
        )
        assert n1.node_id == "home"
        assert n2.node_id == "settings"
        assert len(kg.nodes) == 2
        assert len(kg.edges) == 1
        assert edge1.attempts == 1
        assert edge1.success_rate == 1.0

        # 重复相同转移（不传 explicit node_id，验证自动指纹匹配去重）
        n1_b, edge1_b, n2_b = kg.record_transition(
            home_state,
            {"action_type": "tap", "text": "设置"},
            settings_state,
            success=False,
        )
        assert n1_b.node_id == "home"
        assert n2_b.node_id == "settings"
        assert len(kg.nodes) == 2
        assert len(kg.edges) == 1
        assert edge1_b is edge1
        assert edge1.attempts == 2
        assert edge1.success_rate == 0.5

    def test_dijkstra_find_path_to(self):
        from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph, UINode
        from phone_pilot.core.ui.page import PageSignature

        kg = UIKnowledgeGraph()
        for nid, label in [
            ("home", "首页"),
            ("menu", "菜单"),
            ("profile", "个人中心"),
            ("target", "目标页"),
        ]:
            kg.add_or_update_node(
                UINode(
                    node_id=nid,
                    page_signature=PageSignature(name=nid, required_elements=[label]),
                )
            )

        # 路径 A: home -> target 直接转移，但成功率极低 (0.1) => cost = 1.0 - 0.05 + (10 - 1) = 9.95
        kg.edges.append(
            UIEdge(
                source_id="home",
                target_id="target",
                action_type="tap",
                trigger_criteria={"text": "不稳定直达"},
                success_rate=0.1,
                attempts=10,
                successes=1,
            )
        )
        # 路径 B: home -> menu -> target 两跳高可靠 (1.0) => 单跳 cost = 0.5, 两跳总 cost = 1.0
        kg.edges.append(
            UIEdge(
                source_id="home",
                target_id="menu",
                action_type="tap",
                trigger_criteria={"text": "菜单"},
                success_rate=1.0,
            )
        )
        kg.edges.append(
            UIEdge(
                source_id="menu",
                target_id="target",
                action_type="tap",
                trigger_criteria={"text": "目标页"},
                success_rate=1.0,
            )
        )

        path = kg.find_path_to("target", {"elements": [{"text": "首页"}]})
        assert len(path) == 2
        assert [e.target_id for e in path] == ["menu", "target"]

        # 已在目标页时返回空列表
        assert kg.find_path_to("target", "target") == []
        # 不可达节点返回空列表
        assert kg.find_path_to("nonexistent", "home") == []

    def test_export_and_load_json_and_knowledge_base(self):
        import pathlib
        import tempfile

        from phone_pilot.core.ui.graph import UIKnowledgeGraph
        from phone_pilot.core.ui.knowledge_base import UIKnowledgeBase

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = pathlib.Path(tmpdir) / "ui_graph.json"
            kb = UIKnowledgeBase(storage_path=json_path, auto_save=True)

            kb.record(
                {"activity": ".HomeActivity", "elements": [{"text": "首页"}]},
                {"action_type": "tap", "text": "详情"},
                {"activity": ".DetailActivity", "elements": [{"text": "详情页"}]},
                from_node_id="home",
                to_node_id="detail",
            )
            assert json_path.exists()

            info = kb.summary()
            assert info["node_count"] == 2
            assert info["edge_count"] == 1

            kb2 = UIKnowledgeBase(storage_path=json_path)
            assert len(kb2.graph.nodes) == 2
            assert len(kb2.graph.edges) == 1
            path = kb2.find_path("detail", "home")
            assert len(path) == 1
            assert path[0].trigger_criteria == {"text": "详情"}

            loaded = UIKnowledgeGraph.load_graph_json(kb2.save())
            assert set(loaded.nodes.keys()) == {"home", "detail"}

