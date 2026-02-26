"""弹窗自动检测与关闭 / Automatic popup detection and dismissal.

三层本地检测管线：
1. Window 层级检测 — 通过 dumpsys window 快速判断是否有 Dialog Window
2. UI 树结构分析 — 检查 class_name / resource_id / 遮罩层等结构特征
3. 文本规则匹配 — 内置 DEFAULT_RULES 覆盖中/英/日常见弹窗

检测到弹窗后，查找并点击关闭按钮。
Three-layer local detection pipeline:
1. Window layer detection — dumpsys window for Dialog windows
2. UI tree structure analysis — class_name / resource_id / overlay detection
3. Text rule matching — built-in DEFAULT_RULES for common popups
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Sequence

if TYPE_CHECKING:
    from phone_pilot.core.ui_node import UINode
    from phone_pilot.script_api.context import ScriptContext


# ---------------------------------------------------------------------------
# PopupRule — 弹窗规则
# ---------------------------------------------------------------------------

@dataclass
class PopupRule:
    """弹窗关闭规则 / Popup dismissal rule.

    字段 / Fields:
        name            — 规则名称（日志用）/ Rule name for logging
        detect_texts    — 检测文本列表（任一匹配即命中）/ Detection texts (any match triggers)
        dismiss_texts   — 关闭按钮文本（按优先级尝试）/ Dismiss button texts (priority order)
        dismiss_resource_ids — 按 resource_id 关闭 / Dismiss by resource_id
        detect_class_names   — 检测 class_name 特征 / Detect by class_name
        detect_resource_ids  — 检测 resource_id 特征 / Detect by resource_id
        priority        — 优先级（越高越先检查）/ Priority (higher = checked first)
        enabled         — 是否启用 / Enabled
    """

    name: str = ""
    detect_texts: list[str] = field(default_factory=list)
    dismiss_texts: list[str] = field(default_factory=list)
    dismiss_resource_ids: list[str] = field(default_factory=list)
    detect_class_names: list[str] = field(default_factory=list)
    detect_resource_ids: list[str] = field(default_factory=list)
    priority: int = 0
    enabled: bool = True


# ---------------------------------------------------------------------------
# 内置默认规则 / Built-in default rules
# ---------------------------------------------------------------------------

DEFAULT_RULES: list[PopupRule] = [
    # 系统权限弹窗 / System permission dialogs
    PopupRule(
        name="system_permission",
        detect_texts=["允许", "位置权限", "存储权限", "相机权限", "通知权限",
                      "Allow", "Permission", "While using", "Only this time"],
        dismiss_texts=["允许", "始终允许", "仅在使用中允许", "Allow", "While using the app",
                       "Only this time", "許可", "許可する"],
        dismiss_resource_ids=["com.android.permissioncontroller:id/permission_allow_button",
                              "com.android.packageinstaller:id/permission_allow_button",
                              "android:id/button1"],
        detect_class_names=["AlertDialog", "GrantPermissionsActivity"],
        detect_resource_ids=["android:id/alertTitle",
                             "com.android.permissioncontroller:id/permission_message"],
        priority=100,
    ),
    # 通用确认弹窗 / Generic confirmation dialogs
    PopupRule(
        name="generic_confirm",
        detect_texts=["确定", "确认", "知道了", "OK", "Got it", "了解"],
        dismiss_texts=["确定", "确认", "知道了", "OK", "Got it", "了解",
                       "わかりました", "閉じる"],
        dismiss_resource_ids=["android:id/button1"],
        detect_class_names=["AlertDialog"],
        detect_resource_ids=["android:id/alertTitle", "android:id/message"],
        priority=80,
    ),
    # 关闭 / 取消按钮 / Close / Cancel buttons
    PopupRule(
        name="close_cancel",
        detect_texts=["关闭", "取消", "Close", "Cancel", "Dismiss",
                      "不再提示", "Skip", "跳过"],
        dismiss_texts=["关闭", "取消", "Close", "Cancel", "Dismiss",
                       "不再提示", "Skip", "跳过", "閉じる", "キャンセル"],
        priority=60,
    ),
    # 系统更新 / System update dialogs
    PopupRule(
        name="system_update",
        detect_texts=["系统更新", "System Update", "新版本", "升级",
                      "アップデート", "更新"],
        dismiss_texts=["稍后", "Later", "Not now", "以后再说", "取消",
                       "あとで", "Cancel"],
        priority=70,
    ),
    # 日文常见弹窗 / Japanese common dialogs
    PopupRule(
        name="japanese_common",
        detect_texts=["許可", "閉じる", "確認", "同意"],
        dismiss_texts=["許可", "許可する", "閉じる", "OK", "はい",
                       "同意する", "同意して続行"],
        priority=50,
    ),
]


# ---------------------------------------------------------------------------
# 弹窗结构特征常量 / Dialog structure detection constants
# ---------------------------------------------------------------------------

# class_name 子串匹配（任一命中则认为是弹窗节点）
_DIALOG_CLASS_PATTERNS = [
    "Dialog", "AlertDialog", "BottomSheet", "PopupWindow",
    "BottomSheetDialog", "DialogFragment",
]

# resource_id 精确匹配（系统标准弹窗组件）
_DIALOG_RESOURCE_IDS = {
    "android:id/alertTitle",
    "android:id/message",
    "android:id/button1",
    "android:id/button2",
    "android:id/button3",
    "android:id/parentPanel",
    "android:id/contentPanel",
}

# Window 层级检测关键字
_DIALOG_WINDOW_PATTERNS = re.compile(
    r"(Dialog|PopupWindow|AlertDialog|BottomSheet|Toast|permission)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# PopupGuard — 弹窗守卫
# ---------------------------------------------------------------------------

class PopupGuard:
    """弹窗守卫 — Window 层级 + UI 树结构 两层本地检测。
    Popup guardian — Window layer + UI tree structure two-layer local detection.

    检测到弹窗后，自动查找并点击关闭按钮。
    Automatically detects and dismisses popups.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        rules: 自定义弹窗规则列表（None 用 DEFAULT_RULES）/ Custom rules (None for defaults)
    """

    def __init__(
        self,
        ctx: "ScriptContext",
        rules: Optional[list[PopupRule]] = None,
    ) -> None:
        self._ctx = ctx
        self._rules = sorted(
            rules or list(DEFAULT_RULES),
            key=lambda r: r.priority,
            reverse=True,
        )

    # ---- 公开接口 ----

    def check_and_dismiss(self) -> Optional[dict]:
        """检测并关闭弹窗 / Detect and dismiss popup.

        返回纠正详情 dict（供经验库学习）或 None（无弹窗）。
        Returns fix_detail dict (for experience learning) or None (no popup).

        Returns / 返回值:
            dict: {"popup_type": str, "dismiss_text": str, "detect_method": str, ...}
            None: 未检测到弹窗 / No popup detected
        """
        try:
            nodes = self._ctx.driver.ui.dump_ui_nodes()
        except Exception:
            return None

        # 1. Window 层级检测（快速路径）
        has_dialog_window = self._check_window_layer()

        # 2. UI 树结构分析
        structure_info = self._analyze_ui_structure(nodes)

        if has_dialog_window or structure_info.get("is_popup"):
            # 先尝试结构化关闭（resource_id）
            result = self._dismiss_by_structure(nodes, structure_info)
            if result:
                result["detect_method"] = "window_layer" if has_dialog_window else "ui_structure"
                return result

            # 再尝试文本规则关闭
            result = self._dismiss_by_rules(nodes)
            if result:
                result["detect_method"] = "text_rule"
                return result

        # 3. 即使结构检测未命中，也尝试文本规则（覆盖自定义弹窗）
        if not has_dialog_window and not structure_info.get("is_popup"):
            result = self._dismiss_by_rules(nodes)
            if result:
                result["detect_method"] = "text_rule_fallback"
                return result

        return None

    def add_rule(self, rule: PopupRule) -> None:
        """添加自定义规则 / Add custom rule."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    # ---- 第一层：Window 层级检测 ----

    def _check_window_layer(self) -> bool:
        """通过 dumpsys window 检测是否有 Dialog Window。
        Check for Dialog windows via dumpsys window.
        """
        try:
            platform = self._ctx.driver.platform
            if platform == "android":
                return self._check_window_android()
            elif platform == "harmony":
                return self._check_window_harmony()
        except Exception:
            pass
        return False

    def _check_window_android(self) -> bool:
        """Android: dumpsys window windows → 检测 Dialog 类型 Window."""
        try:
            from phone_pilot.android.adb.runner import CommandRunner, adb_prefix
            serial = self._ctx.device_serial or ""
            cmd = adb_prefix(serial) + ["shell", "dumpsys", "window", "windows"]
            result = CommandRunner.run(cmd, check=False, log_output=False)
            if result and result.stdout:
                output = result.stdout
                # 查找活动的 Dialog/PopupWindow/Alert 窗口
                return bool(_DIALOG_WINDOW_PATTERNS.search(output))
        except Exception:
            pass
        return False

    def _check_window_harmony(self) -> bool:
        """HarmonyOS: hdc shell 等效检测。"""
        # HarmonyOS 目前没有 dumpsys 等效命令，跳过此层
        return False

    # ---- 第二层：UI 树结构分析 ----

    def _analyze_ui_structure(self, nodes: Sequence["UINode"]) -> dict:
        """分析 UI 树结构特征判断是否有弹窗。
        Analyze UI tree structure to detect popup characteristics.

        检查点 / Checks:
        1. class_name 包含 Dialog/AlertDialog/BottomSheet/PopupWindow
        2. resource_id 命中系统标准弹窗组件
        3. 是否有覆盖大面积的遮罩层
        """
        info: dict[str, Any] = {"is_popup": False, "type": "", "dialog_nodes": []}

        screen_size = None
        try:
            screen_size = self._ctx.driver.screen.get_screen_size()
        except Exception:
            pass

        for node in nodes:
            # 检查 class_name
            cls = getattr(node, "class_name", "") or ""
            for pattern in _DIALOG_CLASS_PATTERNS:
                if pattern.lower() in cls.lower():
                    info["is_popup"] = True
                    info["type"] = "dialog_class"
                    info["dialog_nodes"].append(node)
                    break

            # 检查 resource_id
            res_id = getattr(node, "resource_id", "") or ""
            if res_id in _DIALOG_RESOURCE_IDS:
                info["is_popup"] = True
                if not info["type"]:
                    info["type"] = "dialog_resource_id"

            # 检查覆盖面积（遮罩层检测）
            if screen_size and not info["is_popup"]:
                bounds = getattr(node, "bounds_tuple", lambda: None)()
                if bounds and not getattr(node, "clickable", False):
                    x1, y1, x2, y2 = bounds
                    sw, sh = screen_size
                    area = (x2 - x1) * (y2 - y1)
                    screen_area = sw * sh
                    if screen_area > 0 and area / screen_area > 0.7:
                        # 大面积非可点击元素 → 可能是遮罩层
                        text = getattr(node, "text", "") or ""
                        if not text:  # 遮罩层通常没有文字
                            info["is_popup"] = True
                            info["type"] = "overlay_mask"

        return info

    # ---- 关闭逻辑 ----

    def _dismiss_by_structure(
        self, nodes: Sequence["UINode"], structure_info: dict
    ) -> Optional[dict]:
        """通过系统标准 resource_id 关闭弹窗。
        Dismiss popup using standard Android resource_ids.
        """
        # 查找 button1/button2/button3（Android AlertDialog 标准按钮）
        for target_id in ["android:id/button1", "android:id/button2", "android:id/button3"]:
            for node in nodes:
                res_id = getattr(node, "resource_id", "") or ""
                if res_id == target_id and getattr(node, "clickable", False):
                    center = node.center()
                    if center:
                        try:
                            self._ctx.driver.input.tap(center[0], center[1])
                            time.sleep(0.5)
                            return {
                                "popup_type": structure_info.get("type", "dialog"),
                                "dismiss_text": getattr(node, "text", "") or target_id,
                                "dismiss_resource_id": target_id,
                            }
                        except Exception:
                            pass
        return None

    def _dismiss_by_rules(self, nodes: Sequence["UINode"]) -> Optional[dict]:
        """通过文本规则匹配并关闭弹窗。
        Dismiss popup using text-based rules.
        """
        # 收集所有可见文本
        all_texts: list[str] = []
        for node in nodes:
            for attr in ("text", "content_desc"):
                val = getattr(node, attr, "") or ""
                if val:
                    all_texts.append(val)

        for rule in self._rules:
            if not rule.enabled:
                continue

            # 检测是否有弹窗特征文本
            detected = False
            for dt in rule.detect_texts:
                if any(dt in t for t in all_texts):
                    detected = True
                    break

            if not detected:
                # 也检查 detect_class_names / detect_resource_ids
                for node in nodes:
                    cls = getattr(node, "class_name", "") or ""
                    for dcls in rule.detect_class_names:
                        if dcls.lower() in cls.lower():
                            detected = True
                            break
                    if detected:
                        break
                    res_id = getattr(node, "resource_id", "") or ""
                    for drid in rule.detect_resource_ids:
                        if drid == res_id:
                            detected = True
                            break
                    if detected:
                        break

            if not detected:
                continue

            # 尝试点击关闭按钮（按 resource_id 优先）
            for dismiss_rid in rule.dismiss_resource_ids:
                for node in nodes:
                    res_id = getattr(node, "resource_id", "") or ""
                    if res_id == dismiss_rid and getattr(node, "clickable", False):
                        center = node.center()
                        if center:
                            try:
                                self._ctx.driver.input.tap(center[0], center[1])
                                time.sleep(0.5)
                                return {
                                    "popup_type": rule.name,
                                    "dismiss_text": getattr(node, "text", "") or dismiss_rid,
                                    "dismiss_resource_id": dismiss_rid,
                                    "rule_name": rule.name,
                                }
                            except Exception:
                                pass

            # Fallback: 按文本查找关闭按钮
            for dismiss_text in rule.dismiss_texts:
                for node in nodes:
                    node_text = getattr(node, "text", "") or ""
                    node_desc = getattr(node, "content_desc", "") or ""
                    if (dismiss_text in node_text or dismiss_text in node_desc) and getattr(
                        node, "clickable", False
                    ):
                        center = node.center()
                        if center:
                            try:
                                self._ctx.driver.input.tap(center[0], center[1])
                                time.sleep(0.5)
                                return {
                                    "popup_type": rule.name,
                                    "dismiss_text": dismiss_text,
                                    "rule_name": rule.name,
                                }
                            except Exception:
                                pass

        return None
