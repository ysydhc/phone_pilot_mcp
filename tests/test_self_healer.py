"""Tests for the error self-healing system.

覆盖范围：
- HealingExperienceStore: CRUD / 查询 / 反馈 / 清理 / 统计
- PopupGuard: UI 树检测 / 文本规则 / 关闭逻辑
- SelfHealer: 管线编排 / 经验复用 / LLM fallback
- LLM healer: JSON 解析 / 响应处理
- RunSession: healing_events.json 持久化
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

from phone_pilot.core.healing_experience import (
    HealingExperienceStore,
    HealingRecord,
    _safe_filename,
)
from phone_pilot.extensions.llm.healer import (
    LLMHealSuggestion,
    _build_ui_summary,
    _parse_llm_response,
)
from phone_pilot.script_api.popup_guard import (
    DEFAULT_RULES,
    PopupGuard,
    PopupRule,
)
from phone_pilot.script_api.self_healer import (
    HealAction,
    HealEvent,
    SelfHealer,
    get_self_healer,
)


# ---------------------------------------------------------------------------
# Helpers / Mocks
# ---------------------------------------------------------------------------

class MockUINode:
    """Minimal UINode mock for testing."""

    def __init__(
        self,
        text: str = "",
        content_desc: str = "",
        resource_id: str = "",
        class_name: str = "",
        clickable: bool = False,
        bounds: str = "",
        _center_x: int = 0,
        _center_y: int = 0,
    ):
        self.text = text
        self.content_desc = content_desc
        self.resource_id = resource_id
        self.class_name = class_name
        self.clickable = clickable
        self.bounds = bounds
        self._center_x = _center_x
        self._center_y = _center_y

    def center(self) -> Optional[tuple[int, int]]:
        if self._center_x and self._center_y:
            return (self._center_x, self._center_y)
        return None

    def bounds_tuple(self) -> Optional[tuple[int, int, int, int]]:
        if not self.bounds:
            return None
        import re
        m = re.search(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", self.bounds)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        return None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "content_desc": self.content_desc,
            "resource_id": self.resource_id,
            "class_name": self.class_name,
            "clickable": self.clickable,
            "bounds": self.bounds,
        }


def _make_mock_ctx(
    platform: str = "android",
    popup_guard: bool = True,
    llm_healing: bool = False,
) -> MagicMock:
    """Create a mock ScriptContext for testing."""
    ctx = MagicMock()
    ctx.popup_guard = popup_guard
    ctx.llm_healing = llm_healing
    ctx.max_heal_attempts = 3
    ctx.popup_rules = None
    ctx.device_serial = "mock_serial"
    ctx.driver.platform = platform
    ctx.driver.ui.dump_ui_nodes.return_value = []
    ctx.driver.ui.get_current_activity.return_value = {
        "package": "com.test.app",
        "activity": "com.test.app.MainActivity",
    }
    ctx.driver.screen.get_screen_size.return_value = (1080, 2400)
    ctx.driver.screen.screenshot.return_value = b"fake_png"
    ctx._self_healer = None
    return ctx


# ---------------------------------------------------------------------------
# HealingExperienceStore Tests
# ---------------------------------------------------------------------------

class TestHealingRecord(unittest.TestCase):
    """Test HealingRecord dataclass."""

    def test_default_values(self):
        rec = HealingRecord(action="find_text", query="hello")
        self.assertEqual(rec.action, "find_text")
        self.assertTrue(rec.record_id)
        self.assertTrue(rec.created_at)
        self.assertEqual(rec.hit_count, 0)

    def test_success_rate(self):
        rec = HealingRecord(hit_count=7, fail_count=3, success=True)
        self.assertAlmostEqual(rec.success_rate, 0.7)

    def test_success_rate_zero_total(self):
        rec = HealingRecord(success=True)
        self.assertEqual(rec.success_rate, 1.0)
        rec2 = HealingRecord(success=False)
        self.assertEqual(rec2.success_rate, 0.0)

    def test_serialization_roundtrip(self):
        rec = HealingRecord(
            action="find_text",
            query="OK",
            app_package="com.test",
            activity="Main",
            fix_type="popup_dismiss",
            fix_detail={"dismiss_text": "确定"},
        )
        d = rec.to_dict()
        rec2 = HealingRecord.from_dict(d)
        self.assertEqual(rec.action, rec2.action)
        self.assertEqual(rec.fix_detail, rec2.fix_detail)

    def test_from_dict_ignores_unknown_fields(self):
        d = {"action": "tap", "unknown_field": 42}
        rec = HealingRecord.from_dict(d)
        self.assertEqual(rec.action, "tap")


class TestHealingExperienceStore(unittest.TestCase):
    """Test HealingExperienceStore CRUD operations."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = HealingExperienceStore(store_dir=self.tmpdir)

    def test_learn_and_lookup(self):
        rec = HealingRecord(
            action="find_text",
            query="确定",
            app_package="com.test",
            activity="MainActivity",
            fix_type="popup_dismiss",
            fix_detail={"dismiss_text": "OK"},
            source="popup_guard",
            success=True,
        )
        self.store.learn(rec)

        results = self.store.lookup("find_text", "确定", "com.test", "MainActivity")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].fix_type, "popup_dismiss")

    def test_learn_merges_existing(self):
        rec = HealingRecord(
            action="find_text",
            query="OK",
            app_package="com.test",
            activity="Main",
            fix_type="popup_dismiss",
        )
        self.store.learn(rec)
        self.store.learn(rec)

        results = self.store.lookup("find_text", "OK", "com.test", "Main")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].hit_count, 2)

    def test_lookup_fallback_to_generic(self):
        rec = HealingRecord(
            action="find_text",
            query="other_text",
            app_package="com.test",
            activity="Main",
            fix_type="popup_dismiss",
            success=True,
            hit_count=5,
        )
        self.store.learn(rec)

        # Lookup for a different query on same page → should fallback
        results = self.store.lookup("find_text", "not_found", "com.test", "Main")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].query, "other_text")

    def test_report_outcome_success(self):
        rec = HealingRecord(
            action="find_text",
            query="OK",
            app_package="com.test",
            activity="Main",
            fix_type="popup_dismiss",
        )
        learned = self.store.learn(rec)
        rid = learned.record_id

        updated = self.store.report_outcome(
            rid, True, package="com.test", activity="Main"
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.hit_count, 2)  # learn=1 + report=1

    def test_report_outcome_failure(self):
        rec = HealingRecord(
            action="find_text",
            query="OK",
            app_package="com.test",
            activity="Main",
            fix_type="popup_dismiss",
        )
        learned = self.store.learn(rec)
        self.store.report_outcome(
            learned.record_id, False, package="com.test", activity="Main"
        )

        results = self.store.lookup("find_text", "OK", "com.test", "Main")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].fail_count, 1)

    def test_prune_removes_old_records(self):
        rec = HealingRecord(
            action="old_action",
            query="old",
            app_package="com.test",
            activity="Main",
            fix_type="popup_dismiss",
        )
        self.store.learn(rec)
        # Manually set old timestamp
        records = self.store._load_activity("com.test", "Main")
        for r in records:
            r.last_hit_at = "2020-01-01T00:00:00"
        self.store._save_activity("com.test", "Main", records)
        # Clear cache to reload from disk
        self.store._cache.clear()

        pruned = self.store.prune(max_age_days=1)
        self.assertEqual(pruned, 1)

    def test_get_stats(self):
        self.store.learn(HealingRecord(
            action="a", query="q", app_package="p", activity="act",
            fix_type="popup_dismiss", source="popup_guard",
        ))
        stats = self.store.get_stats()
        self.assertEqual(stats["total_records"], 1)
        self.assertIn("popup_dismiss", stats["by_fix_type"])

    def test_global_experience(self):
        rec = HealingRecord(
            action="find_text", query="global_query",
            fix_type="wait_increase", source="llm_analysis",
        )
        self.store.learn_global(rec)

        results = self.store.lookup("find_text", "global_query", "any_pkg", "any_act")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].fix_type, "wait_increase")


