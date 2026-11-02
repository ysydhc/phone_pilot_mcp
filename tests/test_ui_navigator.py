"""Unit tests for UINavigator (topological pathfinding & self-healing UI navigation)."""

from __future__ import annotations

from typing import Any

from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph, UINode
from phone_pilot.core.ui.navigator import UINavigator
from phone_pilot.core.ui.page import PageSignature


def _build_sample_graph() -> tuple[UIKnowledgeGraph, dict[str, dict[str, Any]]]:
    """构建测试用多跳页面知识图谱与对应页面状态字典。"""
    states: dict[str, dict[str, Any]] = {
        "home": {
            "activity": "com.demo.MainActivity",
            "elements": [
                {"text": "首页", "bounds": [40, 80, 200, 140]},
                {"text": "广告列表", "bounds": [100, 300, 400, 380]},
                {"text": "快捷入口", "bounds": [100, 420, 400, 500]},
            ],
        },
        "ad_list": {
            "activity": "com.demo.AdListActivity",
            "elements": [
                {"text": "广告列表页", "bounds": [40, 80, 260, 140]},
                {"text": "激励视频", "bounds": [100, 320, 420, 400]},
            ],
        },
        "shortcut_hub": {
            "activity": "com.demo.ShortcutActivity",
            "elements": [
                {"text": "快捷中心", "bounds": [40, 80, 260, 140]},
                {"text": "进入激励视频", "bounds": [100, 340, 440, 420]},
            ],
        },
        "reward_detail": {
            "activity": "com.demo.RewardDetailActivity",
            "elements": [
                {"text": "激励视频详情", "bounds": [40, 80, 300, 140]},
                {"text": "播放广告", "bounds": [120, 500, 460, 580]},
            ],
        },
    }

    graph = UIKnowledgeGraph()
    graph.add_or_update_node(
        UINode(
            node_id="home",
            page_signature=PageSignature(
                name="home",
                activity_or_controller="MainActivity",
                required_elements=["首页"],
            ),
        )
    )
    graph.add_or_update_node(
        UINode(
            node_id="ad_list",
            page_signature=PageSignature(
                name="ad_list",
                activity_or_controller="AdListActivity",
                required_elements=["广告列表页"],
            ),
        )
    )
    graph.add_or_update_node(
        UINode(
            node_id="shortcut_hub",
            page_signature=PageSignature(
                name="shortcut_hub",
                activity_or_controller="ShortcutActivity",
                required_elements=["快捷中心"],
            ),
        )
    )
    graph.add_or_update_node(
        UINode(
            node_id="reward_detail",
            page_signature=PageSignature(
                name="reward_detail",
                activity_or_controller="RewardDetailActivity",
                required_elements=["激励视频详情"],
            ),
        )
    )

    graph.edges.extend(
        [
            UIEdge(
                source_id="home",
                target_id="ad_list",
                action_type="tap",
                trigger_criteria={"text": "广告列表"},
                success_rate=1.0,
                attempts=2,
                successes=2,
            ),
            UIEdge(
                source_id="ad_list",
                target_id="reward_detail",
                action_type="tap",
                trigger_criteria={"text": "激励视频"},
                success_rate=1.0,
                attempts=2,
                successes=2,
            ),
        ]
    )
    return graph, states


def test_navigate_multi_hop_success() -> None:
    """验证多跳路径 (home -> ad_list -> reward_detail) 自动寻路与动态坐标点击成功。"""
    graph, states = _build_sample_graph()
    current = {"name": "home"}
    tapped_coords: list[tuple[int, int]] = []

    class DummyDriver:
        def get_page_state(self) -> dict[str, Any]:
            return states[current["name"]]

        def tap(self, x: int, y: int) -> dict[str, Any]:
            tapped_coords.append((x, y))
            if current["name"] == "home":
                current["name"] = "ad_list"
            elif current["name"] == "ad_list":
                current["name"] = "reward_detail"
            return {"ok": True}

    nav = UINavigator(graph, driver=DummyDriver())
    res = nav.navigate_to("reward_detail")

    assert res["ok"] is True
    assert res["reason"] == "reached_target"
    assert res["hops_executed"] == 2
    assert res["heals_used"] == 0
    assert res["final_node_id"] == "reward_detail"
    assert [step["target_id"] for step in res["path_taken"]] == ["ad_list", "reward_detail"]
    # 验证点击坐标由元素 bounds 动态计算中心点得出: (100+400)//2=250, (300+380)//2=340; (100+420)//2=260, (320+400)//2=360
    assert tapped_coords == [(250, 340), (260, 360)]


def test_already_at_target_zero_hops() -> None:
    """验证当前状态已匹配目标页面时零跳直接返回 already_at_target。"""
    graph, states = _build_sample_graph()
    nav = UINavigator(
        graph,
        state_provider=lambda: states["reward_detail"],
    )

    res = nav.navigate_to(
        PageSignature(
            name="reward_detail",
            activity_or_controller="RewardDetailActivity",
            required_elements=["激励视频详情"],
        )
    )
    assert res["ok"] is True
    assert res["hops_executed"] == 0
    assert res["heals_used"] == 0
    assert res["path_taken"] == []
    assert res["final_node_id"] == "reward_detail"
    assert res["reason"] == "already_at_target"


