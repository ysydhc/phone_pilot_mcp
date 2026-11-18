"""Acceptance regressions execute real explorer code without devices."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from phone_pilot.core.ui.explorer import AppExplorer
from phone_pilot.core.ui.graph import UIEdge


def state(page="root", package="com.app", elements=None):
    return {"ok": True, "node_id": page, "activity": {"package": package, "activity": ""},
            "screen": {"width": 400, "height": 800}, "elements": elements or [],
            "scrollable_areas": [{"direction": "vertical", "bounds": [0, 100, 400, 750]}]}


def element(y=200, text="Open", rid="id/open"):
    return {"text": text, "resource_id": rid, "bounds": [0, y, 100, y + 100]}


@pytest.mark.parametrize("foreign_package", ["com.other", ""])
def test_stitch_rejects_foreign_or_unknown_package_without_rollback(foreign_package, monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    base = state(elements=[element()])
    outside = state(package=foreign_package, elements=[element(text="Foreign")])
    current = [base]
    def swipe(*args, **kwargs):
        current[0] = outside
        return {"ok": True}
    swipe = Mock(side_effect=swipe)
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe)),
                           state_provider=lambda: current[0], target_package="com.app")
    result = explorer.stitch_scrollable_state(base)
    assert all(el["text"] != "Foreign" for el in result["elements"])
    assert swipe.call_count == 1
    assert explorer._current_scroll_page == 0


def test_recovery_callback_uses_live_bounds_and_full_locator(monkeypatch):
    captured = []
    def navigate(nav, target, **kwargs):
        captured.append(nav.action_executor)
        return {"ok": False}
    monkeypatch.setattr("phone_pilot.core.ui.explorer.UINavigator.navigate_to", navigate)
    root = state(elements=[element()])
    drift = state("drift", elements=[])
    current = [root]
    def tap(*args):
        current[0] = drift
        return {"ok": True}
    tap = Mock(side_effect=tap)
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(tap=tap)),
                           state_provider=lambda: current[0], back_executor=lambda: False,
                           target_package="com.app")
    explorer.explore(max_depth=1, step_delay_s=0, inspect_package_routes=False, enable_scroll_stitch=False)
    assert captured
    current[0] = state(elements=[element(100, text="Wrong"), element(500)])
    edge = UIEdge("root", "child", trigger_criteria=element())
    assert captured[0](edge, current[0])["ok"]
    assert tap.call_args.args == (50, 550)


def test_recovery_callback_blocks_each_foreign_action(monkeypatch):
    captured = []
    def navigate(nav, target, **kwargs):
        captured.append(nav.action_executor)
        return {"ok": False}
    monkeypatch.setattr("phone_pilot.core.ui.explorer.UINavigator.navigate_to", navigate)
    current = [state(elements=[element()])]
    def action(_):
        current[0] = state("drift")
        return True
    action = Mock(side_effect=action)
    explorer = AppExplorer(state_provider=lambda: current[0], action_executor=action,
                           back_executor=lambda: False, target_package="com.app")
    explorer.explore(max_depth=1, step_delay_s=0, inspect_package_routes=False, enable_scroll_stitch=False)
    assert captured
    current[0] = state("outside", "com.other", [element(text="Close")])
    calls = action.call_count
    assert not captured[0](UIEdge("outside", "root", trigger_criteria={"text": "Close"}), current[0])["ok"]
    assert action.call_count == calls


def test_child_viewport_does_not_inherit_parent_offset(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    current = [state(elements=[element()])]
    swipe = Mock(return_value={"ok": True})
    def tap(*args):
        current[0] = state("child", elements=[element(text="Child", rid="id/child")])
        return {"ok": True}
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe, tap=tap),
                           screen=SimpleNamespace(get_screen_size=lambda: (400, 800))),
                           state_provider=lambda: current[0], target_package="com.app")
    assert explorer.execute_action({**element(), "scroll_page": 1})
    swipe.reset_mock()
    assert explorer.execute_action(element(text="Child", rid="id/child"))
    swipe.assert_not_called()
    assert explorer._current_scroll_page == 0


def test_real_same_activity_child_resets_offset_without_synthetic_ids(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    parent = state(elements=[element()])
    parent.pop("node_id")
    child = state(elements=[element(text="Child", rid="id/child")])
    child.pop("node_id")
    current = [parent]
    swipe = Mock(return_value={"ok": True})
    tap = Mock(side_effect=lambda *args: (current.__setitem__(0, child), {"ok": True})[1])
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe, tap=tap),
                           screen=SimpleNamespace(get_screen_size=lambda: (400, 800))),
                           state_provider=lambda: current[0], target_package="com.app")
    assert explorer.execute_action({**element(), "scroll_page": 1})
    swipe.reset_mock()
    assert explorer.execute_action(element(text="Child", rid="id/child"))
    swipe.assert_not_called()


def test_recovery_real_navigator_never_scrolls_foreign_app(monkeypatch):
    from phone_pilot.core.ui.graph import UIKnowledgeGraph, UINode
    from phone_pilot.core.ui.navigator import UINavigator
    from phone_pilot.core.ui.page import PageSignature
    graph = UIKnowledgeGraph(nodes={
        "a": UINode("a", PageSignature(required_elements=["A"])),
        "b": UINode("b", PageSignature(required_elements=["B"]))},
        edges=[UIEdge("a", "b", trigger_criteria={"text": "Go"})])
    inside = state(elements=[element(text="A"), element(text="Go")])
    inside.pop("node_id")
    outside = state(package="com.other", elements=[element(text="Close")])
    outside.pop("node_id")
    current = [inside]
    swipe = Mock(return_value={"ok": True})
    def execute(edge, st):
        current[0] = outside
        return {"ok": True}
    nav = UINavigator(graph, driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe)),
                      state_provider=lambda: current[0], action_executor=execute,
                      action_guard=lambda st: AppExplorer._extract_package(st) == "com.app")
    assert not nav.navigate_to("b", current_state=inside, max_heals=1)["ok"]
    swipe.assert_not_called()


def test_expired_live_read_never_dispatches_custom_action(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.monotonic", lambda: now[0])
    action = Mock(return_value=True)
    def read():
        now[0] = 2.0
        return state(elements=[element()])
    explorer = AppExplorer(state_provider=read, action_executor=action, target_package="com.app")
    explorer._deadline = 1.0
    assert not explorer.execute_action(element())
    action.assert_not_called()


def test_content_desc_scroll_locator_uses_fresh_bounds(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    current = [state(elements=[])]
    after = state(elements=[{"content_desc": "深层设置", "bounds": [0, 300, 100, 400]}])
    swipe = Mock(side_effect=lambda *args, **kw: (current.__setitem__(0, after), {"ok": True})[1])
    tap = Mock(return_value={"ok": True})
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe, tap=tap),
                           screen=SimpleNamespace(get_screen_size=lambda: (400, 800))),
                           state_provider=lambda: current[0], target_package="com.app")
    assert explorer.execute_action({"content_desc": "深层设置", "scroll_page": 1}, require_live_locator=True)
    tap.assert_called_once_with(50, 350)


def test_scroll_selected_chip_disappearing_is_not_page_drift(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    base = state(elements=[element(), {**element(text="Selected", rid="id/chip"), "selected": True}])
    after = state(elements=[element(text="Below", rid="id/below")])
    current = [base]
    count = [0]
    def swipe(*args, **kw):
        count[0] += 1
        current[0] = after if count[0] == 1 else base
        return {"ok": True}
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe)),
                           state_provider=lambda: current[0], target_package="com.app")
    result = explorer.stitch_scrollable_state(base)
    assert any(el["text"] == "Below" for el in result["elements"])
    assert count[0] == 2
    assert explorer._current_scroll_page == 0


def test_host_only_state_cannot_bypass_explicit_host_constraint():
    from phone_pilot.core.ui.graph import UIKnowledgeGraph, UINode
    from phone_pilot.core.ui.page import PageSignature
    node = UINode("target", PageSignature(), host_node_id="host-a")
    assert not UIKnowledgeGraph().state_matches_node({"node_id": "target", "host_node_id": "host-b"}, node)


def test_navigator_guard_rejects_custom_scroll_fallback():
    from phone_pilot.core.ui.graph import UIKnowledgeGraph
    from phone_pilot.core.ui.navigator import UINavigator
    action = Mock(return_value={"ok": False})
    nav = UINavigator(UIKnowledgeGraph(), state_provider=lambda: state(package="com.other"),
                      action_executor=action, action_guard=lambda _: False)
    assert not nav._try_scroll({"scrollable": True})
    action.assert_not_called()


def test_parent_restored_viewport_does_not_scroll_again(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    parent = state(elements=[element()])
    child = state("child", elements=[element(text="Child", rid="id/child")])
    current = [parent]
    swipe = Mock(return_value={"ok": True})
    tap = Mock(side_effect=lambda *args: (current.__setitem__(0, child), {"ok": True})[1])
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe, tap=tap),
                           screen=SimpleNamespace(get_screen_size=lambda: (400, 800))),
                           state_provider=lambda: current[0], target_package="com.app")
    assert explorer.execute_action({**element(), "scroll_page": 1})
    current[0] = parent  # Actual Back restored the same previous viewport.
    swipe.reset_mock()
    assert explorer.execute_action({**element(), "scroll_page": 1})
    swipe.assert_not_called()


def test_title_scrolling_offscreen_still_stitches_and_restores(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    base = state(elements=[element(), element(text="Settings", rid="id/page_title")])
    after = state(elements=[element(text="Below", rid="id/below")])
    current = [base]
    calls = [0]
    def swipe(*args, **kw):
        calls[0] += 1
        current[0] = after if calls[0] == 1 else base
        return {"ok": True}
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe)),
                           state_provider=lambda: current[0], target_package="com.app")
    stitched = explorer.stitch_scrollable_state(base)
    assert any(el["text"] == "Below" for el in stitched["elements"])
    assert calls[0] == 2
    assert explorer._current_scroll_page == 0


def test_shared_control_ids_distinct_titles_do_not_restore_parent_offset(monkeypatch):
    monkeypatch.setattr("phone_pilot.core.ui.explorer.time.sleep", lambda _: None)
    parent = state(elements=[element(rid="id/menu_row"), element(text="Parent", rid="id/toolbar_title")])
    child = state(elements=[element(rid="id/menu_row"), element(text="Child", rid="id/toolbar_title")])
    parent.pop("node_id")
    child.pop("node_id")
    current = [parent]
    swipe = Mock(return_value={"ok": True})
    tap = Mock(side_effect=lambda *args: (current.__setitem__(0, child), {"ok": True})[1])
    explorer = AppExplorer(driver=SimpleNamespace(input=SimpleNamespace(swipe=swipe, tap=tap),
                           screen=SimpleNamespace(get_screen_size=lambda: (400, 800))),
                           state_provider=lambda: current[0], target_package="com.app")
    assert explorer.execute_action({**element(rid="id/menu_row"), "scroll_page": 1})
    swipe.reset_mock()
    assert explorer.execute_action(element(rid="id/menu_row"))
    swipe.assert_not_called()
