"""验证工具包 — B 类验收基准与辅助。
Verification utilities — Class B acceptance baseline and helpers.

与 core/verify 的职责划分：
- core/verify 负责断言引擎与截图/内存等基线（运行时验证）
- verification/baseline 仅提供 B 类验证用的基准 App 常量与判断函数（验收场景）
Responsibility split with core/verify:
- core/verify: assertion engine and screenshot/memory baselines (runtime)
- verification/baseline: B-class baseline constants and helpers (acceptance)
"""

from phone_pilot.verification.baseline import (
    ANDROID_SETTINGS_PACKAGE,
    ANDROID_SETTINGS_HOME_TEXTS,
    SETTINGS_SUBPAGE_TEXTS,
    HARMONY_SETTINGS_PACKAGE,
    current_activity_is_settings,
)

__all__ = [
    "ANDROID_SETTINGS_PACKAGE",
    "ANDROID_SETTINGS_HOME_TEXTS",
    "SETTINGS_SUBPAGE_TEXTS",
    "HARMONY_SETTINGS_PACKAGE",
    "current_activity_is_settings",
]
