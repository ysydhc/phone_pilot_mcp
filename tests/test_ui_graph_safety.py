"""Regression tests for graph identity, consolidation and safe consumers."""

import pytest

from phone_pilot.core.ui.graph import UIEdge, UIKnowledgeGraph, UINode
from phone_pilot.core.ui.navigator import UINavigator
from phone_pilot.core.ui.page import PageSignature


def tab_state(selected):
    return {
        "activity": "com.app/.MainActivity",
        "screen": {"width": 1080, "height": 2340},
        "elements": [
            {"text": label, "resource_id": f"id/tab_{i}",
             "bounds": [100 + i * 200, 120, 200 + i * 200, 180],
             "selected": label == selected}
            for i, label in enumerate(["同城", "关注", "推荐", "商城"])
        ],
    }


def test_selected_tab_identity_and_navigator_agree():
    g = UIKnowledgeGraph()
    a = g._resolve_or_infer_node(tab_state("同城"))
    b = g._resolve_or_infer_node(tab_state("关注"))
    assert a.node_id != b.node_id
    assert len(g.nodes) == 2
    assert g.match_node(tab_state("同城")) is a
    assert not UINavigator(g)._state_matches_node(tab_state("同城"), b)
    assert g.consolidate_equivalent_nodes() == {}


def test_incoming_display_semantic_does_not_reject_self():
    g = UIKnowledgeGraph()
    state = {"activity": "com.app/.GeneralActivity", "elements": [{"text": "数据总览"}]}
    _, _, node = g.record_transition(
        {"activity": "com.app/.MainActivity", "elements": [{"text": "创作中心"}]},
        {"text": "创作中心"}, state,
    )
    assert node.semantic_name == "创作中心"
    live = {**state, "screen": {"width": 1080, "height": 2340},
            "elements": [{"text": "工作台", "resource_id": "id/toolbar_title", "bounds": [400, 100, 680, 180]},
                         {"text": "数据总览"}]}
    assert g.match_node(live) is node
    assert UINavigator(g)._state_matches_node(live, node)


def test_v1_graph_and_missing_geometry_remain_matchable():
    live = tab_state("同城")
    g = UIKnowledgeGraph.load_graph_json({"version": "1.0", "edges": [], "nodes": {
        "old": {"node_id": "old", "activity_or_vc": live["activity"],
                "page_signature": {"name": "old", "required_elements": ["同城", "关注"]}}
    }})
    assert g.match_node(live).node_id == "old"
    modern = UIKnowledgeGraph()
    node = modern._resolve_or_infer_node(live)
    assert modern.match_node({k: v for k, v in live.items() if k != "screen"}) is node


def dynamic_state(text, **extra):
    return {"activity": "com.app/.DetailActivity", "elements": [
        {"text": text, "resource_id": "id/detail_count"},
        {"resource_id": "id/detail_action"}, {"resource_id": "id/detail_toolbar"}], **extra}


def test_dynamic_skeleton_and_navigator_use_same_rules():
    g = UIKnowledgeGraph()
    node = g._resolve_or_infer_node(dynamic_state("离线缓存(1)"))
    live = dynamic_state("离线缓存(2)")
    assert g.match_node(live) is node
    assert UINavigator(g)._state_matches_node(live, node)
    node.page_signature.forbidden_elements = ["错误"]
    assert g.match_node(dynamic_state("错误")) is None
    node.page_signature.forbidden_elements = []
    node.active_fragment = "DetailFragment"
    assert g.match_node(dynamic_state("离线缓存(1)", active_fragment="OtherFragment")) is None
    assert g.match_node(live) is None  # Missing Fragment cannot justify skeleton fallback.


def test_modal_skeleton_does_not_match_another_modal():
    g = UIKnowledgeGraph()
    node = g._resolve_or_infer_node(dynamic_state("确认删除", node_type="modal"))
    assert g.match_node(dynamic_state("确认退出", node_type="modal")) is None
    assert not UINavigator(g)._state_matches_node(dynamic_state("确认退出", node_type="modal"), node)


def equivalent_node(nid, requirements, skeleton, **extra):
    return UINode(nid, PageSignature(name=nid, required_elements=requirements),
                  activity_or_vc="com.app/.WebViewActivity", semantic_name="详情",
                  id_skeleton=skeleton, **extra)


@pytest.mark.parametrize("requirements,skeleton", [(["申请售后"], ["order_panel"]),
                                                    (["确认已读"], ["notice_panel"]),
                                                    (["申请售后"], ["notice_panel"])])
def test_same_semantic_requires_signature_and_structure_evidence(requirements, skeleton):
    a = equivalent_node("a", ["申请售后"], ["order_panel"])
    b = equivalent_node("b", requirements, skeleton)
    g = UIKnowledgeGraph(nodes={"a": a, "b": b})
    mapping = g.consolidate_equivalent_nodes()
    assert bool(mapping) == (requirements == ["申请售后"] and skeleton == ["order_panel"])