class TestSafeFilename(unittest.TestCase):
    def test_package_name(self):
        self.assertEqual(_safe_filename("com.example.app"), "com.example.app")

    def test_slashes(self):
        self.assertEqual(_safe_filename("a/b\\c"), "a_b_c")

    def test_empty(self):
        self.assertEqual(_safe_filename(""), "_unknown")

    def test_special_chars(self):
        result = _safe_filename("com.test@#$%")
        self.assertNotIn("@", result)


# ---------------------------------------------------------------------------
# PopupGuard Tests
# ---------------------------------------------------------------------------

class TestPopupGuard(unittest.TestCase):
    """Test PopupGuard detection and dismissal."""

    def test_default_rules_not_empty(self):
        self.assertGreater(len(DEFAULT_RULES), 0)

    def test_no_popup_returns_none(self):
        ctx = _make_mock_ctx()
        ctx.driver.ui.dump_ui_nodes.return_value = [
            MockUINode(text="Hello World", class_name="TextView"),
        ]
        pg = PopupGuard(ctx)
        result = pg.check_and_dismiss()
        self.assertIsNone(result)

    def test_detect_dialog_class_name(self):
        ctx = _make_mock_ctx()
        ctx.driver.ui.dump_ui_nodes.return_value = [
            MockUINode(
                text="权限提示",
                class_name="android.app.AlertDialog",
            ),
            MockUINode(
                text="允许",
                resource_id="com.android.permissioncontroller:id/permission_allow_button",
                clickable=True,
                _center_x=540,
                _center_y=1200,
            ),
        ]
        pg = PopupGuard(ctx)
        result = pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertIn("dismiss_text", result)

    def test_detect_by_text_rule(self):
        ctx = _make_mock_ctx()
        ctx.driver.ui.dump_ui_nodes.return_value = [
            MockUINode(text="系统更新提示"),
            MockUINode(
                text="稍后",
                clickable=True,
                _center_x=540,
                _center_y=1200,
            ),
        ]
        pg = PopupGuard(ctx)
        result = pg.check_and_dismiss()
        self.assertIsNotNone(result)

    def test_custom_rule(self):
        ctx = _make_mock_ctx()
        custom = PopupRule(
            name="custom_ad",
            detect_texts=["广告"],
            dismiss_texts=["关闭广告"],
            priority=200,
        )
        ctx.driver.ui.dump_ui_nodes.return_value = [
            MockUINode(text="广告"),
            MockUINode(text="关闭广告", clickable=True, _center_x=100, _center_y=100),
        ]
        pg = PopupGuard(ctx, rules=[custom])
        result = pg.check_and_dismiss()
        self.assertIsNotNone(result)
        self.assertEqual(result.get("rule_name"), "custom_ad")

    def test_add_rule(self):
        ctx = _make_mock_ctx()
        pg = PopupGuard(ctx)
        initial = len(pg._rules)
        pg.add_rule(PopupRule(name="test", priority=999))
        self.assertEqual(len(pg._rules), initial + 1)
        self.assertEqual(pg._rules[0].name, "test")  # Highest priority first


