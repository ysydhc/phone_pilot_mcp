#!/usr/bin/env python3
"""phone_pilot 自动化脚本示例。
Example automation script for phone_pilot.

演示核心功能：
- 清理后台 & 启动应用
- 文本查找 & 点击（含弹窗自愈）
- 滚动查找
- 日志监控 & 录屏
- 截图保存
- 错误自愈（弹窗自动关闭 + LLM 分析兜底）
- HTML 可视化报告（脚本结束后自动生成）

进阶能力（本文件后半段演示，Agent 生成脚本时优先参考）：
- 健壮交互：tap_when_ready / safe_tap / safe_close（替代裸 sleep + tap，避免时序误触）
- 等待条件：wait_for_element_appear / wait_for_activity_change
- 日志断言：logcat_assert_sequence（验证事件按顺序发生）
- 网络打桩：mock_server_start / mock_set_rule / mock_assert_switched
- 用例编排：TestCase + run_cases（区分被测缺陷 fail 与环境问题 EnvError）

运行: python easy_use/example_script.py
"""

from __future__ import annotations


from phone_pilot.script_api import (
    ScriptContext,
    TestCase,
    chain,
    find_text,
    logcat_assert_sequence,
    mock_assert_switched,
    mock_server_start,
    mock_set_rule,
    retry,
    run_cases,
    run_script,
    safe_close,
    safe_tap,
    screenshot,
    scroll_to_find,
    tap_when_ready,
    wait_for_activity_change,
    wait_for_element_appear,
)

# ---------------------------------------------------------------------------
# 配置 / Configuration
# ---------------------------------------------------------------------------

PACKAGE = "com.example.app"  # 替换为你的应用包名 / Replace with your app package

ctx = ScriptContext(
    # device_serial="your_device",  # 可选，单设备时自动检测
    auto_screenshot=True,  # 每步自动截图
    auto_dump_hprof=False,  # 成功后自动 dump 内存（可选）
    auto_report=True,  # 结束后自动生成 HTML 报告（默认开启）
    popup_guard=True,  # 弹窗自动检测与关闭（默认开启）
    llm_healing=False,  # LLM 全局分析兜底（需 OPENAI_API_KEY）
    max_heal_attempts=3,  # 单步最大自愈尝试次数
)


# ---------------------------------------------------------------------------
# 主流程 / Main Flow
# ---------------------------------------------------------------------------


def main() -> int:
    # Step 1: 清理后台 & 启动应用
    # Clear background & launch app
    chain(ctx).clear_background().restart_app_pkg(PACKAGE).wait(3).done()

    # Step 2: 查找文本并点击
    # Find text and tap
    find_text(ctx, "目标按钮", retry_attempts=5, retry_interval_ms=1000).tap(wait=2)

    # Step 2.1（推荐）: 用 tap_when_ready 替代"裸 find + tap"
    # 轮询等到元素"可点击"状态再点击，点击坐标与匹配来自同一次查询（无坐标漂移）；
    # 超时则不点击，避免点到尚未就绪的位置而误触。适合按钮渲染慢/动画未结束的场景。
    # Poll until the element is actually clickable, then tap. Safer than a fixed sleep.
    tap_when_ready(ctx, text="下一步", timeout_s=10, require_clickable=True)

    # Step 2.2（推荐）: 用 safe_tap 做带误触保护的坐标点击
    # 点击后校验前台状态：若跳出了当前 app（package 变化）或未到达 expect_activity，
    # 判定为误触并自动 go_back 复位。适合点击图片/广告位等无文本元素时防跳出。
    # Tap with mis-tap protection: auto go_back if it leaves the app / lands wrong page.
    safe_tap(ctx, x=540, y=1200, expect_package=PACKAGE)

    # Step 3: 使用正则匹配动态文本
    # Use regex for dynamic text
    elem = find_text(ctx, r"re:\d+\.\d+K", retry_attempts=3)
    if elem:
        elem.tap(wait=1)

    # Step 4: 滚动查找（页面内容不在当前视图）
    # Scroll to find element not in current view
    target = scroll_to_find(ctx, "目标文本", direction="up_down", max_count=5)
    target.tap(wait=2)

    # Step 4.1: 等待页面真正跳转到详情页后再继续
    # wait_for_activity_change 确认发生了"真实跳转"（记录基线 + 检测离开），
    # 不同于 wait_for_activity（若已在目标页会立即成功）。to_activity 可用后缀匹配。
    # Confirm a real navigation happened (baseline + leave), not just "already there".
    wait_for_activity_change(ctx, to_activity="DetailActivity", timeout_s=10)

    # Step 4.2: 若弹出全屏弹窗/广告，用 safe_close 完整关闭
    # 流程：等关闭按钮就绪 → 点击 → 验证按钮消失 → 失败则 go_back 回退重试；
    # 找不到按钮时可回退到固定坐标（带误触保护）。
    # Full close flow with retry + fallback coordinate.
    safe_close(ctx, resource_id="com.example.app:id/close", fallback_coord=(1000, 100))

    # Step 5: 开启日志和录屏
    # Start logcat capture and screen recording
    ctx.start_logcat("my_log", tag="MyApp", level="D")
    ctx.start_record("my_recording")

    # Step 6: 等待某个条件满足（带重试 + 自愈）
    # Wait for condition with retry + self-healing
    # 传入 ctx 后，retry 会在失败间隙调用 SelfHealer（弹窗关闭/LLM 分析）
    retry(
        lambda: find_text(ctx, "加载完成", retry_attempts=1),
        desc="等待加载完成",
        max_attempts=10,
        interval=1.5,
        ctx=ctx,  # 启用自愈 / Enable self-healing
    )

    # Step 6.1: 用 wait_for_element_appear 等元素出现（可要求可交互态）
    # 相比 retry+find_text 更语义化；require_clickable=True 时等到"可点击"才算出现。
    # Semantic wait; require_clickable ensures the element is interactive, not just present.
    wait_for_element_appear(ctx, text="提交", timeout_s=15, require_clickable=True)

    # Step 6.2: 断言业务日志按顺序出现（子序列匹配，中间可夹杂其它日志）
    # 常用于验证一条链路的关键事件确实按预期顺序发生，比逐条 search 更能表达时序意图。
    # Assert log patterns appear IN ORDER (subsequence match) within the timeout.
    logcat_assert_sequence(
        ctx,
        "click_submit",
        "request_start",
        "request_success",
        timeout_s=20,
    )

    # Step 7: 手动截图
    # Manual screenshot
    screenshot(ctx, "after_loading")

    # Step 8: 停止录屏和日志
    # Stop recording and logcat
    ctx.stop_record()
    ctx.stop_logcat("my_log")

    # Step 9: 最终截图
    screenshot(ctx, "final_result")

    return 0


