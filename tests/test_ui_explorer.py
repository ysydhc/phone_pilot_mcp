"""Unit tests for UIKnowledgeGraph Mermaid/Mindmap export and AppExplorer."""

from __future__ import annotations

import shlex
from types import SimpleNamespace
from unittest.mock import Mock

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


def test_hierarchical_statechart_and_skeleton_jaccard_matching():
    graph = UIKnowledgeGraph()
    # 1. 测试个人中心子 Tab 语义命名与分类
    mine_state_v1 = {
        "ok": True,
        "activity": "tv.danmaku.bili/.MainActivityV2",
        "active_fragment": "UserCenterFragment",
        "elements": [
            {"text": "ysydhc", "resource_id": "tv.danmaku.bili:id/nick_name"},
            {"text": "空间", "resource_id": "tv.danmaku.bili:id/tv_go_to_space"},
            {"text": "离线缓存", "resource_id": "tv.danmaku.bili:id/mine_title"},
            {"text": "历史记录", "resource_id": "tv.danmaku.bili:id/mine_title"},
            {"text": "我的收藏", "resource_id": "tv.danmaku.bili:id/mine_title"},
        ],
    }
    node_mine = graph._resolve_or_infer_node(mine_state_v1)
    assert node_mine.node_type == "sub_tab"
    assert node_mine.semantic_name == "个人中心"
    assert node_mine.active_fragment == "UserCenterFragment"

    # 2. 当动态昵称/文本变化但 DOM ID 骨架一致（Jaccard >= 0.80）时，应命中同一状态节点而非分裂新节点
    mine_state_v2 = {
        "ok": True,
        "activity": "tv.danmaku.bili/.MainActivityV2",
        "active_fragment": "UserCenterFragment",
        "elements": [
            {"text": "另一位用户昵称_动态变化", "resource_id": "tv.danmaku.bili:id/nick_name"},
            {"text": "空间", "resource_id": "tv.danmaku.bili:id/tv_go_to_space"},
            {"text": "离线缓存(3)", "resource_id": "tv.danmaku.bili:id/mine_title"},
            {"text": "历史记录(99+)", "resource_id": "tv.danmaku.bili:id/mine_title"},
            {"text": "我的收藏(12)", "resource_id": "tv.danmaku.bili:id/mine_title"},
        ],
    }
    matched = graph.match_node(mine_state_v2)
    assert matched is not None
    assert matched.node_id == node_mine.node_id

    # 3. 测试模态浮层（Modal）分类与 Mermaid 六边形渲染
    modal_state = {
        "ok": True,
        "activity": "tv.danmaku.bili/.MainActivityV2",
        "elements": [
            {"text": "转发动态", "resource_id": "tv.danmaku.bili:id/title"},
            {"text": "说说我的看法...", "resource_id": "tv.danmaku.bili:id/edit"},
            {"text": "取消", "resource_id": "tv.danmaku.bili:id/cancel"},
        ],
    }
    node_modal = graph._resolve_or_infer_node(modal_state)
    assert node_modal.node_type == "modal"
    assert "转发动态" in node_modal.semantic_name


def test_equivalence_clustering_and_state_toggle_filtering():
    explorer = AppExplorer()
    state = {
        "ok": True,
        "screen": {"width": 1080, "height": 2340},
        "elements": [
            # 同属 profile_header 通用结构簇的 4 个控件 -> 应仅保留 1 个代表项，其余写入 equivalent_triggers
            {"text": "ysydhc", "resource_id": "com.example.app:id/nick_name", "bounds": [200, 300, 400, 360], "clickable": True},
            {"text": "[layer_avatar]", "resource_id": "com.example.app:id/layer_avatar", "bounds": [50, 280, 180, 400], "clickable": True},
            {"text": "空间", "resource_id": "com.example.app:id/tv_go_to_space", "bounds": [850, 300, 1000, 360], "clickable": True},
            {"text": "正式会员", "resource_id": "com.example.app:id/vip_label", "bounds": [200, 370, 350, 410], "clickable": True},
            # 页内纯状态开关 -> 应被过滤不触发导航点击
            {"text": "[day_night_setting]", "resource_id": "com.example.app:id/day_night_setting", "bounds": [900, 120, 980, 190], "clickable": True},
            # 独立业务入口 -> 应正常保留
            {"text": "设置", "resource_id": "com.example.app:id/menu_setting", "bounds": [100, 1800, 980, 1920], "clickable": True},
        ],
    }
    cands = explorer.filter_interactive_candidates(state, max_per_page=20, depth=1)
    assert len(cands) == 2
    rep = cands[0]
    assert rep["text"] == "ysydhc"
    eq_triggers = rep["trigger_criteria"].get("equivalent_triggers", [])
    assert "空间" in eq_triggers
    assert "正式会员" in eq_triggers
    assert cands[1]["text"] == "设置"

    # 验证通用入边触发器语义传播（Incoming Trigger Semantic Propagation）：
    # 当目标页面无标准顶栏标题、仅含通用容器类名（如 GeneralActivity）时，应自动继承触发按钮文本作为 semantic_name
    generic_dst_state = {
        "ok": True,
        "activity": "com.example.app/.GeneralActivity",
        "screen": {"width": 1080, "height": 2340},
        "elements": [
            {"text": "数据总览", "resource_id": "com.example.app:id/tab_overview", "bounds": [100, 500, 300, 580]},
        ],
    }
    _, _, dst_node = explorer.graph.record_transition(
        state,
        {"action_type": "tap", "trigger_criteria": {"text": "创作中心"}},
        generic_dst_state,
    )
    assert dst_node.semantic_name == "创作中心"


