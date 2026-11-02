#!/usr/bin/env python3
"""E2E Test: iOS Interstitial Lifecycle Funnel (类人脑页面记忆图谱装配验证大脚本).

通过 PageNode 原子页面记忆与装配流程，验证 iOS XAdSDKDevSample 插屏广告从导航、
加载样式到成功曝光全链路。
"""

import sys
import time
from phone_pilot.core.driver_factory import create_driver
from phone_pilot.core.ui.page import PageNode, PageSignature
from phone_pilot.mcp._page_state_tools import _collect_page_elements


# ===========================================================================
# 1. 业务页面记忆节点 (Page Memory Nodes)
# ===========================================================================


class IOSDemoMainPage(PageNode):
    """Demo App 首页记忆节点。"""

    signature = PageSignature(
        name="IOSDemoMainPage",
        required_elements=["AdMasterSDK Demo", "Interstitial", "Rewarded Video"],
    )

    def tap_interstitial(self) -> dict:
        print("  👉 在主页点击 [Interstitial] 进入插屏测试菜单")
        return self.tap_text("Interstitial")


class IOSInterstitialStylePage(PageNode):
    """插屏规格选择页记忆节点。"""

    signature = PageSignature(
        name="IOSInterstitialStylePage",
        required_elements=["16:9 Video", "9:16 Video", "16:9 Image"],
    )

    def select_16_9_video(self) -> dict:
        print("  👉 选择并触发 [16:9 Video] 插屏视频广告加载")
        return self.tap_text("16:9 Video")


class IOSAdDisplayStage(PageNode):
    """插屏广告展示渲染态记忆节点。"""

    signature = PageSignature(
        name="IOSAdDisplayStage",
        required_elements=["Install"],  # 广告物料渲染出 Install 行动按钮
    )

    def is_rendered(self) -> bool:
        return self.is_current()


# ===========================================================================
# 2. 模拟器测试执行驱动适配层
# ===========================================================================


class IOSTestContext:
    """包装 iOS 模拟器驱动，提供 PageNode 所需的统一接口。"""

    def __init__(
        self,
        udid: str = "4B50275C-4FE3-4934-9858-BF3C2D91DCFB",
        bundle_id: str = "com.baidujp.gbusdkdemo1",
    ) -> None:
        self.udid = udid
        self.bundle_id = bundle_id
        self.driver = create_driver(udid, platform="ios")

    def get_page_state(self) -> dict:
        try:
            elements, scrollable, all_texts = _collect_page_elements(self.driver)
            return {"elements": elements, "scrollable": scrollable, "all_texts": all_texts}
        except Exception as e:
            return {"elements": [], "error": str(e)}

    def tap_when_ready(self, text: str, timeout_s: float = 8.0) -> dict:
        # 在当前页面寻找包含 text 的元素并触发点击
        state = self.get_page_state()
        elements = state.get("elements", [])
        for elem in elements:
            lbl = elem.get("label") or elem.get("text") or elem.get("resource_id") or ""
            if text in lbl:
                center = elem.get("center")
                if center and len(center) >= 2:
                    cx, cy = center[0], center[1]
                    input_driver = self.driver.input if callable(self.driver.input) else self.driver.input
                    if callable(input_driver):
                        res = input_driver().tap(cx, cy)
                    else:
                        res = input_driver.tap(cx, cy)
                    return {"ok": True, "tapped": text, "center": [cx, cy], "driver_res": res}
        return {"ok": False, "error": f"Element with text '{text}' not found"}

    def launch_app(self) -> bool:
        app_driver = self.driver.app if callable(self.driver.app) else self.driver.app
        if callable(app_driver):
            res = app_driver().launch_app(self.bundle_id)
        else:
            res = app_driver.launch_app(self.bundle_id)
        return bool(res.get("ok", True))


# ===========================================================================
# 3. 门禁验证管线 (Funnel Pipeline)
# ===========================================================================


def run_test() -> bool:
    print("=================================================================")
    print("🚀 开始执行 iOS 插屏广告生命周期门禁大脚本")
    print("📱 设备 UDID: 4B50275C-4FE3-4934-9858-BF3C2D91DCFB")
    print("📦 Bundle ID: com.baidujp.gbusdkdemo1")
    print("=================================================================")

    ctx = IOSTestContext()

    # Step 0: 检查或重新拉起 App
    print("\n▶ [Step 0] 确认目标 Demo App 处于前台...")
    ctx.launch_app()
    time.sleep(1.0)

    # Step 1: 主页识别与导航
    print("\n▶ [Step 1] 校验主页认知特征指纹...")
    main_page = IOSDemoMainPage(ctx)
    if not main_page.is_current():
        print("  ℹ️ 当前不在主页，尝试点击顶部返回按钮回到主页...")
        ctx.tap_when_ready("AdMasterSDK Demo")
        time.sleep(1.2)

    if main_page.is_current():
        print("  ✓ 命中主页指纹: IOSDemoMainPage")
    else:
        print("  ℹ️ 页面当前在插屏子页，继续后续验证...")

    # Step 2: 进入插屏规格选择页
    style_page = IOSInterstitialStylePage(ctx)
    if not style_page.is_current():
        print("\n▶ [Step 2] 在主页触发跳转至插屏调试页...")
        main_page.tap_interstitial()
        time.sleep(1.5)

    if style_page.wait_until_current(timeout_s=8.0):
        print("  ✓ 成功转移至页面: IOSInterstitialStylePage")
    else:
        print("  ❌ 未能进入插屏规格页")
        return False

    # Step 3: 触发 16:9 Video 广告加载并渲染
    print("\n▶ [Step 3] 触发广告加载动作: [16:9 Video]...")
    style_page.select_16_9_video()
    time.sleep(2.0)

    # Step 4: 断言广告展示态
    print("\n▶ [Step 4] 断言广告展示态指纹 (包含转化动作按钮 Install)...")
    ad_stage = IOSAdDisplayStage(ctx)
    if ad_stage.wait_until_current(timeout_s=10.0):
        print("  ✓ 成功命中广告展示态: IOSAdDisplayStage [Install 按钮正常曝光渲染]")
    else:
        print("  ❌ 广告展示态未能按预期渲染")
        return False

    print("\n=================================================================")
    print("🎉 [SUCCESS] iOS 插屏广告生命周期 E2E 验证 100% 成功！")
    print("   - 页面记忆流转: MainPage -> InterstitialStylePage -> AdDisplayStage")
    print("   - 广告物料与转化能力验证完成")
    print("=================================================================")
    return True


if __name__ == "__main__":
    success = run_test()
    sys.exit(0 if success else 1)