# ---------------------------------------------------------------------------
# 进阶示例 A：网络打桩（Mock Server）
# Advanced example A: network mocking
# ---------------------------------------------------------------------------
# 场景：想让 App 的某个接口返回可控数据（如构造"空列表""异常状态"等边界），
# 用宿主机侧 mock server 打桩。start 会自动建立 adb reverse 隧道，
# run_script 结束时自动拆除。注意：仅明文 HTTP，App debug 包需允许 cleartext。
# Use case: stub an App API to return controlled data (e.g. empty / error states).


def demo_mock(ctx: ScriptContext) -> int:
    # 1. 启动 mock server（data_port/ctrl_port 传 0 表示自动分配端口）
    r = mock_server_start(ctx)
    if not r.get("ok"):
        print(f"mock 启动失败: {r.get('error')}")
        return 1

    # 2. 写规则：GET /api/config 固定返回下面这个 JSON body（默认 status=200）
    #    匹配基于 method + path（query string 会被忽略）。
    mock_set_rule(ctx, "GET", "/api/config", {"feature_enabled": True, "items": []})

    # 3. 触发 App 去请求该接口（此处示意：重启让 App 重新拉配置）
    chain(ctx).restart_app_pkg(PACKAGE).wait(3).done()

    # 4. 断言 App 确实把请求切到了 mock（收到 >= min_hits 个请求才算切换成功）。
    #    这一步同时是"App baseURL 是否正确指向 mock"的探针；超时会给出诊断提示。
    switched = mock_assert_switched(ctx, min_hits=1, timeout_s=10)
    if not switched.get("ok"):
        print(f"App 未切换到 mock: {switched.get('note')}")
        return 1

    screenshot(ctx, "mock_applied")
    return 0


# ---------------------------------------------------------------------------
# 进阶示例 B：用例编排（TestCase + run_cases）
# Advanced example B: test-case orchestration
# ---------------------------------------------------------------------------
# 场景：把一批相互独立的检查组织成用例批量跑。每个用例走 setup→act→check→teardown。
# 关键区分：
#   - 被测缺陷        → check 返回 False / act 抛普通异常 → 记为 fail（不重试）
#   - 环境问题(可重试) → 抛 EnvError（如 app 没起来）      → 整例清场后重跑
#   - 前置条件缺失     → 抛 SkipCase                        → 记为 skip


class LoginCase(TestCase):
    name = "登录后进入首页"

    def setup(self, ctx: ScriptContext) -> None:
        # 环境准备：保证 app 处于干净启动态。setup 必须能自足重建环境，
        # 这样 EnvError 触发的清场重试才有意义。
        chain(ctx).clear_background().restart_app_pkg(PACKAGE).wait(3).done()

    def act(self, ctx: ScriptContext) -> None:
        # 真机操作：被测的主流程
        tap_when_ready(ctx, text="登录", timeout_s=10)

    def check(self, ctx: ScriptContext) -> bool:
        # 判定：返回 None/True → pass；返回 False → fail
        return find_text(ctx, "首页") is not None

    def teardown(self, ctx: ScriptContext) -> None:
        # 复位：无论成败都会执行；这里回到干净态，供下一个用例复用
        chain(ctx).clear_background().done()


def demo_cases(ctx: ScriptContext) -> int:
    # 批量执行：env_error 的用例最多整例重跑 retry_env_errors 次；
    # max_total_s 是总时长预算，超出后剩余用例记为 skip(budget_exceeded)。
    result = run_cases(ctx, [LoginCase()], retry_env_errors=1, max_total_s=300)
    # result = {"ok", "total", "pass", "fail", "env_error", "error", "skip", "results": [...]}
    print(f"用例结果: pass={result['pass']} fail={result['fail']} ok={result['ok']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    # 默认运行基础主流程；需要演示进阶能力时，把 main 换成 demo_mock / demo_cases。
    # By default runs main(); swap for demo_mock / demo_cases to try advanced features.
    run_script(main, ctx=ctx)