def test_empty_signature_and_semantic_are_not_equivalence_proof():
    g = UIKnowledgeGraph(nodes={nid: equivalent_node(nid, [], []) for nid in ("a", "b")})
    assert g.consolidate_equivalent_nodes() == {}


def test_distinct_locators_survive_and_identical_edges_sum_outcomes():
    nodes = {nid: UINode(nid, PageSignature(name=nid)) for nid in ("home", "detail")}
    dead = UIEdge("home", "detail", trigger_criteria={"text": "入口", "resource_id": "id/dead"},
                  attempts=5, successes=0, success_rate=0)
    reliable = UIEdge("home", "detail", trigger_criteria={"text": "入口", "resource_id": "id/good", "scroll_page": 2},
                      attempts=5, successes=5)
    duplicate = UIEdge("home", "detail", trigger_criteria=dict(reliable.trigger_criteria), attempts=2, successes=1)
    g = UIKnowledgeGraph(nodes=nodes, edges=[dead, reliable, duplicate])
    g.consolidate_equivalent_nodes()
    assert len(g.edges) == 2
    assert reliable.attempts == 7 and reliable.successes == 6
    assert reliable.success_rate == round(6 / 7, 4)
    assert g.find_path_to("detail", "home") == [reliable]
    assert reliable.trigger_criteria["scroll_page"] == 2


@pytest.mark.parametrize("mode", ["simple", "detailed"])
def test_mermaid_unsafe_ids_and_html_are_escaped(mode):
    ids = ["end", "subgraph", "Main$Inner", "中文 id", "ui_node_0"]
    nodes = {nid: UINode(nid, PageSignature(name=nid, required_elements=["<返回>&"]),
                        semantic_name="<页面>&", interactive_elements=[{"text": "<返回首页>"}]) for nid in ids}
    g = UIKnowledgeGraph(nodes=nodes, edges=[UIEdge("end", "Main$Inner", trigger_criteria={"text": '<点击>&"'})])
    output = g.to_mermaid(mode=mode)
    assert '    end[' not in output and '    Main$Inner[' not in output
    assert '<页面>' not in output and '<点击>' not in output
    assert '&lt;页面&gt;&amp;' in output and '&lt;点击&gt;&amp;&quot;' in output
    assert '&amp;lt;' not in output
    definitions = [line.split('["')[0].strip() for line in output.splitlines()[1:] if '["' in line]
    assert len(set(definitions)) == len(nodes)


def test_navigator_scrolls_two_recorded_viewports_using_fresh_bounds():
    g = UIKnowledgeGraph(nodes={"home": UINode("home", PageSignature(required_elements=["首页"]))})
    current = {"page": 0}
    taps = []
    def state():
        elements = [{"text": "首页"}]
        if current["page"] == 2:
            elements.append({"text": "深层入口", "resource_id": "id/entry", "bounds": [100, 500, 300, 600]})
        return {"activity": "MainActivity", "screen": {"width": 1080, "height": 2340}, "elements": elements}
    class Driver:
        def swipe(self, *args):
            current["page"] += 1
        def tap(self, *args):
            taps.append(args)
            return {"ok": True}
    nav = UINavigator(g, driver=Driver(), state_provider=state)
    result = nav._execute_edge(UIEdge("home", "detail", trigger_criteria={"text": "深层入口", "resource_id": "id/entry", "scroll_page": 2}), state())
    assert result["ok"]
    assert current["page"] == 2
    assert taps == [(200, 550)]


def test_combined_id_and_text_do_not_accept_wrong_list_row():
    wrong = {"text": "删除账户", "resource_id": "id/menu_row", "bounds": [0, 0, 100, 100]}
    correct = {"text": "隐私设置", "resource_id": "id/menu_row", "bounds": [0, 200, 100, 300]}
    criteria = {"text": "隐私设置", "resource_id": "id/menu_row"}
    assert UINavigator._find_element_for_criteria(criteria, {"elements": [wrong]}) is None
    assert UINavigator._find_element_for_criteria(criteria, {"elements": [wrong, correct]}) is correct


def test_scroll_replay_accepts_disappearing_initial_viewport_labels():
    initial = {"activity": "com.app/.SettingsActivity", "active_fragment": "SettingsFragment",
               "screen": {"width": 1080, "height": 2340},
               "elements": [{"text": "账户设置", "resource_id": "id/menu_row"}]}
    after = {**initial, "elements": [{"text": "隐私设置", "resource_id": "id/menu_row", "bounds": [100, 500, 300, 600]}]}
    g = UIKnowledgeGraph()
    source = g._resolve_or_infer_node(initial)
    current = {"state": initial}
    taps = []
    class Driver:
        def swipe(self, *args):
            current["state"] = after
        def tap(self, *args):
            taps.append(args)
            return {"ok": True}
    nav = UINavigator(g, driver=Driver(), state_provider=lambda: current["state"])
    result = nav._execute_edge(UIEdge(source.node_id, "privacy", trigger_criteria={
        "text": "隐私设置", "resource_id": "id/menu_row", "scroll_page": 1}), initial)
    assert result["ok"]
    assert taps == [(200, 550)]


