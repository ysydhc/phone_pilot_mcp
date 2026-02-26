"""屏幕状态检查点：保存和对比设备屏幕快照。

检查点包含：截图、UI 可见文本、当前 Activity 信息。
用于"修改前后对比"场景。

用法::

    from phone_pilot.core.checkpoint import save_checkpoint, diff_checkpoint
    # 保存
    result = save_checkpoint(driver, name="before_deploy")
    # 对比
    diff = diff_checkpoint(driver, name="before_deploy")
"""

from __future__ import annotations

import pathlib
import time
from typing import Any, Optional

from phone_pilot.core.storage import (
    iso_now,
    recordings_root,
    safe_name,
    write_json,
    read_json,
)


def _checkpoints_dir(out_dir: Optional[str] = None) -> pathlib.Path:
    """检查点根目录。"""
    root = recordings_root(out_dir)
    return root / "checkpoints"


def save_checkpoint(
    driver: Any,
    name: str,
    *,
    out_dir: Optional[str] = None,
) -> dict:
    """保存当前屏幕完整状态作为检查点。

    Parameters
    ----------
    driver : DeviceDriver
        设备驱动实例。
    name : str
        检查点名称（用于后续对比时引用）。
    out_dir : str | None
        自定义输出目录。

    Returns
    -------
    dict
        ``{ok, name, path, screenshot_path, ui_texts, activity}``
    """
    sname = safe_name(name)
    cp_dir = _checkpoints_dir(out_dir) / sname
    cp_dir.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []

    # 1. 截图
    screenshot_path = str(cp_dir / "screenshot.png")
    try:
        png_data = driver.screen.screenshot()
        pathlib.Path(screenshot_path).write_bytes(png_data)
    except Exception as e:
        errors.append(f"screenshot: {e}")
        screenshot_path = ""

    # 2. UI 可见文本
    ui_texts: list[str] = []
    try:
        ui_texts = driver.ui.collect_all_texts()
    except Exception as e:
        errors.append(f"ui_texts: {e}")

    write_json(cp_dir / "ui_texts.json", {"texts": ui_texts})

    # 3. 当前 Activity
    activity: dict = {}
    try:
        activity = driver.ui.get_current_activity()
    except Exception as e:
        errors.append(f"activity: {e}")

    write_json(cp_dir / "activity.json", activity)

    # 4. 元信息
    meta = {
        "name": sname,
        "timestamp": iso_now(),
        "ts_epoch": time.time(),
        "device_serial": getattr(driver, "device_serial", ""),
        "platform": getattr(driver, "platform", ""),
        "errors": errors,
    }
    write_json(cp_dir / "meta.json", meta)

    return {
        "ok": True,
        "name": sname,
        "path": str(cp_dir),
        "screenshot_path": screenshot_path,
        "ui_texts": ui_texts,
        "activity": activity,
    }


def diff_checkpoint(
    driver: Any,
    name: str,
    *,
    out_dir: Optional[str] = None,
) -> dict:
    """对比当前屏幕与已保存检查点的差异。

    Parameters
    ----------
    driver : DeviceDriver
        设备驱动实例。
    name : str
        之前保存的检查点名称。
    out_dir : str | None
        自定义输出目录。

    Returns
    -------
    dict
        ``{ok, similarity, text_added, text_removed, activity_changed,
          diff_screenshot_path}``
    """
    sname = safe_name(name)
    cp_dir = _checkpoints_dir(out_dir) / sname

    if not cp_dir.exists():
        return {"ok": False, "error": f"checkpoint_not_found: {sname}"}

    # 加载历史检查点
    old_texts: list[str] = []
    old_activity: dict = {}
    old_screenshot_path = cp_dir / "screenshot.png"

    try:
        data = read_json(cp_dir / "ui_texts.json")
        old_texts = data.get("texts", [])
    except Exception:
        pass

    try:
        old_activity = read_json(cp_dir / "activity.json")
    except Exception:
        pass

    # 获取当前状态
    errors: list[str] = []

    cur_texts: list[str] = []
    try:
        cur_texts = driver.ui.collect_all_texts()
    except Exception as e:
        errors.append(f"ui_texts: {e}")

    cur_activity: dict = {}
    try:
        cur_activity = driver.ui.get_current_activity()
    except Exception as e:
        errors.append(f"activity: {e}")

    # 文本差异
    old_set = set(old_texts)
    cur_set = set(cur_texts)
    text_added = sorted(cur_set - old_set)
    text_removed = sorted(old_set - cur_set)

    # Activity 是否变化
    activity_changed = (
        old_activity.get("package") != cur_activity.get("package")
        or old_activity.get("activity") != cur_activity.get("activity")
    )

    # 截图相似度
    similarity: float = 0.0
    diff_screenshot_path = ""
    try:
        cur_png = driver.screen.screenshot()
        if old_screenshot_path.exists():
            old_png = old_screenshot_path.read_bytes()
            similarity, diff_screenshot_path = _compare_screenshots(
                old_png, cur_png, cp_dir
            )
    except Exception as e:
        errors.append(f"screenshot_diff: {e}")

    return {
        "ok": True,
        "similarity": round(similarity, 4),
        "text_added": text_added,
        "text_removed": text_removed,
        "activity_changed": activity_changed,
        "diff_screenshot_path": diff_screenshot_path,
        "old_texts_count": len(old_texts),
        "cur_texts_count": len(cur_texts),
        "errors": errors,
    }


def _compare_screenshots(
    old_png: bytes,
    cur_png: bytes,
    cp_dir: pathlib.Path,
) -> tuple[float, str]:
    """对比两张截图，返回 (相似度, diff图片路径)。"""
    try:
        import cv2
        import numpy as np

        old_arr = cv2.imdecode(
            np.frombuffer(old_png, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        cur_arr = cv2.imdecode(
            np.frombuffer(cur_png, dtype=np.uint8), cv2.IMREAD_COLOR
        )

        if old_arr is None or cur_arr is None:
            return 0.0, ""

        # 统一尺寸
        if old_arr.shape != cur_arr.shape:
            cur_arr = cv2.resize(cur_arr, (old_arr.shape[1], old_arr.shape[0]))

        # 灰度 SSIM 简化版
        gray_old = cv2.cvtColor(old_arr, cv2.COLOR_BGR2GRAY)
        gray_cur = cv2.cvtColor(cur_arr, cv2.COLOR_BGR2GRAY)

        # 像素级相似度 (归一化)
        diff = cv2.absdiff(gray_old, gray_cur)
        non_zero = np.count_nonzero(diff)
        total = diff.size
        similarity = 1.0 - (non_zero / total) if total > 0 else 0.0

        # 生成 diff 图片（高亮差异区域）
        _, thresh = cv2.threshold(diff, 30, 255, cv2.THRESH_BINARY)
        diff_color = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
        diff_color[:, :, 0] = 0  # 去掉蓝色通道
        diff_color[:, :, 1] = 0  # 去掉绿色通道，只保留红色
        overlay = cv2.addWeighted(cur_arr, 0.7, diff_color, 0.3, 0)

        diff_path = cp_dir / "diff_screenshot.png"
        cv2.imwrite(str(diff_path), overlay)

        return similarity, str(diff_path)

    except ImportError:
        return 0.0, ""
