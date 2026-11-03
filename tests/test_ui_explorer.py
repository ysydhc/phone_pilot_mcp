"""Unit tests for UIKnowledgeGraph Mermaid/Mindmap export and AppExplorer."""

from __future__ import annotations

import json
import pytest

from phone_pilot.core.ui.explorer import AppExplorer
from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph, UINode
from phone_pilot.core.ui.page import PageSignature
from phone_pilot.mcp._evolution_tools import (
    phone_load_ui_graph,
    phone_record_transition,
    phone_save_ui_graph,
)


def test_graph_to_mermaid_and_mindmap():
    graph = UIKnowledgeGraph()
    node_a = UINode(
        node_id="home_page",
        page_signature=PageSignature(name="home_page", activity_or_controller="MainActivity"),
        activity_or_vc="com.example.MainActivity",
    )
    node_b = UINode(
        node_id="settings_page",
        page_signature=PageSignature(name="settings_page", activity_or_controller="SettingsActivity"),
        activity_or_vc="com.example.SettingsActivity",
    )
    graph.add_or_update_node(node_a)
    graph.add_or_update_node(node_b)

    graph.record_transition(
        node_a,
        {"action_type": "tap", "trigger_criteria": {"text": "设置"}},
        node_b,
        from_node_id="home_page",
        to_node_id="settings_page",
        success=True,
    )

    mermaid = graph.to_mermaid(root_node_id="home_page")
    assert "graph TD" in mermaid
    assert "home_page" in mermaid
    assert "settings_page" in mermaid
    assert "tap: 设置" in mermaid

    mindmap = graph.to_mindmap_dict(root_node_id="home_page")
    assert mindmap["id"] == "home_page"
    assert len(mindmap["children"]) == 1
    assert mindmap["children"][0]["id"] == "settings_page"
    assert mindmap["children"][0]["edge"]["action_type"] == "tap"


def test_app_explorer_blacklist_filtering():
    explorer = AppExplorer()
    state = {
        "ok": True,
        "elements": [
            {"text": "个人中心", "resource_id": "btn_profile", "bounds": [10, 10, 50, 50]},
            {"text": "注销账号", "resource_id": "btn_delete", "bounds": [10, 60, 50, 100]},
            {"text": "安全退出", "resource_id": "btn_logout", "bounds": [10, 110, 50, 150]},
            {"text": "系统设置", "resource_id": "btn_settings", "bounds": [10, 160, 50, 200]},
        ],
    }

    candidates = explorer.filter_interactive_candidates(state, max_per_page=10)
    texts = [c["text"] for c in candidates]
    assert "个人中心" in texts
    assert "系统设置" in texts
    assert "注销账号" not in texts
    assert "安全退出" not in texts


def test_app_explorer_explore_mock_roaming():
    # 模拟三状态跳转: Home (点击设置) -> Settings (点击关于) -> About
    current_page = ["Home"]

    def mock_state_provider():
        p = current_page[0]
        if p == "Home":
            return {
                "ok": True,
                "node_id": "page_home",
                "activity": "MainActivity",
                "elements": [
                    {"text": "设置", "resource_id": "btn_settings", "bounds": [100, 200, 300, 250]}
                ],
            }
        elif p == "Settings":
            return {
                "ok": True,
                "node_id": "page_settings",
                "activity": "SettingsActivity",
                "elements": [
                    {"text": "关于我们", "resource_id": "btn_about", "bounds": [100, 300, 300, 350]}
                ],
            }
        else:
            return {
                "ok": True,
                "node_id": "page_about",
                "activity": "AboutActivity",
                "elements": [],
            }

    def mock_action_executor(action):
        crit = action.get("trigger_criteria", {})
        txt = crit.get("text")
        if txt == "设置":
            current_page[0] = "Settings"
            return True
        elif txt == "关于我们":
            current_page[0] = "About"
            return True
        return False

    def mock_back_executor():
        if current_page[0] == "About":
            current_page[0] = "Settings"
            return True
        elif current_page[0] == "Settings":
            current_page[0] = "Home"
            return True
        return False

    explorer = AppExplorer(
        state_provider=mock_state_provider,
        action_executor=mock_action_executor,
        back_executor=mock_back_executor,
    )

    res = explorer.explore(max_depth=3, max_pages=10, step_delay_s=0.0)
    assert res["ok"] is True
    assert res["nodes_count"] >= 3
    assert res["edges_count"] >= 2
    assert "graph TD" in res["mermaid"]
    assert "mindmap" in res
    assert res["mindmap"]["id"] == "page_home"


@pytest.mark.asyncio
async def test_mcp_graph_tools(tmp_path):
    graph_file = str(tmp_path / "test_graph.json")

    # 1. 记录转移
    rec_res = await phone_record_transition(
        action={"action_type": "tap", "trigger_criteria": {"text": "登录"}},
        from_state={"node_id": "welcome_page", "activity": "WelcomeActivity"},
        to_state={"node_id": "login_page", "activity": "LoginActivity"},
        graph_path=graph_file,
    )
    assert rec_res["ok"] is True
    assert rec_res["total_nodes"] == 2
    assert rec_res["total_edges"] == 1

    # 2. 保存图谱
    save_res = await phone_save_ui_graph(path=graph_file)
    assert save_res["ok"] is True
    assert save_res["nodes_count"] == 2

    # 3. 加载图谱
    load_res = await phone_load_ui_graph(path=graph_file)
    assert load_res["ok"] is True
    assert load_res["nodes_count"] == 2
    assert "graph TD" in load_res["mermaid"]
    assert load_res["mindmap"]["id"] in ("welcome_page", "login_page")