# ---------------------------------------------------------------------------
# LLM Healer Tests
# ---------------------------------------------------------------------------

class TestLLMHealSuggestion(unittest.TestCase):
    def test_default_values(self):
        s = LLMHealSuggestion()
        self.assertEqual(s.fix_type, "other")
        self.assertEqual(s.fix_detail, {})

    def test_custom_values(self):
        s = LLMHealSuggestion(
            fix_type="popup_dismiss",
            confidence=0.95,
            reason="检测到弹窗",
            fix_detail={"dismiss_text": "OK"},
        )
        self.assertEqual(s.fix_type, "popup_dismiss")
        self.assertEqual(s.confidence, 0.95)


class TestBuildUISummary(unittest.TestCase):
    def test_empty(self):
        result = _build_ui_summary([])
        self.assertIn("无 UI 元素", result)

    def test_with_nodes(self):
        nodes = [
            {"text": "Hello", "class_name": "TextView", "clickable": True},
            {"text": "", "content_desc": "icon", "resource_id": "btn:id/ok"},
        ]
        result = _build_ui_summary(nodes)
        self.assertIn("Hello", result)
        self.assertIn("icon", result)


class TestParseLLMResponse(unittest.TestCase):
    def test_valid_json(self):
        text = '{"fix_type": "popup_dismiss", "confidence": 0.9, "reason": "弹窗", "fix_detail": {"dismiss_text": "OK"}}'
        result = _parse_llm_response(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.fix_type, "popup_dismiss")
        self.assertAlmostEqual(result.confidence, 0.9)

    def test_json_in_markdown(self):
        text = '```json\n{"fix_type": "scroll", "confidence": 0.8, "reason": "not visible", "fix_detail": {"direction": "down"}}\n```'
        result = _parse_llm_response(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.fix_type, "scroll")

    def test_invalid_response(self):
        text = "I don't know what happened"
        result = _parse_llm_response(text)
        self.assertIsNone(result)

    def test_partial_json(self):
        text = 'Some explanation: {"fix_type": "wait_increase", "confidence": 0.6, "reason": "loading", "fix_detail": {"extra_wait_s": 3}}'
        result = _parse_llm_response(text)
        self.assertIsNotNone(result)
        self.assertEqual(result.fix_type, "wait_increase")


