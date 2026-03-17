#!/usr/bin/env python3
"""通过 phone_pilot MCP 工具按顺序执行 demo 流程，并全程录屏。

步骤：
1. 回到桌面
2. 清理 cc.admaster.android.demo 后台进程（如有）
3. 启动 cc.admaster.android.demo
4. 点击「开屏」进入开屏示例页
5. 点击「load」（全字符匹配，不点 loadAndShow）
6. 等待页面出现「请求成功」，超时 5s；超时或出现「请求失败」则重试步骤 5，最多 3 次
7. 请求成功后点击「show」进入广告显示页
8. 等待 6s，确认广告显示完成并返回，提示脚本完成
9. 全程录屏（方案 B：先 start 再轮询 status 再 stop）

用法：uv run python scripts/run_demo_flow_local.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 从 MCP server 直接导入工具（同一套实现）
from phone_pilot.mcp.server import (
    phone_force_stop,
    phone_get_page_state,
    phone_go_home,
    phone_launch_app,
    phone_list_devices,
    phone_recording_status,
    phone_start_recording,
    phone_stop_recording,
    phone_tap_element,
    phone_wait_for_element,
)

PACKAGE = "cc.admaster.android.demo"
LOAD_RETRY_MAX = 3
WAIT_SUCCESS_TIMEOUT_S = 5.0
FINAL_WAIT_S = 6.0


async def _get_device_serial() -> str:
    res = await phone_list_devices()
    if not res.get("ok"):
        raise RuntimeError(f"phone_list_devices 失败: {res}")
    devices = res.get("devices") or []
    if not devices:
        raise RuntimeError("未发现设备，请连接一台 Android 设备并确认 adb devices")
    serial = devices[0].get("device_serial") or devices[0].get("serial") or ""
    if not serial:
        serial = list(devices[0].keys())[0] if devices[0] else ""
    if not serial:
        raise RuntimeError("无法从 phone_list_devices 解析 device_serial")
    return serial


async def _start_recording_and_wait(serial: str) -> None:
    r = await phone_start_recording(device_serial=serial)
    if not r.get("ok") or r.get("status") != "starting":
        raise RuntimeError(f"phone_start_recording 失败或未返回 starting: {r}")
    print("[录屏] 已提交后台启动，轮询 status...")
    for _ in range(30):
        st = phone_recording_status(serial)
        if not st.get("ok"):
            raise RuntimeError(f"phone_recording_status 失败: {st}")
        status = st.get("status")
        if status == "started":
            print("[录屏] 已 started，继续流程")
            return
        if status == "failed":
            raise RuntimeError(f"录屏启动失败: {st.get('error', st)}")
        await asyncio.sleep(1.0)
    raise RuntimeError("录屏 status 未在 30s 内变为 started")


def _find_element_index_by_label(elements: list, label: str, exact: bool = True) -> int | None:
    for e in elements:
        lab = (e.get("label") or "").strip()
        if exact:
            if lab == label:
                return e.get("index")
        else:
            if label in lab:
                return e.get("index")
    return None


async def main() -> int:
    print("1. 获取设备...")
    serial = await _get_device_serial()
    print(f"    device_serial: {serial}")

    print("2. 开始全程录屏（方案 B）...")
    await _start_recording_and_wait(serial)

    print("3. 回到桌面...")
    r = await phone_go_home(device_serial=serial)
    if not r.get("ok"):
        raise RuntimeError(f"phone_go_home 失败: {r}")
    await asyncio.sleep(1.0)

    print("4. 清理 cc.admaster.android.demo 后台进程...")
    await phone_force_stop(device_serial=serial, package=PACKAGE)
    await asyncio.sleep(0.8)

    print("5. 启动 cc.admaster.android.demo...")
    r = await phone_launch_app(device_serial=serial, package=PACKAGE)
    if not r.get("ok"):
        raise RuntimeError(f"phone_launch_app 失败: {r}")
    await asyncio.sleep(2.0)

    print("6. 点击「开屏」进入开屏示例页...")
    state = await phone_get_page_state(device_serial=serial)
    if not state.get("ok"):
        raise RuntimeError(f"phone_get_page_state 失败: {state}")
    elements = state.get("elements") or []
    idx = _find_element_index_by_label(elements, "开屏", exact=False)
    if idx is None:
        raise RuntimeError("未找到「开屏」按钮，elements 文本: " + str([e.get("label") for e in elements[:20]]))
    r = await phone_tap_element(index=idx, device_serial=serial)
    if not r.get("ok"):
        raise RuntimeError(f"phone_tap_element 开屏 失败: {r}")
    await asyncio.sleep(1.5)

    load_attempt = 0
    while load_attempt < LOAD_RETRY_MAX:
        load_attempt += 1
        print(f"7. 点击「load」（全字符匹配）第 {load_attempt}/{LOAD_RETRY_MAX} 次...")
        state = await phone_get_page_state(device_serial=serial)
        if not state.get("ok"):
            raise RuntimeError(f"phone_get_page_state 失败: {state}")
        elements = state.get("elements") or []
        # 全字符对比：只要 label 完全等于 "load"
        idx = _find_element_index_by_label(elements, "load", exact=True)
        if idx is None:
            print("    未找到 label 为 load 的按钮，当前 labels: " + str([e.get("label") for e in elements[:30]]))
            if load_attempt >= LOAD_RETRY_MAX:
                raise RuntimeError("多次未找到「load」按钮")
            await asyncio.sleep(1.0)
            continue
        r = await phone_tap_element(index=idx, device_serial=serial)
        if not r.get("ok"):
            raise RuntimeError(f"phone_tap_element load 失败: {r}")
        await asyncio.sleep(0.5)

        print("    等待「请求成功」（超时 5s）...")
        wait_res = await phone_wait_for_element(device_serial=serial, text="请求成功", timeout_s=WAIT_SUCCESS_TIMEOUT_S)
        if not wait_res.get("ok"):
            raise RuntimeError(f"phone_wait_for_element 失败: {wait_res}")
        if wait_res.get("found"):
            print("    已出现「请求成功」")
            break
        # 超时未找到，检查是否出现「请求失败」
        state = await phone_get_page_state(device_serial=serial)
        all_texts = (state.get("ok") and state.get("all_texts")) or []
        if "请求失败" in (str(t) for t in all_texts):
            print("    页面显示「请求失败」，将重试点击 load")
        if load_attempt >= LOAD_RETRY_MAX:
            raise RuntimeError("在 5s 内未出现「请求成功」且已达最大重试次数")
        await asyncio.sleep(0.8)

    print("8. 点击「show」进入广告显示页...")
    state = await phone_get_page_state(device_serial=serial)
    if not state.get("ok"):
        raise RuntimeError(f"phone_get_page_state 失败: {state}")
    elements = state.get("elements") or []
    idx = _find_element_index_by_label(elements, "show", exact=True)
    if idx is None:
        idx = _find_element_index_by_label(elements, "show", exact=False)
    if idx is None:
        raise RuntimeError("未找到「show」按钮")
    r = await phone_tap_element(index=idx, device_serial=serial)
    if not r.get("ok"):
        raise RuntimeError(f"phone_tap_element show 失败: {r}")

    print(f"9. 等待 {FINAL_WAIT_S}s 确认广告显示完成...")
    await asyncio.sleep(FINAL_WAIT_S)

    print("10. 停止录屏...")
    r = await phone_stop_recording(device_serial=serial)
    if not r.get("ok"):
        raise RuntimeError(f"phone_stop_recording 失败: {r}")
    print(f"    录屏已保存: {r.get('local_path', '')}")

    print("\n脚本完成。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