def _explorer_state(node_id="home", package="com.example.app", activity="HomeActivity", elements=None):
    return {
        "ok": True,
        "node_id": node_id,
        "package": package,
        "activity": f"{package}/.{activity}",
        "screen": {"width": 400, "height": 800},
        "elements": elements or [],
    }


def _menu_element(text="设置", resource_id="com.example.app:id/btn_settings"):
    return {"text": text, "resource_id": resource_id, "bounds": [40, 240, 360, 300], "clickable": True}


@pytest.mark.parametrize("state", [
    {"package": "com.example.app.clone"},
    {"activity": "com.example.app.clone/.HomeActivity"},
    {"activity": {"package": "com.example.app.clone", "activity": "HomeActivity"}},
    {"bundle_id": "com.example.app.clone"},
])
def test_app_explorer_package_lock_requires_exact_identity(state):
    explorer = AppExplorer(target_package="com.example.app")
    assert explorer._is_within_target_package(state, explorer.target_package) is False
    assert explorer._is_within_target_package({"package": "com.example.app"}, explorer.target_package) is True


def test_app_explorer_external_initial_target_fails_without_action():
    state_provider = Mock(return_value=_explorer_state(package="com.example.app.clone", elements=[_menu_element()]))
    action = Mock(return_value=True)
    back = Mock(return_value={"ok": False})
    launch = Mock(return_value={"ok": False})
    swipe = Mock(return_value={"ok": True})
    driver = SimpleNamespace(app=SimpleNamespace(launch_app=launch), go_back=back,
                             input=SimpleNamespace(swipe=swipe))
    explorer = AppExplorer(driver=driver, state_provider=state_provider, action_executor=action,
                           back_executor=back, target_package="com.example.app")
    result = explorer.explore(step_delay_s=0)
    assert result["ok"] is False
    assert result["error"] == "target_package_unavailable"
    action.assert_not_called()
    swipe.assert_not_called()
    back.assert_called_once()
    launch.assert_called_once()
    assert explorer.graph.nodes == {}
    assert explorer.graph.edges == []


def test_app_explorer_escape_to_prefix_package_never_records_external_node():
    home = _explorer_state(elements=[_menu_element()])
    outside = _explorer_state("external", package="com.example.app.clone")
    current = [home]

    def tap(_action):
        current[0] = outside
        return {"ok": True}

    def back():
        current[0] = home
        return {"ok": True}

    tap_spy = Mock(side_effect=tap)
    back_spy = Mock(side_effect=back)
    explorer = AppExplorer(state_provider=lambda: current[0], action_executor=tap_spy,
                           back_executor=back_spy, target_package="com.example.app")
    result = explorer.explore(step_delay_s=0, inspect_package_routes=False, enable_scroll_stitch=False)
    assert result["ok"] is True
    tap_spy.assert_called_once()
    back_spy.assert_called_once()
    assert "external" not in explorer.graph.nodes
    assert explorer.graph.edges == []
    assert explorer.history == []


