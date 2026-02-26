"""phone_pilot.script_api — 脚本 API 统一入口 / Script API unified entry point.

将所有公开 API 和常用内部函数从子模块汇聚到 ``phone_pilot.script_api`` 命名空间，
确保以下两种导入方式继续正常工作：

    from phone_pilot.script_api import ScriptContext, find_text, ...
    from phone_pilot import script_api as api; api.find_text(...)
"""
from __future__ import annotations

# ---- 重新导出：核心类 & 异常 ----
from phone_pilot.core.ui import Box
from phone_pilot.core.run_store import RunSession

from .context import ScriptContext, RetryExhausted

# ---- 重新导出：查找 API ----
from .find import find_text, find_image, find_text_list, find_image_list

# ---- 重新导出：操作 API ----
from .actions import (
    clear_background,
    reset_home_screen,
    restart_app_pkg,
    launch_from_home,
    screenshot,
    screenshot_annotated,
    swipe,
    swipe_up,
    swipe_down,
    swipe_left,
    swipe_right,
    type_text,
    keyevent,
    tap_xy,
    mem_snapshot,
    mem_diff,
    mem_check_leak,
    dump_hprof_snapshot,
    device_capture,
    unlock_device,
    dump_ui,
    raw_uia,
    _build_element_ops,
)

# ---- 重新导出：日志 API ----
from .logcat import logcat_find, logcat_wait_for, _merge_logcat_lines

# ---- 重新导出：执行器 & 链式 API ----
from .runner import (
    ChainResult,
    chain,
    retry,
    scroll_to_find,
    run_script,
    _finalize_run,
)

# ---- 重新导出：错误自愈 ----
from .popup_guard import PopupGuard, PopupRule
from .self_healer import SelfHealer, HealAction, get_self_healer
from phone_pilot.core.healing_experience import HealingExperienceStore, HealingRecord

# ---- 重新导出：私有辅助函数（chain.py / 测试文件通过 api._xxx 访问）----
from ._helpers import (
    _get_screen_size,
    _input_swipe,
    _input_tap,
    _input_keyevent,
    _input_text,
    _get_current_focus,
    _take_screenshot,
    _dump_ui_nodes,
    _ensure_out_dir,
    _box_from_bounds,
    _roi_from_box,
    _box_center,
    _normalize_text,
    _device_locales_from_protocol,
    _locale_to_ocr_langs,
    _expand_ocr_langs,
    _auto_log_print,
)

from ._ocr import (
    _ocr_find_text_boxes,
    _ocr_texts_from_image,
    _ocr_find_text_boxes_roi,
    _ocr_find_text_boxes_regex,
)

from ._locate import (
    _uia_find_text_boxes,
    _airtest_find_image_boxes,
    _template_size,
    _select_relative_box,
    _ui_ops,
    _anchor_from_element,
    _apply_uia_fields,
    _ui_element_from_box,
    _ui_relative_find,
    _uia_info_from_element,
    _ui_locate,
)

__all__ = [
    # Core
    "ScriptContext",
    "RunSession",
    "ChainResult",
    "chain",
    "Box",
    # 异常
    "RetryExhausted",
    # 重试 & 滚动
    "retry",
    "scroll_to_find",
    # 入口
    "run_script",
    # App 控制
    "clear_background",
    "reset_home_screen",
    "restart_app_pkg",
    "launch_from_home",
    # 查找
    "find_image",
    "find_image_list",
    "find_text",
    "find_text_list",
    # 截图 & 滑动
    "screenshot",
    "screenshot_annotated",
    "swipe",
    "swipe_up",
    "swipe_down",
    "swipe_left",
    "swipe_right",
    # 输入
    "type_text",
    "keyevent",
    "tap_xy",
    # 日志
    "logcat_find",
    "logcat_wait_for",
    # UIAutomator 逃生通道
    "dump_ui",
    "raw_uia",
    # 错误自愈
    "PopupGuard",
    "PopupRule",
    "SelfHealer",
    "HealAction",
    "get_self_healer",
    "HealingExperienceStore",
    "HealingRecord",
    # 内存分析
    "mem_snapshot",
    "mem_diff",
    "mem_check_leak",
    # 其他
    "dump_hprof_snapshot",
    "device_capture",
    "unlock_device",
    # 私有辅助（供 chain.py / 测试通过 api._xxx 访问）
    "_build_element_ops",
    "_merge_logcat_lines",
    "_finalize_run",
    "_get_screen_size",
    "_input_swipe",
    "_input_tap",
    "_input_keyevent",
    "_input_text",
    "_get_current_focus",
    "_take_screenshot",
    "_dump_ui_nodes",
    "_ensure_out_dir",
    "_box_from_bounds",
    "_roi_from_box",
    "_box_center",
    "_normalize_text",
    "_device_locales_from_protocol",
    "_locale_to_ocr_langs",
    "_expand_ocr_langs",
    "_auto_log_print",
    # OCR 内部
    "_ocr_find_text_boxes",
    "_ocr_texts_from_image",
    "_ocr_find_text_boxes_roi",
    "_ocr_find_text_boxes_regex",
    # 定位内部
    "_uia_find_text_boxes",
    "_airtest_find_image_boxes",
    "_template_size",
    "_select_relative_box",
    "_ui_ops",
    "_anchor_from_element",
    "_apply_uia_fields",
    "_ui_element_from_box",
    "_ui_relative_find",
    "_uia_info_from_element",
    "_ui_locate",
]
