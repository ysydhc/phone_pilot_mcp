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

运行: python easy_use/example_script.py
"""
from __future__ import annotations


from phone_pilot.script_api import (
    ScriptContext,
    chain,
    find_text,
    retry,
    run_script,
    screenshot,
    scroll_to_find,
)

# ---------------------------------------------------------------------------
# 配置 / Configuration
# ---------------------------------------------------------------------------

PACKAGE = "com.example.app"  # 替换为你的应用包名 / Replace with your app package

ctx = ScriptContext(
    # device_serial="your_device",  # 可选，单设备时自动检测
    auto_screenshot=True,            # 每步自动截图
    auto_dump_hprof=False,           # 成功后自动 dump 内存（可选）
    auto_report=True,                # 结束后自动生成 HTML 报告（默认开启）
    popup_guard=True,                # 弹窗自动检测与关闭（默认开启）
    llm_healing=False,               # LLM 全局分析兜底（需 OPENAI_API_KEY）
    max_heal_attempts=3,             # 单步最大自愈尝试次数
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

    # Step 3: 使用正则匹配动态文本
    # Use regex for dynamic text
    elem = find_text(ctx, r"re:\d+\.\d+K", retry_attempts=3)
    if elem:
        elem.tap(wait=1)

    # Step 4: 滚动查找（页面内容不在当前视图）
    # Scroll to find element not in current view
    target = scroll_to_find(ctx, "目标文本", direction="up_down", max_count=5)
    target.tap(wait=2)

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


if __name__ == "__main__":
    run_script(main, ctx=ctx)