@pytest.mark.parametrize("login_state", [
    _explorer_state(activity="LoginActivity", elements=[_menu_element("发送验证码", "com.example.app:id/btn_send")]),
    {**_explorer_state(elements=[_menu_element("发送验证码", "com.example.app:id/btn_send")]),
     "all_texts": ["请输入手机号"]},
])
def test_app_explorer_login_root_records_node_without_sending(login_state):
    action = Mock(return_value=True)
    back = Mock(return_value=True)
    swipe = Mock(return_value={"ok": True})
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe)),
                           state_provider=lambda: login_state, action_executor=action, back_executor=back)
    result = explorer.explore(step_delay_s=0, inspect_package_routes=False)
    assert result["ok"] is True
    assert result["nodes_count"] == 1
    assert result["edges_count"] == 0
    action.assert_not_called()
    back.assert_not_called()
    swipe.assert_not_called()


@pytest.mark.parametrize("budget", [
    {"max_depth": 0}, {"max_depth": -1},
    {"max_pages": 0}, {"max_pages": -1},
    {"timeout_s": 0}, {"timeout_s": -1},
])
def test_app_explorer_invalid_budget_has_no_io(budget, monkeypatch):
    state = Mock(return_value=_explorer_state(elements=[_menu_element()]))
    action = Mock()
    back = Mock()
    subprocess_run = Mock()
    monkeypatch.setattr("subprocess.run", subprocess_run)
    explorer = AppExplorer(driver=SimpleNamespace(platform="android", serial="only-this-device"),
                           state_provider=state, action_executor=action, back_executor=back,
                           target_package="com.example.app")
    result = explorer.explore(**budget)
    assert result == {"ok": False, "error": "invalid_exploration_budget"}
    state.assert_not_called()
    action.assert_not_called()
    back.assert_not_called()
    subprocess_run.assert_not_called()
    assert explorer.graph.nodes == {}


@pytest.mark.parametrize("text", ["关闭", "", "返回"])
def test_app_explorer_close_delete_backup_never_executes(text):
    action = Mock(return_value=True)
    # Empty text exercises the top-left geometric close fallback as well.
    element = {"text": text, "resource_id": "com.example.app:id/delete_backup",
               "bounds": [8, 40, 48, 80], "clickable": True}
    explorer = AppExplorer(action_executor=action)
    assert explorer._try_ui_close_or_back(_explorer_state(elements=[element])) is False
    action.assert_not_called()


@pytest.mark.parametrize("surface", ["tap_driver", "tap_executor", "back_driver", "back_executor", "relaunch"])
def test_app_explorer_dict_failure_is_not_success(surface):
    failed = Mock(return_value={"ok": False, "error": "device_busy_locked"})
    driver = SimpleNamespace(input=SimpleNamespace(tap=failed), go_back=failed,
                             app=SimpleNamespace(launch_app=failed))
    explorer = AppExplorer(driver=driver,
                           action_executor=failed if surface == "tap_executor" else None,
                           back_executor=failed if surface == "back_executor" else None)
    if surface.startswith("tap"):
        result = explorer.execute_action({"action_type": "tap", **_menu_element()})
    elif surface.startswith("back"):
        result = explorer.execute_back()
    else:
        result = explorer._relaunch_target_package("com.example.app")
    assert result is False
    failed.assert_called_once()


def test_app_explorer_failed_dict_tap_does_not_record_transition():
    state = Mock(return_value=_explorer_state(elements=[_menu_element()]))
    action = Mock(return_value={"ok": False, "error": "tap_failed"})
    explorer = AppExplorer(state_provider=state, action_executor=action)
    result = explorer.explore(step_delay_s=0, inspect_package_routes=False, enable_scroll_stitch=False)
    assert result["ok"] is True
    action.assert_called_once()
    # Initial read plus live pre-dispatch package check (no post-failure transition read).
    assert state.call_count == 2
    assert result["edges_count"] == 0
    assert explorer.history == []


def test_app_explorer_missing_offscreen_target_never_taps_stale_bounds(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _seconds: None)
    tap = Mock(return_value={"ok": True})
    swipe = Mock(return_value={"ok": True})
    driver = SimpleNamespace(input=SimpleNamespace(tap=tap, swipe=swipe),
                             screen=SimpleNamespace(get_screen_size=Mock(return_value=(400, 800))))
    live = Mock(return_value=_explorer_state(elements=[_menu_element("其他入口")]))
    explorer = AppExplorer(driver=driver, state_provider=live)
    action = {"action_type": "tap", **_menu_element("已消失的入口"), "scroll_page": 1}
    assert explorer.execute_action(action) is False
    assert swipe.call_count >= 1
    assert live.call_count >= 1
    tap.assert_not_called()


@pytest.mark.parametrize("platform,serial", [("ios", "ios-serial"), ("harmony", "hdc-serial"),
                                             (None, "unknown-serial"), ("android", ""), ("android", "   ")])
