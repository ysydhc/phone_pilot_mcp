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
