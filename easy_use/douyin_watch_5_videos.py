#!/usr/bin/env python3
"""抖音观看 5 个视频：回到桌面 → 清空后台 → 打开抖音 → 每 10 秒向上滑动到下一个视频，共 5 个。

使用 phone_pilot.script_api，安装 phone_pilot 后直接运行：
  python easy_use/douyin_watch_5_videos.py

包名默认 com.ss.android.ugc.aweme；极速版请设置环境变量：
  DOUYIN_PACKAGE=com.ss.android.ugc.aweme.lite
"""
from __future__ import annotations

import os
import time

from phone_pilot.core.log import _log as _runner_log
from phone_pilot.script_api import (
    ScriptContext,
    dump_ui,
    reset_home_screen,
    run_script,
    swipe_up,
)

# ---------------------------------------------------------------------------
# 配置 / Configuration
# ---------------------------------------------------------------------------

DEFAULT_PACKAGE = "com.ss.android.ugc.aweme"
STAY_SECONDS = 10
VIDEO_COUNT = 5

# 进入抖音后/每次滑动前检测：若出现这些文案视为阻塞页（登录/引导等），中止脚本
BLOCKING_KEYWORDS = ("登录", "同意", "青少年", "跳过", "验证", "获取验证码")

ctx = ScriptContext(
    auto_screenshot=True,
    auto_report=True,
    popup_guard=True,
    llm_healing=False,
    auto_meminfo=False,
)


def _douyin_package() -> str:
    return os.environ.get("DOUYIN_PACKAGE", "").strip() or DEFAULT_PACKAGE


def _is_blocking_page(ctx: ScriptContext) -> tuple[bool, str | None]:
    """检测当前是否为登录/引导等阻塞页（基于 UI dump 文本）。返回 (是否阻塞, 命中的关键词)。"""
    try:
        raw = dump_ui(ctx)
        for kw in BLOCKING_KEYWORDS:
            if kw in (raw or ""):
                return True, kw
    except Exception:
        pass
    return False, None


# ---------------------------------------------------------------------------
# 主流程 / Main Flow
# ---------------------------------------------------------------------------

def main() -> int:
    _runner_log("  Step 1: 回到桌面")
    reset_home_screen(ctx)
    time.sleep(0.5)

    package = _douyin_package()
    _runner_log(f"  Step 2: 打开抖音 (包名: {package})")
    launch_res = ctx.driver.app.launch_app(package)
    if not (isinstance(launch_res, dict) and launch_res.get("ok")):
        raise RuntimeError(f"launch_app({package}) 失败: {launch_res}")
    time.sleep(3)

    blocking, kw = _is_blocking_page(ctx)
    if blocking:
        raise RuntimeError(f"检测到阻塞页（含「{kw}」），无法继续滑动，请先手动登录或跳过引导")

    for i in range(VIDEO_COUNT - 1):
        _runner_log(f"  Step 3.{i + 1}: 等待 {STAY_SECONDS}s 后滑动到下一个视频")
        time.sleep(STAY_SECONDS)
        blocking, kw = _is_blocking_page(ctx)
        if blocking:
            raise RuntimeError(f"滑动前检测到阻塞页（含「{kw}」），已中止")
        swipe_up(ctx, duration_ms=500, wait_s=0.5)

    _runner_log("  完成：已观看 5 个视频")
    return 0


if __name__ == "__main__":
    run_script(main, ctx=ctx)