def test_app_explorer_adb_probe_requires_android_and_explicit_serial(platform, serial, monkeypatch):
    run = Mock(return_value=SimpleNamespace(stdout="unexpected"))
    monkeypatch.setattr("subprocess.run", run)
    explorer = AppExplorer(driver=SimpleNamespace(platform=platform, serial=serial))
    assert explorer._run_adb_shell("dumpsys activity top") == ""
    run.assert_not_called()


def test_app_explorer_adb_probe_isolates_selected_android_serial(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stdout="probe output"))
    monkeypatch.setattr("subprocess.run", run)
    explorer = AppExplorer(driver=SimpleNamespace(platform="android", device_serial="android-selected",
                                                  serial="not-selected"))
    assert explorer._run_adb_shell("dumpsys activity top", timeout_s=1.25) == "probe output"
    run.assert_called_once_with(["adb", "-s", "android-selected", "shell", "dumpsys activity top"],
                                capture_output=True, text=True, timeout=1.25, check=False)


def test_app_explorer_adb_shell_parameters_are_quoted(monkeypatch):
    run = Mock(return_value=SimpleNamespace(stdout="Status: ok"))
    monkeypatch.setattr("subprocess.run", run)
    explorer = AppExplorer(driver=SimpleNamespace(platform="android", serial="android-selected"))
    package = "com.example.app; echo 'unsafe'"
    explorer.extract_package_routes(package)
    command = run.call_args.args[0]
    assert command[:4] == ["adb", "-s", "android-selected", "shell"]
    assert shlex.split(command[4]) == ["dumpsys", "package", package]
    component = f"{package}/.DetailsActivity; echo unsafe"
    assert explorer.teleport_to_activity(package, component, step_delay_s=0) is True
    command = run.call_args.args[0]
    assert shlex.split(command[4]) == ["am", "start", "-W", "-n", component]
    run.reset_mock()
    assert explorer.teleport_to_activity("com.example.app", "com.other.app/.DetailsActivity", step_delay_s=0) is False
    run.assert_not_called()


def _equivalent_node(node_id, *, activity="com.example.app/.DetailsActivity", fragment="com.example.ContentFragment",
                     host="host", node_type="modal", rich=False):
    return UINode(node_id=node_id, page_signature=PageSignature(name=node_id, required_elements=["text:详情"]),
                  activity_or_vc=activity, active_fragment=fragment, host_node_id=host,
                  node_type=node_type, semantic_name="详情", id_skeleton=["shared_id"],
                  interactive_elements=[_menu_element()] if rich else [])


@pytest.mark.parametrize("different", [
    {"activity": "com.other.app/.DetailsActivity"},
    {"activity": "com.example.app/com.other.DetailsActivity"},
    {"fragment": "com.other.ContentFragment"},
    {"fragment": ""},
    {"host": "other_host"},
    {"host": ""},
])
def test_graph_consolidation_preserves_full_activity_fragment_host_boundaries(different):
    graph = UIKnowledgeGraph()
    graph.add_or_update_node(_equivalent_node("first"))
    graph.add_or_update_node(_equivalent_node("second", **different))
    assert graph.consolidate_equivalent_nodes() == {}
    assert set(graph.nodes) == {"first", "second"}


def test_graph_consolidation_remaps_host_and_edges_to_retained_canonical_node():
    graph = UIKnowledgeGraph()
    graph.add_or_update_node(_equivalent_node("old_host", host="", fragment="", node_type="page"))
    graph.add_or_update_node(_equivalent_node("canonical_host", host="", fragment="", node_type="page", rich=True))
    graph.add_or_update_node(_equivalent_node("child", host="old_host"))
    graph.edges.append(UIEdge(source_id="old_host", target_id="child", trigger_criteria={"text": "详情"}))
    mapping = graph.consolidate_equivalent_nodes()
    assert mapping == {"old_host": "canonical_host"}
    assert set(graph.nodes) == {"canonical_host", "child"}
    assert graph.nodes["child"].host_node_id == "canonical_host"
    assert len(graph.edges) == 1
    assert graph.edges[0].source_id == "canonical_host"
    assert graph.edges[0].target_id == "child"
    assert graph.export_graph_json()["nodes"]


