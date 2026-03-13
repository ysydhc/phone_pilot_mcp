"""B 类验证基准：系统设置 App 包名与预期文案。
Class B verification baseline: Settings app package and expected texts.

与 core/verify 的截图/内存基线并列，仅用于 B 类验收场景。
Separate from core/verify baselines, only for B-class acceptance.

消费者 / Consumers:
- B 类验收脚本（如 scripts/verify_mcp_tools_b.py）
- phone_pilot/tests/ 下 B 类相关 pytest
- 文档中「B 类流程说明」引用该模块
"""
from __future__ import annotations

# ========== Android 基准 / Android Baseline ==========

# 系统设置包名 / Settings app package name
ANDROID_SETTINGS_PACKAGE: str = "com.android.settings"

# 设置首页预期文案（用于判断「在设置首页」）/ Expected texts on Settings home
ANDROID_SETTINGS_HOME_TEXTS: list[str] = ["设置", "WLAN", "电池"]

# 设置子页预期文案 / Expected texts on Settings subpage
SETTINGS_SUBPAGE_TEXTS: list[str] = ["关于手机", "型号"]

# ========== HarmonyOS 基准（预留）/ Harmony Baseline (reserved) ==========

# B 类多平台时在此补充等价基准 / Fill when adding Harmony B-class baseline
HARMONY_SETTINGS_PACKAGE: str | None = None


def current_activity_is_settings(activity_dict: dict | None) -> bool:
    """判断当前前台是否为系统设置。入参与 phone_get_current_activity 返回结构一致。
    Check if current foreground is Settings. Input matches get_current_activity return shape.

    入参格式 / Input format:
        {"package": "com.android.settings", "activity": ".Settings"}
        或 None / or None

    Args:
        activity_dict: phone_get_current_activity 的返回值 / Return value of phone_get_current_activity

    Returns:
        True 当 package == ANDROID_SETTINGS_PACKAGE / True when package matches
    """
    if not activity_dict or not isinstance(activity_dict, dict):
        return False
    # 比对包名 / Compare package name
    return activity_dict.get("package") == ANDROID_SETTINGS_PACKAGE
