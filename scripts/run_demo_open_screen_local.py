#!/usr/bin/env python3
"""
通过 phone_pilot 本地 API 直接执行 demo 开屏流程（不经过 MCP）。
用于验证流程逻辑与设备可用性。
"""
from __future__ import annotations

import time
from pathlib import Path

# 确保 adb 环境
from phone_pilot.android.adb.utils import ensure_adb_env

ensure_adb_env()

from phone_pilot.mcp.server import _resolve_device_serial
from phone_pilot.android.driver import AndroidDriver
from phone_pilot.android.ui.query import element_query_impl
from phone_pilot.android.device.utils import input_tap


PACKAGE = "cc.admaster.android.demo"
OUT_DIR = Path(".recordings")
RECORDING_NAME = "demo_open_screen_local"


def get_driver():
    resolved, plat, err = _resolve_device_serial(None, "auto")
    if err:
        raise RuntimeError(f"设备未就绪: {err}")
    if plat != "android":
        raise RuntimeError("仅支持 Android 设备")
    return AndroidDriver(resolved), resolved


def tap_by_text(device_serial: str, text: str, timeout_s: float = 5.0) -> bool:
    """根据文本查找并点击，找到则点击返回 True，否则 False。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = element_query_impl(device_serial, {"text_contains": text}, limit=5)
        if res.get("ok") and res.get("count", 0) > 0:
            el = res["elements"][0]
            cx = el.get("center_x")
            cy = el.get("center_y")
            if cx is not None and cy is not None:
                r = input_tap(device_serial, int(cx), int(cy))
                if r.get("ok"):
                    return True
        time.sleep(0.3)
    return False


def wait_for_text(device_serial: str, text: str, timeout_s: float) -> bool:
    """等待页面上出现某文本，出现返回 True，超时返回 False。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = element_query_impl(device_serial, {"text_contains": text}, limit=3)
        if res.get("ok") and res.get("count", 0) > 0:
            return True
        time.sleep(0.25)
    return False


def has_text(device_serial: str, text: str) -> bool:
    """页面上是否包含某文本。"""
    res = element_query_impl(device_serial, {"text_contains": text}, limit=1)
    return bool(res.get("ok") and res.get("count", 0) > 0)


def main():
    print("1. 解析设备并创建 driver ...")
    driver, device_serial = get_driver()
    print(f"   设备: {device_serial}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rec_dir = OUT_DIR / "recordings"
    rec_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    local_path = rec_dir / f"{RECORDING_NAME}_{ts}.mp4"
    remote_filename = f"phone_pilot_rec_{ts}.mp4"

    print("2. 开始录屏（全程记录）...")
    rec_res = driver.screen.start_screenrecord(path=remote_filename)
    if not rec_res.get("ok"):
        print(f"   录屏失败: {rec_res}")
        return 1
    remote_path = rec_res.get("remote_path") or f"/sdcard/{remote_filename}"
    print(f"   录屏中 -> {local_path}")

    try:
        print("3. 回到桌面 ...")
        r = driver.go_home()
        if not r.get("ok"):
            print(f"   go_home 失败: {r}")
            return 1
        time.sleep(0.8)

        print("4. 清理 demo 后台进程 ...")
        r = driver.app.force_stop(PACKAGE)
        print(f"   force_stop: {r.get('ok')}")
        time.sleep(0.5)

        print("5. 启动 cc.admaster.android.demo ...")
        r = driver.app.launch_app(PACKAGE)
        if not r.get("ok"):
            print(f"   launch 失败: {r}")
            return 1
        time.sleep(1.5)

        print("6. 点击「开屏」进入开屏示例页 ...")
        if not tap_by_text(device_serial, "开屏", timeout_s=8):
            print("   未找到「开屏」按钮")
            return 1
        time.sleep(1.0)

        print("7. 点击 load，等待「请求成功」（最多重试 3 次）...")
        for attempt in range(3):
            if not tap_by_text(device_serial, "load", timeout_s=5):
                print(f"   第 {attempt + 1} 次未找到 load 按钮")
                time.sleep(0.5)
                continue
            time.sleep(0.5)
            if wait_for_text(device_serial, "请求成功", timeout_s=5.0):
                print("   请求成功")
                break
            if has_text(device_serial, "请求失败"):
                print(f"   第 {attempt + 1} 次显示请求失败，重试 ...")
                continue
            print(f"   第 {attempt + 1} 次超时未看到请求成功，重试 ...")
        else:
            print("   3 次后仍未出现请求成功")
            return 1

        print("8. 点击 show 进入广告显示页 ...")
        if not tap_by_text(device_serial, "show", timeout_s=5):
            print("   未找到 show 按钮")
            return 1

        print("9. 等待 6s 确认广告显示完成 ...")
        time.sleep(6)

        print("10. 脚本完成。")
    finally:
        print("11. 停止录屏并拉取文件 ...")
        r = driver.screen.stop_screenrecord()
        if r.get("ok"):
            driver.pull_file(remote_path, str(local_path))
            print(f"    录屏已保存: {local_path}")
        else:
            print(f"    stop_screenrecord: {r}")

    return 0


if __name__ == "__main__":
    exit(main())