# ---------------------------------------------------------------------------
# SelfHealer Tests
# ---------------------------------------------------------------------------

class TestSelfHealer(unittest.TestCase):
    """Test SelfHealer pipeline orchestration."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.ctx = _make_mock_ctx()

    def test_try_heal_experience_hit(self):
        store = HealingExperienceStore(store_dir=self.tmpdir)
        store.learn(HealingRecord(
            action="find_text",
            query="hello",
            app_package="com.test.app",
            activity="com.test.app.MainActivity",
            fix_type="popup_dismiss",
            fix_detail={"dismiss_text": "OK"},
            success=True,
            hit_count=10,
        ))

        pg = PopupGuard(self.ctx)
        healer = SelfHealer(self.ctx, experience_store=store, popup_guard=pg)

        action = healer.try_heal("find_text", "hello", "element_not_found")
        self.assertIsNotNone(action)
        self.assertEqual(action.source, "experience")
        self.assertEqual(action.fix_type, "popup_dismiss")

    def test_try_heal_popup_guard(self):
        store = HealingExperienceStore(store_dir=self.tmpdir)
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockUINode(text="系统更新"),
            MockUINode(text="稍后", clickable=True, _center_x=540, _center_y=1200),
        ]

        pg = PopupGuard(self.ctx)
        healer = SelfHealer(self.ctx, experience_store=store, popup_guard=pg)

        action = healer.try_heal("find_text", "target", "element_not_found")
        self.assertIsNotNone(action)
        self.assertEqual(action.source, "popup_guard")
        self.assertEqual(action.fix_type, "popup_dismiss")

    def test_try_heal_no_issue_returns_none(self):
        store = HealingExperienceStore(store_dir=self.tmpdir)
        pg = PopupGuard(self.ctx)
        healer = SelfHealer(self.ctx, experience_store=store, popup_guard=pg)

        action = healer.try_heal("find_text", "target", "element_not_found")
        self.assertIsNone(action)

    def test_report_outcome_updates_store(self):
        store = HealingExperienceStore(store_dir=self.tmpdir)
        learned = store.learn(HealingRecord(
            action="find_text",
            query="hello",
            app_package="com.test.app",
            activity="com.test.app.MainActivity",
            fix_type="popup_dismiss",
            success=True,
            hit_count=5,
        ))

        pg = PopupGuard(self.ctx)
        healer = SelfHealer(self.ctx, experience_store=store, popup_guard=pg)

        heal = HealAction(
            fix_type="popup_dismiss",
            source="experience",
            record_id=learned.record_id,
        )
        healer.report_outcome(heal, True)

        # Verify store was updated
        results = store.lookup("find_text", "hello", "com.test.app", "com.test.app.MainActivity")
        self.assertEqual(results[0].hit_count, 6)

    def test_healing_events_recorded(self):
        store = HealingExperienceStore(store_dir=self.tmpdir)
        self.ctx.driver.ui.dump_ui_nodes.return_value = [
            MockUINode(text="知道了", clickable=True, _center_x=540, _center_y=1200),
        ]

        pg = PopupGuard(self.ctx)
        healer = SelfHealer(self.ctx, experience_store=store, popup_guard=pg)
        healer.try_heal("find_text", "x", "element_not_found")

        events = healer.healing_events
        self.assertGreater(len(events), 0)
        self.assertEqual(events[0].fix_type, "popup_dismiss")

    def test_heal_event_to_dict(self):
        event = HealEvent(
            timestamp="2026-02-06T10:00:00",
            action="find_text",
            query="hello",
            fix_type="popup_dismiss",
            success=True,
        )
        d = event.to_dict()
        self.assertEqual(d["action"], "find_text")
        self.assertTrue(d["success"])


class TestGetSelfHealer(unittest.TestCase):
    """Test lazy initialization via get_self_healer."""

    def test_returns_healer_when_popup_guard_enabled(self):
        ctx = _make_mock_ctx(popup_guard=True)
        healer = get_self_healer(ctx)
        self.assertIsNotNone(healer)
        self.assertIsInstance(healer, SelfHealer)

    def test_returns_none_when_popup_guard_disabled(self):
        ctx = _make_mock_ctx(popup_guard=False)
        healer = get_self_healer(ctx)
        self.assertIsNone(healer)

    def test_caches_healer_on_ctx(self):
        ctx = _make_mock_ctx(popup_guard=True)
        h1 = get_self_healer(ctx)
        h2 = get_self_healer(ctx)
        self.assertIs(h1, h2)


# ---------------------------------------------------------------------------
# RunSession healing_events integration
# ---------------------------------------------------------------------------

class TestRunSessionHealingEvents(unittest.TestCase):
    """Test saving healing events to RunSession."""

    def test_save_healing_events(self):
        from phone_pilot.core.run_store import RunSession

        tmpdir = tempfile.mkdtemp()
        session = RunSession(Path(tmpdir), script_name="test")

        events = [
            {"timestamp": "2026-02-06T10:00:00", "action": "find_text", "fix_type": "popup_dismiss", "success": True},
            {"timestamp": "2026-02-06T10:00:05", "action": "find_image", "fix_type": "llm_fix", "success": False},
        ]
        path = session.save_healing_events(events)

        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["healing_events"]), 2)


# ---------------------------------------------------------------------------
# Cross-script experience reuse
# ---------------------------------------------------------------------------

class TestCrossScriptExperienceReuse(unittest.TestCase):
    """Verify experience learned in one script is available to another."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_cross_script_reuse(self):
        # Script A learns an experience
        store_a = HealingExperienceStore(store_dir=self.tmpdir)
        store_a.learn(HealingRecord(
            action="find_text",
            query="广告关闭",
            app_package="com.gravity",
            activity="com.gravity.TaskActivity",
            fix_type="popup_dismiss",
            fix_detail={"dismiss_text": "閉じる"},
            source="popup_guard",
            success=True,
            hit_count=3,
        ))

        # Script B creates a new store pointing to the same directory
        store_b = HealingExperienceStore(store_dir=self.tmpdir)
        results = store_b.lookup(
            "find_text", "広告関閉",
            "com.gravity", "com.gravity.TaskActivity",
        )
        # Should find via fallback (same action, same page, different query)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0].fix_type, "popup_dismiss")


if __name__ == "__main__":
    unittest.main()