def test_self_healing_popup_dismissal() -> None:
    """验证导航途中遭遇意外弹窗遮挡时，自动触发 Level 1 弹窗关闭自愈并继续到达目标。"""
    graph, states = _build_sample_graph()
    popup_state: dict[str, Any] = {
        "activity": "com.demo.MainActivity",
        "elements": [
            {"text": "青少年模式提示", "bounds": [80, 400, 640, 700]},
            {"text": "关闭", "bounds": [260, 720, 460, 780]},
        ],
    }

    state_holder = {"state": states["home"], "popup_shown": False}

    def action_executor(edge: UIEdge, state: dict[str, Any]) -> dict[str, Any]:
        # 第一次从 home 点击“广告列表”时，被意外弹窗拦截
        if edge.source_id == "home" and edge.target_id == "ad_list" and not state_holder["popup_shown"]:
            state_holder["popup_shown"] = True
            state_holder["state"] = popup_state
            return {"ok": True}
        # 自愈器点击“关闭”弹窗后，露出广告列表页
        if edge.trigger_criteria.get("is_popup_dismiss"):
            state_holder["state"] = states["ad_list"]
            return {"ok": True}
        if edge.source_id == "ad_list" and edge.target_id == "reward_detail":
            state_holder["state"] = states["reward_detail"]
            return {"ok": True}
        return {"ok": False}

    nav = UINavigator(
        graph,
        state_provider=lambda: state_holder["state"],
        action_executor=action_executor,
    )
    res = nav.navigate_to("reward_detail", max_heals=2)

    assert res["ok"] is True
    assert res["reason"] == "reached_target"
    assert res["heals_used"] == 1
    assert res["final_node_id"] == "reward_detail"
    assert any(ev["type"] == "popup_dismiss" and ev["label"] == "关闭" for ev in res["healing_events"])


def test_self_healing_dynamic_repath() -> None:
    """验证页面转移发生意外漂移到另一已知节点时，触发 Level 3 动态重规划路径到达终点。"""
    graph, states = _build_sample_graph()
    # 增加备用边: shortcut_hub -> reward_detail
    backup_edge = UIEdge(
        source_id="shortcut_hub",
        target_id="reward_detail",
        action_type="tap",
        trigger_criteria={"text": "进入激励视频"},
        success_rate=0.9,
        attempts=5,
        successes=4,
    )
    graph.edges.append(backup_edge)

    primary_edge = graph.edges[0]  # home -> ad_list
    initial_rate = primary_edge.success_rate
    state_holder = {"state": states["home"]}

    def action_executor(edge: UIEdge, state: dict[str, Any]) -> dict[str, Any]:
        if edge.source_id == "home" and edge.target_id == "ad_list":
            # 模拟页面漂移：本应进入 ad_list，实际误入 shortcut_hub
            state_holder["state"] = states["shortcut_hub"]
            return {"ok": True}
        if edge.source_id == "shortcut_hub" and edge.target_id == "reward_detail":
            state_holder["state"] = states["reward_detail"]
            return {"ok": True}
        return {"ok": False}

    nav = UINavigator(
        graph,
        state_provider=lambda: state_holder["state"],
        action_executor=action_executor,
    )
    res = nav.navigate_to("reward_detail", max_heals=2)

    assert res["ok"] is True
    assert res["reason"] == "reached_target"
    assert res["heals_used"] == 1
    assert res["final_node_id"] == "reward_detail"
    assert primary_edge.success_rate < initial_rate
    assert any(
        ev["type"] == "dynamic_repath" and ev["from_node"] == "shortcut_hub"
        for ev in res["healing_events"]
    )
    assert [step["target_id"] for step in res["path_taken"]] == ["reward_detail"]


def test_navigate_unreachable_updates_edge_stats() -> None:
    """验证目标不可达或边转移持续失败时返回 ok=False 并下调失败边的成功率统计。"""
    graph, states = _build_sample_graph()
    failing_edge = graph.edges[0]  # home -> ad_list
    before_attempts = failing_edge.attempts
    before_rate = failing_edge.success_rate

    unknown_state: dict[str, Any] = {
        "activity": "com.demo.CrashErrorActivity",
        "elements": [{"text": "网络异常页面", "bounds": [40, 200, 300, 260]}],
    }
    state_holder = {"state": states["home"]}

    def action_executor(edge: UIEdge, state: dict[str, Any]) -> dict[str, Any]:
        state_holder["state"] = unknown_state
        return {"ok": False}

    nav = UINavigator(
        graph,
        state_provider=lambda: state_holder["state"],
        action_executor=action_executor,
    )
    res = nav.navigate_to("reward_detail", max_heals=1)

    assert res["ok"] is False
    assert "drift_unrecovered_at_ad_list" in res["reason"]
    assert failing_edge.attempts > before_attempts
    assert failing_edge.success_rate < before_rate

    # 同时验证完全无路径节点直接返回 no_path_found
    res_no_path = nav.navigate_to("non_existent_node", current_state=states["home"])
    assert res_no_path["ok"] is False
    assert res_no_path["reason"] == "no_path_found"