def test_shared_skeleton_cannot_override_stable_page_title():
    def state(title):
        return {"activity": "com.app/.SettingsActivity", "elements": [
            {"text": title, "resource_id": "id/toolbar_title"},
            {"text": "通知", "resource_id": "id/toggle_setting"},
            {"text": "帮助", "resource_id": "id/help_link"}]}
    g = UIKnowledgeGraph()
    account = g._resolve_or_infer_node(state("账户设置"))
    assert g.match_node(state("隐私设置")) is None
    assert not UINavigator(g)._state_matches_node(state("隐私设置"), account)
    privacy = g._resolve_or_infer_node(state("隐私设置"))
    assert account.node_id != privacy.node_id
    assert len(g.nodes) == 2


@pytest.mark.parametrize("node_type", ["page", "sub_tab", "modal"])
def test_explicit_host_mismatch_cannot_merge_identical_pages(node_type):
    g = UIKnowledgeGraph()
    first = dynamic_state("详情", node_type=node_type, host_node_id="host-a")
    other = {**first, "host_node_id": "host-b"}
    node = g._resolve_or_infer_node(first)
    assert g.match_node(other) is None
    assert not g.state_matches_node(other, node)
    second = g._resolve_or_infer_node(other)
    assert second.node_id != node.node_id
    assert second.host_node_id == "host-b"
    assert len(g.nodes) == 2
    # Dynamic-text skeleton fallback must respect the same host guard.
    changed = dynamic_state("详情(2)", node_type=node_type, host_node_id="host-b")
    assert not g.state_matches_node(changed, node)


def test_matching_host_and_missing_legacy_host_remain_compatible():
    g = UIKnowledgeGraph()
    live = dynamic_state("详情", host_node_id="host-a")
    node = g._resolve_or_infer_node(live)
    assert g.match_node(live) is node
    assert g.match_node({key: value for key, value in live.items() if key != "host_node_id"}) is node


def test_caller_signature_is_not_replaced_by_stricter_recorded_node():
    node = UINode("settings", PageSignature(name="settings", required_elements=["设置", "账户"]),
                  activity_or_vc="com.app/.SettingsActivity", active_fragment="SettingsFragment")
    g = UIKnowledgeGraph(nodes={node.node_id: node})
    live = {"activity": {"activity": "com.app/.SettingsActivity"},
            "active_fragment": "OtherFragment", "elements": [{"text": "设置"}]}
    target = PageSignature(name="settings", activity_or_controller="SettingsActivity",
                           required_elements=["设置"])
    actions = []
    nav = UINavigator(g, action_executor=lambda edge, state: actions.append(edge))
    result = nav.navigate_to(target, current_state=live, max_heals=0)
    assert result["ok"]
    assert result["reason"] == "already_at_target"
    assert result["hops_executed"] == 0
    assert actions == []
    # The node-id API still requires the recorded node's full identity.
    assert not nav.navigate_to("settings", current_state=live, max_heals=0)["ok"]


@pytest.mark.parametrize("constraint", ["required", "forbidden", "activity", "callback"])
def test_caller_signature_constraints_cannot_be_bypassed_by_recorded_node(constraint):
    node = UINode("settings", PageSignature(name="settings", required_elements=["设置"]))
    g = UIKnowledgeGraph(nodes={node.node_id: node})
    live = {"activity": "SettingsActivity", "elements": [{"text": "设置"}, {"text": "错误"}]}
    kwargs = {
        "required": {"required_elements": ["账户"]},
        "forbidden": {"forbidden_elements": ["错误"]},
        "activity": {"activity_or_controller": "OtherActivity"},
        "callback": {"extra_match_fn": lambda state: False},
    }[constraint]
    nav = UINavigator(g)
    assert g.state_matches_node(live, node)
    assert not nav.navigate_to(PageSignature(name="settings", **kwargs),
                               current_state=live, max_heals=0)["ok"]


def test_explicit_node_id_does_not_override_conflicting_live_identity():
    node = UINode("settings", PageSignature(name="settings", required_elements=["账户"]))
    nav = UINavigator(UIKnowledgeGraph(nodes={node.node_id: node}))
    live = {"node_id": "settings", "elements": [{"text": "隐私"}]}
    assert not nav.navigate_to("settings", current_state=live, max_heals=0)["ok"]
