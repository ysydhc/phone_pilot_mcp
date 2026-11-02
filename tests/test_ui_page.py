"""PageNode & PageFlowAssembler 页面记忆图谱与脚本编译器单元测试。"""

import unittest
from phone_pilot.core.ui.page import (
    PageNode,
    PageSignature,
    PageFlowAssembler,
    PageTransition,
)


class MockDeviceContext:
    """Mock 设备上下文，用于测试页面指纹匹配与动作流。"""

    def __init__(self, initial_state: dict = None) -> None:
        self.current_state = initial_state or {}
        self.actions_called: list[str] = []

    def get_page_state(self) -> dict:
        return self.current_state

    def tap_when_ready(self, text: str, timeout_s: float = 5.0) -> dict:
        self.actions_called.append(f"tap:{text}")
        return {"ok": True, "tapped": text}


# 页面定义定义
class MockMainPage(PageNode):
    signature = PageSignature(
        name="MainPage",
        required_elements=["Interstitial", "Rewarded Video"],
        forbidden_elements=["Ad Playing", "Close Ad"],
    )

    def open_interstitial(self) -> dict:
        return self.tap_text("Interstitial")


class MockInterstitialPage(PageNode):
    signature = PageSignature(
        name="InterstitialPage",
        required_elements=["Load Ad", "Show Ad"],
        forbidden_elements=["Rewarded Video"],
    )

    def load_and_show(self) -> dict:
        res1 = self.tap_text("Load Ad")
        res2 = self.tap_text("Show Ad")
        return {"load": res1, "show": res2}


class TestPageSignature(unittest.TestCase):
    """测试页面认知特征指纹判断逻辑。"""

    def test_signature_matches(self):
        sig = PageSignature(
            name="SamplePage",
            required_elements=["Settings", "Profile"],
            forbidden_elements=["Login Required"],
        )

        # 匹配成功场景
        state_ok = {
            "elements": [
                {"label": "Welcome to App"},
                {"text": "Go to Settings"},
                {"identifier": "btn_Profile"},
            ]
        }
        self.assertTrue(sig.matches(state_ok))

        # 缺失必要元素场景
        state_missing = {
            "elements": [
                {"label": "Welcome to App"},
                {"text": "Go to Settings"},
            ]
        }
        self.assertFalse(sig.matches(state_missing))
        diff = sig.explain_diff(state_missing)
        self.assertIn("Profile", diff["missing_required"])

        # 命中禁止元素场景
        state_forbidden = {
            "elements": [
                {"text": "Settings"},
                {"label": "Profile"},
                {"text": "Login Required now"},
            ]
        }
        self.assertFalse(sig.matches(state_forbidden))
        diff = sig.explain_diff(state_forbidden)
        self.assertIn("Login Required", diff["hit_forbidden"])


class TestPageNodeAndAssembler(unittest.TestCase):
    """测试 PageNode 与 PageFlowAssembler。"""

    def test_page_is_current(self):
        ctx = MockDeviceContext(
            initial_state={
                "elements": [
                    {"label": "Banner"},
                    {"text": "Interstitial"},
                    {"label": "Rewarded Video"},
                ]
            }
        )
        main_page = MockMainPage(ctx)
        self.assertTrue(main_page.is_current())

        # 切换到插屏页面状态
        ctx.current_state = {
            "elements": [
                {"label": "Load Ad"},
                {"text": "Show Ad"},
            ]
        }
        self.assertFalse(main_page.is_current())
        interstitial_page = MockInterstitialPage(ctx)
        self.assertTrue(interstitial_page.is_current())

    def test_flow_assembler_run_success(self):
        ctx = MockDeviceContext(
            initial_state={
                "elements": [
                    {"text": "Interstitial"},
                    {"label": "Rewarded Video"},
                ]
            }
        )

        assembler = PageFlowAssembler(
            case_name="test_interstitial_sample_flow",
            app_bundle="com.test.sample",
            platform="ios",
        )

        def switch_to_interstitial(page: MockMainPage):
            res = page.open_interstitial()
            # 模拟点击后页面状态发生转移
            ctx.current_state = {
                "elements": [
                    {"label": "Load Ad"},
                    {"text": "Show Ad"},
                ]
            }
            return res

        assembler.add_step(
            name="open_interstitial",
            page_cls=MockMainPage,
            action=switch_to_interstitial,
            expect_page=MockInterstitialPage,
        )
        assembler.add_step(
            name="load_and_show_ad",
            page_cls=MockInterstitialPage,
            action="load_and_show",
        )

        result = assembler.run(ctx)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["step"], "open_interstitial")
        self.assertEqual(result["steps"][1]["step"], "load_and_show_ad")

    def test_compile_to_standalone_script(self):
        assembler = PageFlowAssembler(
            case_name="test_ios_interstitial_funnel",
            app_bundle="com.baidujp.gbusdkdemo1",
            platform="ios",
            description="Auto-generated E2E Lifecycle Funnel Test",
        )
        assembler.add_step(
            name="enter_interstitial",
            page_cls=MockMainPage,
            action="open_interstitial",
            expect_page=MockInterstitialPage,
        )
        assembler.add_step(
            name="load_and_show",
            page_cls=MockInterstitialPage,
            action="load_and_show",
        )
        assembler.add_log_assertion(
            expected_events=["interstitial_call", "interstitial_request", "interstitial_show"]
        )

        code = assembler.compile_to_script()
        self.assertIn("class MockMainPage(PageNode):", code)
        self.assertIn("class MockInterstitialPage(PageNode):", code)
        self.assertIn("def run_test(ctx: ScriptContext) -> bool:", code)
        self.assertIn("interstitial_call", code)
        self.assertIn("interstitial_show", code)


if __name__ == "__main__":
    unittest.main()
