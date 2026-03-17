#!/usr/bin/env python3
"""AdMaster Demo 开屏广告自动化脚本。

流程（与 MCP 调用逻辑一致）：
1. 全程录屏
2. 开启设备端 logcat 监控（tag=adm，保存到当次 run 的 logcat/adm.txt）
3. 回到桌面 → 清理 cc.admaster.android.demo 后台 → 启动 Demo
4. 点击「开屏」进入开屏示例页
5. 点击「load」（全匹配，不点 loadAndShow）→ 等待「请求成功」5s，超时或「请求失败」则重试点击 load，最多 3 次
6. 点击「show」之前打点：取一次内存信息（before_show），记录取内存耗时
7. 点击「show」进入广告展示页
8. 等待 6s 确认广告展示完成并返回
9. 做一次 GC 后再取一次内存信息（after_show_gc），记录取内存耗时
10. 停止 logcat、停止录屏，脚本结束

运行方式：
  python scripts/ad_demo_splash_script.py
  或通过 MCP: phone_run_script(path="scripts/ad_demo_splash_script.py", device_serial="98e516bf0922")

全程会开启 Android 设备端 logcat 监控，使用 tag 过滤 "adm"（匹配含 adm 的日志行，如 AdMaster SDK），
日志文件保存在当次 run 的 logcat/ 目录下，文件名如 adm.txt。

若脚本一启动就卡住：多半是 run_script 在 main() 前执行 mem_snapshot("before")（dumpsys meminfo）
在部分设备上极慢。本脚本已设置 auto_meminfo=False 规避。

MCP 调用 → script_api 映射：
  phone_start_recording / phone_stop_recording  → ctx.start_record() / ctx.stop_record()
  phone_start_logcat(tags="adm") / phone_stop_logcat  → ctx.start_logcat("adm", tag="adm", level="D") / ctx.stop_logcat("adm")
  phone_go_home + phone_force_stop + phone_launch_app  → chain().clear_background().restart_app_pkg().wait(2).done()
  phone_tap_element(index=开屏)  → find_text(ctx, "开屏").tap()
  phone_tap_element(index=load, 全匹配)  → find_text(ctx, "load", exact=True).tap()
  phone_wait_for_element("请求成功", 5s) + 最多重试 3 次  → for attempt in range(3): tap load; 5s 内轮询 请求成功/请求失败
  phone_tap_element(index=show)  → find_text(ctx, "show").tap()
  等待 6s  → chain(ctx).wait(6).done()
"""
from __future__ import annotations

import json
import time

from phone_pilot.core.log import _log as _runner_log
from phone_pilot.memory_analyze import trigger_gc
from phone_pilot.script_api import (
    ScriptContext,
    chain,
    find_text,
    mem_snapshot,
    run_script,
    screenshot,
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

PACKAGE = "cc.admaster.android.demo"

ctx = ScriptContext(
    # device_serial="98e516bf0922",  # 可选，单设备时自动检测
    auto_screenshot=True,
    auto_report=True,
    popup_guard=True,
    # 关闭「脚本开始前」的 meminfo 快照，避免在部分设备上 dumpsys meminfo 卡住导致脚本假死
    auto_meminfo=False,
)


# ---------------------------------------------------------------------------
# 主流程（对应上述 MCP 调用逻辑）
# ---------------------------------------------------------------------------

def main(ctx_or_none: ScriptContext | None = None) -> int:
    """MCP phone_run_script 会传入 ctx；本地 __main__ 使用全局 ctx。"""
    actual_ctx = ctx_or_none if ctx_or_none is not None else globals()["ctx"]

    # 1. 全程录屏
    actual_ctx.start_record("ad_demo_splash")

    # 2. 开启设备端 logcat 监控，tag 过滤 "adm"（匹配含 adm 的日志行，如 AdMaster SDK）
    actual_ctx.start_logcat("adm", tag="adm", level="D")

    # 3. 回到桌面 → 清理后台 → 启动 Demo
    chain(actual_ctx).clear_background().restart_app_pkg(PACKAGE).wait(2).done()

    # 4. 点击「开屏」进入开屏示例页（精确匹配，避免点到「模板插屏(新插屏)」）
    find_text(actual_ctx, "开屏", exact=True, retry_attempts=5, retry_interval_ms=800).tap(wait=1)

    # 5. 点击「load」（全匹配）→ 等待「请求成功」5s，失败则重试，最多 3 次
    load_ok = False
    for attempt in range(3):
        find_text(actual_ctx, "load", exact=True, retry_attempts=3, retry_interval_ms=500).tap(wait=0.6)
        for _ in range(5):
            time.sleep(1)
            if find_text(actual_ctx, "请求成功"):
                load_ok = True
                break
            if find_text(actual_ctx, "请求失败"):
                break
        if load_ok:
            break
    if not load_ok:
        screenshot(actual_ctx, "load_failed_after_3_retries")
        raise RuntimeError("请求成功 未在 3 次 load 内出现")

    # 6. 点击「show」之前打点：取一次内存信息，并记录耗时
    t0 = time.perf_counter()
    mem_snapshot(actual_ctx, "before_show", package=PACKAGE)
    elapsed_before = time.perf_counter() - t0
    _runner_log(f"  [meminfo] 取内存信息(before_show) 耗时: {elapsed_before:.2f}s")

    # 7. 点击「show」进入广告展示页
    find_text(actual_ctx, "show", retry_attempts=3).tap(wait=0.8)

    # 8. 等待 6s 确认广告展示完成并返回
    chain(actual_ctx).wait(6).done()
    screenshot(actual_ctx, "ad_display_done")

    # 9. 做一次 GC 后再取一次内存信息，并记录耗时
    trigger_gc(actual_ctx.device_serial, PACKAGE, wait_s=2.0)
    t1 = time.perf_counter()
    mem_snapshot(actual_ctx, "after_show_gc", package=PACKAGE)
    elapsed_after = time.perf_counter() - t1
    _runner_log(f"  [meminfo] 取内存信息(after_show_gc) 耗时: {elapsed_after:.2f}s")

    # 将取内存耗时写入 meminfo 目录，便于报告查看
    mem_dir = actual_ctx.session.meminfo_dir if actual_ctx.session else None
    if mem_dir:
        timing_path = mem_dir / "meminfo_timing.json"
        timing_path.write_text(
            json.dumps(
                {"before_show_s": round(elapsed_before, 2), "after_show_gc_s": round(elapsed_after, 2)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # 10. 停止 logcat 并保存（日志在 run 目录 logcat/adm.txt）
    actual_ctx.stop_logcat("adm")

    # 11. 停止录屏
    actual_ctx.stop_record()

    return 0


if __name__ == "__main__":
    run_script(main, ctx=ctx)