def test_app_explorer_zero_branch_budget_never_taps():
    action = Mock(return_value=True)
    explorer = AppExplorer(state_provider=lambda: _explorer_state(elements=[_menu_element()]),
                           action_executor=action)
    result = explorer.explore(max_branches_per_page=0, step_delay_s=0,
                              inspect_package_routes=False, enable_scroll_stitch=False)
    assert result["ok"] is True
    action.assert_not_called()
    assert result["edges_count"] == 0


def test_app_explorer_ui_cancel_after_failed_back_does_not_record_successful_back():
    home = _explorer_state(elements=[_menu_element()])
    dialog = _explorer_state("dialog", activity="DetailsActivity", elements=[
        _menu_element("取消", "com.example.app:id/btn_cancel"),
    ])
    current = [home]

    def action(action):
        current[0] = home if action.get("text") == "取消" else dialog
        return {"ok": True}

    back = Mock(return_value={"ok": False, "error": "back_failed"})
    explorer = AppExplorer(state_provider=lambda: current[0], action_executor=action, back_executor=back)
    result = explorer.explore(max_depth=1, step_delay_s=0, inspect_package_routes=False,
                              enable_scroll_stitch=False)
    assert result["ok"] is True
    assert current[0] is home
    assert back.call_count == 2
    assert any(edge.action_type == "tap" for edge in explorer.graph.edges)
    assert not any(edge.action_type == "back" for edge in explorer.graph.edges)


@pytest.mark.asyncio
async def test_mcp_explore_lease_denial_never_constructs_driver(monkeypatch):
    from phone_pilot.mcp import _evolution_tools, _helpers

    monkeypatch.setenv("PHONE_PILOT_STRICT_LOCK", "1")
    monkeypatch.delenv("PHONE_PILOT_SESSION_ID", raising=False)
    resolver = Mock(return_value=("android-selected", "android", None))
    lease = Mock(return_value={"locked": True, "lock_info": {"session_id": "other-session"}})
    driver_factory = Mock()
    monkeypatch.setattr(_helpers, "_resolve_device_serial", resolver)
    import importlib

    lease_module = importlib.import_module("phone_pilot.core.device_lease")
    monkeypatch.setattr(lease_module, "get_device_lock_status", lease)
    monkeypatch.setattr(_evolution_tools, "get_driver", driver_factory)
    result = await _evolution_tools.phone_explore_app(device_serial="android-selected", platform="android")
    assert result["ok"] is False
    assert result["error"] == "device_busy_locked"
    resolver.assert_called_once_with("android-selected", "android")
    lease.assert_called_once_with("android-selected")
    driver_factory.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_explore_failed_launch_never_starts_exploration(monkeypatch):
    from phone_pilot.mcp import _evolution_tools

    launch = Mock(return_value={"ok": False, "error": "launch_failed"})
    state = Mock(return_value=_explorer_state())
    driver = SimpleNamespace(app=SimpleNamespace(launch_app=launch), get_page_state=state)
    driver_factory = Mock(return_value=driver)
    explore = Mock()
    monkeypatch.setattr(_evolution_tools, "get_driver", driver_factory)
    monkeypatch.setattr(_evolution_tools, "_with_device", lambda fn, serial, platform: fn(serial, platform))
    monkeypatch.setattr(AppExplorer, "explore", explore)
    result = await _evolution_tools.phone_explore_app(package_or_bundle="com.example.app",
                                                     device_serial="android-selected", platform="android")
    assert result == {"ok": False, "error": "target_app_launch_failed"}
    driver_factory.assert_called_once_with("android-selected", "android")
    launch.assert_called_once_with("com.example.app", None)
    state.assert_not_called()
    explore.assert_not_called()


def test_app_explorer_offscreen_same_text_different_id_never_taps(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _seconds: None)
    tap = Mock(return_value={"ok": True})
    swipe = Mock(return_value={"ok": True})
    driver = SimpleNamespace(input=SimpleNamespace(tap=tap, swipe=swipe),
                             screen=SimpleNamespace(get_screen_size=Mock(return_value=(400, 800))))
    live = Mock(return_value=_explorer_state(elements=[_menu_element("设置", "com.example.app:id/unrelated")]))
    explorer = AppExplorer(driver=driver, state_provider=live)
    assert explorer.execute_action({"action_type": "tap", **_menu_element(), "scroll_page": 1}) is False
    assert live.call_count >= 1
    tap.assert_not_called()


