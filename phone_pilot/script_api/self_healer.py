"""自愈编排器 / Self-healing orchestrator.

统一编排经验库查询、PopupGuard 弹窗检测、LLM 全局分析三者的协同。
对外提供统一的 try_heal(action, query, error) 接口。
Orchestrates experience lookup, PopupGuard detection, and LLM analysis.
Provides unified try_heal(action, query, error) interface.

使用方式 / Usage:
    在 find_text / retry 等函数的重试间隙调用 SelfHealer.try_heal()，
    获取修复建议后执行并反馈结果。
    Called between retries in find_text / retry etc.,
    applies fix suggestion and reports outcome.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from phone_pilot.core.log import _log as _runner_log
from phone_pilot.core.healing_experience import (
    HealingExperienceStore,
    HealingRecord,
)
from phone_pilot.script_api.popup_guard import PopupGuard

if TYPE_CHECKING:
    from phone_pilot.script_api.context import ScriptContext


# ---------------------------------------------------------------------------
# HealAction — 自愈动作
# ---------------------------------------------------------------------------

@dataclass
class HealAction:
    """自愈动作描述 / Self-healing action descriptor.

    由 SelfHealer.try_heal() 返回，描述建议的修复操作。
    Returned by SelfHealer.try_heal(), describes the suggested fix.

    Attributes / 属性:
        fix_type    — 修复类型 / Fix type:
                      "popup_dismiss" / "param_adjust" / "scroll" /
                      "wait_increase" / "llm_fix"
        fix_detail  — 具体修复参数 / Fix parameters dict
        source      — 来源 / Source: "experience" / "popup_guard" / "llm_analysis"
        confidence  — 置信度 0–1 / Confidence (0–1)
        record_id   — 关联的经验记录 ID / Associated experience record ID
        reason      — 原因说明 / Reason description
    """

    fix_type: str = ""
    fix_detail: dict = field(default_factory=dict)
    source: str = ""
    confidence: float = 1.0
    record_id: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# HealEvent — 自愈事件（写入 RunSession）
# ---------------------------------------------------------------------------

@dataclass
class HealEvent:
    """一次自愈事件记录 / A single healing event record.

    用于写入 RunSession 的 healing_events.json。
    Written to RunSession's healing_events.json.
    """

    timestamp: str = ""
    action: str = ""
    query: str = ""
    error_type: str = ""
    fix_type: str = ""
    fix_source: str = ""
    fix_detail: dict = field(default_factory=dict)
    success: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "query": self.query,
            "error_type": self.error_type,
            "fix_type": self.fix_type,
            "fix_source": self.fix_source,
            "fix_detail": self.fix_detail,
            "success": self.success,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# SelfHealer 编排器
# ---------------------------------------------------------------------------

class SelfHealer:
    """自愈编排器 — 统一入口 / Self-healing orchestrator — unified entry.

    编排经验库查询、PopupGuard 弹窗检测、LLM 全局分析的协同，
    提供 try_heal() + report_outcome() 闭环。
    Orchestrates experience lookup, PopupGuard, and LLM analysis,
    providing a try_heal() + report_outcome() feedback loop.

    Parameters / 参数:
        ctx: 脚本执行上下文 / Script execution context
        experience_store: 经验库实例 / Experience store instance
        popup_guard: 弹窗守卫实例 / PopupGuard instance
        llm_enabled: 是否启用 LLM 分析 / Enable LLM analysis
    """

    def __init__(
        self,
        ctx: "ScriptContext",
        *,
        experience_store: Optional[HealingExperienceStore] = None,
        popup_guard: Optional[PopupGuard] = None,
        llm_enabled: bool = False,
    ) -> None:
        self._ctx = ctx
        self._exp = experience_store or HealingExperienceStore()
        self._pg = popup_guard or PopupGuard(ctx)
        self._llm_enabled = llm_enabled
        self._events: list[HealEvent] = []

    @property
    def healing_events(self) -> list[HealEvent]:
        """返回本次运行的所有自愈事件 / Return all healing events for this run."""
        return list(self._events)

    # ---- 主接口 ----

    def try_heal(
        self,
        action: str,
        query: str,
        error: str,
        *,
        retry_count: int = 0,
    ) -> Optional[HealAction]:
        """尝试自愈 / Attempt self-healing.

        管线顺序 / Pipeline order:
        1. 经验库精确匹配 → 直接返回缓存的修复方案
        2. PopupGuard 弹窗检测 → 若有弹窗则关闭并返回
        3. LLM 全局分析（可选）→ 综合诊断并返回建议

        Parameters / 参数:
            action: 操作类型 (如 "find_text", "find_image")
            query: 操作参数 (如查找的文本)
            error: 错误信息
            retry_count: 已重试次数 (供 LLM 参考)

        Returns / 返回值:
            HealAction: 建议的修复动作（或 None）/ Suggested fix (or None)
        """
        t0 = time.monotonic()
        pkg, act = self._get_app_context()

        # ---- 1. 经验库查询 ----
        records = self._exp.lookup(action, query, pkg, act)
        for rec in records:
            if rec.success_rate > 0.5:
                _runner_log(
                    f"  [self-heal] 经验命中: {rec.fix_type} "
                    f"(success_rate={rec.success_rate:.0%}, hits={rec.hit_count})"
                )
                return HealAction(
                    fix_type=rec.fix_type,
                    fix_detail=dict(rec.fix_detail),
                    source="experience",
                    confidence=rec.success_rate,
                    record_id=rec.record_id,
                    reason=f"经验库命中 (hits={rec.hit_count})",
                )

        # ---- 2. PopupGuard 弹窗检测 ----
        popup_result = self._pg.check_and_dismiss()
        if popup_result:
            _runner_log(
                f"  [self-heal] 弹窗已关闭: {popup_result.get('dismiss_text', '?')} "
                f"(method={popup_result.get('detect_method', '?')})"
            )
            # 学习到经验库
            rec = HealingRecord(
                action=action,
                query=query,
                app_package=pkg,
                activity=act,
                error_type="popup_blocked",
                fix_type="popup_dismiss",
                fix_detail=popup_result,
                source=popup_result.get("detect_method", "structure"),
                success=True,
            )
            learned = self._exp.learn(rec)

            elapsed = (time.monotonic() - t0) * 1000
            self._events.append(HealEvent(
                timestamp=_iso_now(),
                action=action,
                query=query,
                error_type="popup_blocked",
                fix_type="popup_dismiss",
                fix_source="popup_guard",
                fix_detail=popup_result,
                success=True,
                duration_ms=elapsed,
            ))

            return HealAction(
                fix_type="popup_dismiss",
                fix_detail=popup_result,
                source="popup_guard",
                confidence=0.9,
                record_id=learned.record_id,
                reason=f"弹窗已关闭: {popup_result.get('dismiss_text', '')}",
            )

        # ---- 3. LLM 全局分析（可选）----
        if self._llm_enabled:
            llm_action = self._try_llm_analysis(
                action, query, error, pkg, act, records, retry_count
            )
            if llm_action:
                elapsed = (time.monotonic() - t0) * 1000
                self._events.append(HealEvent(
                    timestamp=_iso_now(),
                    action=action,
                    query=query,
                    error_type="llm_diagnosed",
                    fix_type=llm_action.fix_type,
                    fix_source="llm_analysis",
                    fix_detail=llm_action.fix_detail,
                    success=False,  # 尚未验证，report_outcome 后更新
                    duration_ms=elapsed,
                ))
                return llm_action

        return None

    def report_outcome(self, heal_action: HealAction, success: bool) -> None:
        """反馈修复结果 / Report healing outcome.

        更新经验库的命中/失败计数。LLM 修复成功时，将结果写入经验库供下次复用。
        Updates experience store hit/fail counts. On LLM fix success, saves to store.

        Parameters / 参数:
            heal_action: try_heal 返回的修复动作
            success: 修复是否解决了原始问题
        """
        pkg, act = self._get_app_context()

        if heal_action.record_id:
            self._exp.report_outcome(
                heal_action.record_id, success,
                package=pkg, activity=act,
            )

        # LLM 修复成功 → 写入经验库供下次复用
        if success and heal_action.source == "llm_analysis" and not heal_action.record_id:
            rec = HealingRecord(
                action=self._events[-1].action if self._events else "",
                query=self._events[-1].query if self._events else "",
                app_package=pkg,
                activity=act,
                error_type=self._events[-1].error_type if self._events else "",
                fix_type=heal_action.fix_type,
                fix_detail=heal_action.fix_detail,
                source="llm_analysis",
                success=True,
            )
            learned = self._exp.learn(rec)
            heal_action.record_id = learned.record_id
            _runner_log(
                f"  [self-heal] LLM 修复成功，已写入经验库 "
                f"(id={learned.record_id[:8]})"
            )

        # 更新最后一个 event 的 success 状态
        if self._events:
            self._events[-1].success = success

        status = "成功" if success else "失败"
        _runner_log(f"  [self-heal] 修复反馈: {heal_action.fix_type} → {status}")

    # ---- 内部方法 ----

    def _get_app_context(self) -> tuple[str, str]:
        """获取当前 app + activity / Get current app context."""
        try:
            info = self._ctx.driver.ui.get_current_activity()
            return info.get("package", ""), info.get("activity", "")
        except Exception:
            return "", ""

    def _try_llm_analysis(
        self,
        action: str,
        query: str,
        error: str,
        pkg: str,
        act: str,
        past_records: list[HealingRecord],
        retry_count: int,
    ) -> Optional[HealAction]:
        """调用 LLM 进行全局页面分析 / Call LLM for full page analysis."""
        try:
            from phone_pilot.extensions.llm.healer import analyze_and_suggest
        except ImportError:
            _runner_log("[self-heal] LLM 模块不可用")
            return None

        # 收集截图
        screenshot_bytes: Optional[bytes] = None
        try:
            screenshot_bytes = self._ctx.driver.screen.screenshot()
        except Exception:
            pass

        # 收集 UI 节点
        ui_nodes: list[dict] = []
        try:
            nodes = self._ctx.driver.ui.dump_ui_nodes()
            ui_nodes = [n.to_dict() for n in nodes]
        except Exception:
            pass

        # 构建错误上下文
        error_context = {
            "action": action,
            "query": query,
            "error_msg": error,
            "retry_count": retry_count,
            "app_package": pkg,
            "activity": act,
        }

        # 过往经验（供 LLM 参考）
        past_exps = [
            {
                "fix_type": r.fix_type,
                "fix_detail": r.fix_detail,
                "success_rate": r.success_rate,
                "source": r.source,
            }
            for r in past_records[:5]
        ]

        _runner_log(f"  [self-heal] 调用 LLM 分析 ({action}:{query[:30]})...")
        suggestion = analyze_and_suggest(
            screenshot_bytes=screenshot_bytes,
            ui_nodes=ui_nodes,
            error_context=error_context,
            past_experiences=past_exps,
        )

        if suggestion and suggestion.confidence > 0.3:
            _runner_log(
                f"  [self-heal] LLM 建议: {suggestion.fix_type} "
                f"(confidence={suggestion.confidence:.0%}) — {suggestion.reason[:60]}"
            )
            return HealAction(
                fix_type=suggestion.fix_type,
                fix_detail=suggestion.fix_detail,
                source="llm_analysis",
                confidence=suggestion.confidence,
                record_id="",
                reason=suggestion.reason,
            )

        return None


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def get_self_healer(ctx: "ScriptContext") -> Optional[SelfHealer]:
    """获取 ctx 上的 SelfHealer 实例（若 popup_guard 启用）。
    Get SelfHealer instance from ctx (if popup_guard enabled).

    延迟初始化：首次调用时创建 SelfHealer 并缓存到 ctx._self_healer。
    Lazy init: creates SelfHealer on first call and caches to ctx._self_healer.
    """
    if not getattr(ctx, "popup_guard", True):
        return None

    healer = getattr(ctx, "_self_healer", None)
    if healer is not None:
        return healer

    try:
        exp_store = HealingExperienceStore()
        pg = PopupGuard(ctx, rules=getattr(ctx, "popup_rules", None))
        llm_enabled = getattr(ctx, "llm_healing", False)

        healer = SelfHealer(
            ctx,
            experience_store=exp_store,
            popup_guard=pg,
            llm_enabled=llm_enabled,
        )
        ctx._self_healer = healer  # type: ignore[attr-defined]
        return healer
    except Exception:
        return None


def _iso_now() -> str:
    """当前时间 ISO 格式 / Current time in ISO format."""
    from phone_pilot.core.storage import iso_now
    return iso_now()