def test_app_explorer_expired_positive_deadline_never_dispatches_actions(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.monotonic", lambda: now[0])
    tap = Mock(return_value={"ok": True})
    back = Mock(return_value={"ok": True})
    swipe = Mock(return_value={"ok": True})
    driver = SimpleNamespace(input=SimpleNamespace(tap=tap, swipe=swipe), go_back=back)

    def state():
        now[0] += 2.0  # A slow state read exceeds an otherwise valid positive budget.
        return _explorer_state(elements=[_menu_element()])

    explorer = AppExplorer(driver=driver, state_provider=state)
    result = explorer.explore(timeout_s=1.0, step_delay_s=0, inspect_package_routes=False,
                              enable_scroll_stitch=False)
    assert result["ok"] is True
    assert result["edges_count"] == 0
    tap.assert_not_called()
    back.assert_not_called()
    swipe.assert_not_called()


def test_app_explorer_failed_escape_recovery_never_taps_external_close():
    home = _explorer_state(elements=[_menu_element()])
    outside = _explorer_state("external", package="com.other.app", elements=[
        _menu_element("关闭", "com.other.app:id/btn_close"),
    ])
    current = [home]

    def action(_action):
        current[0] = outside
        return {"ok": True}

    tap = Mock(side_effect=action)
    back = Mock(return_value={"ok": False, "error": "back_failed"})
    launch = Mock(return_value={"ok": False, "error": "launch_failed"})
    swipe = Mock(return_value={"ok": True})
    driver = SimpleNamespace(app=SimpleNamespace(launch_app=launch),
                             input=SimpleNamespace(swipe=swipe))
    explorer = AppExplorer(driver=driver, state_provider=lambda: current[0],
                           action_executor=tap, back_executor=back, target_package="com.example.app")
    result = explorer.explore(step_delay_s=0, inspect_package_routes=False, enable_scroll_stitch=False)
    assert result["ok"] is True
    tap.assert_called_once()
    assert tap.call_args.args[0]["text"] == "设置"
    assert back.call_count >= 1
    assert launch.call_count >= 1
    swipe.assert_not_called()
    assert "external" not in explorer.graph.nodes
    assert explorer.graph.edges == []
    # The standalone close recovery helper must enforce the same lock.
    assert explorer._try_ui_close_or_back(outside) is False
    tap.assert_called_once()


@pytest.mark.parametrize("success", [True, False])
def test_app_explorer_back_edge_dispatches_to_execute_back(success):
    back = Mock(return_value={"ok": success})
    tap = Mock(return_value={"ok": True})
    driver = SimpleNamespace(go_back=back, input=SimpleNamespace(tap=tap))
    explorer = AppExplorer(driver=driver)
    edge = UIEdge(source_id="child", target_id="parent", action_type="back",
                  trigger_criteria={"key": "BACK"})
    assert explorer.execute_action({"action_type": edge.action_type,
                                    "trigger_criteria": edge.trigger_criteria}) is success
    back.assert_called_once()
    tap.assert_not_called()


@pytest.mark.parametrize("starting_page,target_page", [(0, 2), (2, 0)])
def test_app_explorer_partial_scroll_failure_tracks_offset_and_restores_before_tap(
    starting_page, target_page, monkeypatch,
):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _seconds: None)
    events = []
    outcomes = iter([True, False, True])

    def swipe(*_args, **_kwargs):
        ok = next(outcomes)
        events.append(("swipe", ok))
        return {"ok": ok}

    def tap(*_args):
        events.append(("tap", True))
        return {"ok": True}

    swipe_spy = Mock(side_effect=swipe)
    tap_spy = Mock(side_effect=tap)
    driver = SimpleNamespace(input=SimpleNamespace(tap=tap_spy, swipe=swipe_spy),
                             screen=SimpleNamespace(get_screen_size=Mock(return_value=(400, 800))))
    state = Mock(return_value=_explorer_state(elements=[_menu_element()]))
    explorer = AppExplorer(driver=driver, state_provider=state)
    explorer._current_scroll_page = starting_page
    assert explorer.execute_action({"action_type": "tap", **_menu_element(),
                                    "scroll_page": target_page}) is False
    assert explorer._current_scroll_page == 1
    tap_spy.assert_not_called()
    # Reads guard each swipe; no extra read occurs after the explicit failed swipe.
    assert state.call_count == 3

    # Returning to viewport zero must compensate for the successful partial swipe.
    assert explorer.execute_action({"action_type": "tap", **_menu_element(), "scroll_page": 0}) is True
    assert explorer._current_scroll_page == 0
    assert events == [("swipe", True), ("swipe", False), ("swipe", True), ("tap", True)]
    assert swipe_spy.call_args.kwargs["duration_ms"] == 380
    tap_spy.assert_called_once()
