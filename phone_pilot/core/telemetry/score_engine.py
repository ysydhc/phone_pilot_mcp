"""TelemetryScoreEngine — 端侧真实执行质量打分与度量收集引擎。

提供四维度量化评分标准（100 分制）：
1. 结果正确性（40%）：执行是否成功返回且断言有效。
2. 端侧有效性（30%）：端侧 UI 或系统状态是否产生预期的有效状态跃迁（防空击/无效滑动）。
3. 响应时延（20%）：端到端调用耗时阶梯打分（<=500ms 满分，阶梯衰减）。
4. 自愈代价（10%）：是否依赖弹窗关闭、滚动重试或路径重规划等自愈动作。
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence


READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "phone_get_page_state",
    "phone_get_focused_state",
    "phone_screenshot",
    "phone_list_devices",
    "phone_get_device_info",
    "phone_get_screen_size",
    "phone_read_clipboard",
    "phone_get_notifications",
    "phone_discover_tools",
    "phone_check_freshness",
    "phone_verify_ad_format",
    "phone_smart_find",
    "phone_res_list",
    "phone_res_get",
    "phone_load_ui_graph",
})


@dataclass
class ToolExecutionRecord:
    """单一工具调用的遥测与打分记录。"""

    tool_name: str
    platform: str = "android"  # "android" | "harmony" | "ios" | "auto"
    device_serial: str = ""
    ok: bool = True
    error: str = ""
    latency_ms: float = 0.0
    ui_changed: bool = True
    healed: bool = False
    heal_level: int = 0  # 0: 无自愈, 1: 关弹窗, 2: 滚动重试, 3: 重新规划路径
    arguments: dict[str, Any] = field(default_factory=dict)
    sub_steps: list[dict[str, Any]] = field(default_factory=list)
    underlying_action: str = ""
    healing_details: dict[str, Any] = field(default_factory=dict)
    response_summary: dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    score: float = 0.0

    def compute_score(self) -> float:
        """根据四维度模型计算 0-100 的综合执行得分。"""
        # 1. 结果正确性 (40 分)
        score_correctness = 40.0 if self.ok else 0.0

        # 2. 端侧有效性 (30 分)
        is_read_only = self.tool_name in READ_ONLY_TOOLS
        if is_read_only:
            score_ui = 30.0 if self.ok else 0.0
        else:
            if not self.ok:
                score_ui = 0.0
            elif self.ui_changed:
                score_ui = 30.0
            else:
                score_ui = 10.0  # 未产生预期页面变化的空击/空操作

        # 3. 响应时延 (20 分)
        lat = max(0.0, float(self.latency_ms))
        if lat <= 500.0:
            score_latency = 20.0
        elif lat <= 1500.0:
            score_latency = 16.0
        elif lat <= 3000.0:
            score_latency = 10.0
        else:
            score_latency = 5.0

        # 4. 自愈代价 (10 分)
        if not self.ok:
            score_healing = 0.0
        elif self.heal_level == 0:
            score_healing = 10.0  # 一次成功
        elif self.heal_level == 1:
            score_healing = 8.0   # Level 1: 关弹窗自愈
        elif self.heal_level == 2:
            score_healing = 5.0   # Level 2: 滚动重试
        else:
            score_healing = 3.0   # Level 3: 重新规划图谱路径

        total = score_correctness + score_ui + score_latency + score_healing
        self.score = round(max(0.0, min(100.0, total)), 1)
        return self.score

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.score == 0.0:
            d["score"] = self.compute_score()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolExecutionRecord":
        rec = cls(
            tool_name=str(data.get("tool_name", "unknown")),
            platform=str(data.get("platform", "android")),
            device_serial=str(data.get("device_serial", "")),
            ok=bool(data.get("ok", True)),
            error=str(data.get("error", "")),
            latency_ms=float(data.get("latency_ms", 0.0)),
            ui_changed=bool(data.get("ui_changed", True)),
            healed=bool(data.get("healed", False)),
            heal_level=int(data.get("heal_level", 0)),
            arguments=dict(data.get("arguments", {})),
            sub_steps=list(data.get("sub_steps", [])),
            underlying_action=str(data.get("underlying_action", "")),
            healing_details=dict(data.get("healing_details", {})),
            response_summary=dict(data.get("response_summary", {})),
            call_id=str(data.get("call_id", uuid.uuid4().hex[:12])),
            timestamp=float(data.get("timestamp", time.time())),
            score=float(data.get("score", 0.0)),
        )
        if rec.score == 0.0:
            rec.compute_score()
        return rec


class TelemetryScoreEngine:
    """遥测收集与评分聚合计算引擎。"""

    def __init__(self, log_path: Optional[str] = None) -> None:
        if log_path:
            self.log_path = pathlib.Path(log_path).expanduser().resolve()
        else:
            env_path = os.environ.get("PHONE_PILOT_TELEMETRY_PATH")
            if env_path:
                self.log_path = pathlib.Path(env_path).expanduser().resolve()
            else:
                self.log_path = pathlib.Path(".recordings/pp_telemetry.jsonl").resolve()

    def record(self, record: ToolExecutionRecord) -> ToolExecutionRecord:
        """追加一条遥测记录至本地存储。"""
        if record.score == 0.0:
            record.compute_score()

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass

        return record

    def load_records(self, limit: int = 2000) -> list[ToolExecutionRecord]:
        """从 JSONL 文件加载近期的执行记录。"""
        if not self.log_path.exists():
            return []

        records: list[ToolExecutionRecord] = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        records.append(ToolExecutionRecord.from_dict(data))
                    except Exception:
                        continue
        except Exception:
            return []

        if limit > 0 and len(records) > limit:
            records = records[-limit:]
        return records

    def aggregate_metrics(
        self, records: Optional[Sequence[ToolExecutionRecord]] = None
    ) -> dict[str, Any]:
        """聚合统计全量执行指标与平台细分数据。"""
        dataset = list(records) if records is not None else self.load_records()
        if not dataset:
            return {
                "total_calls": 0,
                "average_score": 0.0,
                "grade": "N/A",
                "success_rate": 0.0,
                "average_latency_ms": 0.0,
                "platform_breakdown": {},
                "tool_breakdown": {},
                "slow_calls": [],
                "error_breakdown": {},
            }

        total_calls = len(dataset)
        total_score = sum(r.score for r in dataset)
        total_success = sum(1 for r in dataset if r.ok)
        total_latency = sum(r.latency_ms for r in dataset)

        avg_score = round(total_score / total_calls, 1)
        succ_rate = round(total_success / total_calls * 100, 1)
        avg_lat = round(total_latency / total_calls, 1)

        # 评定等级
        if avg_score >= 90.0:
            grade = "A+"
        elif avg_score >= 80.0:
            grade = "A"
        elif avg_score >= 70.0:
            grade = "B"
        elif avg_score >= 60.0:
            grade = "C"
        else:
            grade = "D"

        # 平台细分
        platform_breakdown: dict[str, dict[str, Any]] = {}
        for r in dataset:
            p = r.platform.lower() if r.platform else "android"
            if p not in platform_breakdown:
                platform_breakdown[p] = {"calls": 0, "success": 0, "total_score": 0.0, "total_lat": 0.0}
            entry = platform_breakdown[p]
            entry["calls"] += 1
            if r.ok:
                entry["success"] += 1
            entry["total_score"] += r.score
            entry["total_lat"] += r.latency_ms

        for p, data in platform_breakdown.items():
            cnt = data["calls"]
            data["success_rate"] = round(data["success"] / cnt * 100, 1) if cnt else 0.0
            data["average_score"] = round(data["total_score"] / cnt, 1) if cnt else 0.0
            data["average_latency_ms"] = round(data["total_lat"] / cnt, 1) if cnt else 0.0

        # 工具细分
        tool_breakdown: dict[str, dict[str, Any]] = {}
        for r in dataset:
            t = r.tool_name
            if t not in tool_breakdown:
                tool_breakdown[t] = {"calls": 0, "success": 0, "total_score": 0.0, "total_lat": 0.0}
            entry = tool_breakdown[t]
            entry["calls"] += 1
            if r.ok:
                entry["success"] += 1
            entry["total_score"] += r.score
            entry["total_lat"] += r.latency_ms

        for t, data in tool_breakdown.items():
            cnt = data["calls"]
            data["success_rate"] = round(data["success"] / cnt * 100, 1) if cnt else 0.0
            data["average_score"] = round(data["total_score"] / cnt, 1) if cnt else 0.0
            data["average_latency_ms"] = round(data["total_lat"] / cnt, 1) if cnt else 0.0

        # Top 10 慢调用
        slow_sorted = sorted(dataset, key=lambda x: x.latency_ms, reverse=True)[:10]
        slow_calls = [
            {
                "tool_name": r.tool_name,
                "latency_ms": round(r.latency_ms, 1),
                "platform": r.platform,
                "ok": r.ok,
                "call_id": r.call_id,
            }
            for r in slow_sorted
        ]

        # 错误分类
        error_breakdown: dict[str, int] = {}
        for r in dataset:
            if not r.ok and r.error:
                err_key = r.error.split(":")[0].strip()[:40] or "unknown_error"
                error_breakdown[err_key] = error_breakdown.get(err_key, 0) + 1

        return {
            "total_calls": total_calls,
            "average_score": avg_score,
            "grade": grade,
            "success_rate": succ_rate,
            "average_latency_ms": avg_lat,
            "platform_breakdown": platform_breakdown,
            "tool_breakdown": tool_breakdown,
            "slow_calls": slow_calls,
            "error_breakdown": error_breakdown,
        }


# 全局默认单例与便捷打分记录入口
_default_engine = TelemetryScoreEngine()


def record_tool_telemetry(
    tool_name: str,
    *,
    platform: str = "android",
    device_serial: str = "",
    ok: bool = True,
    error: str = "",
    latency_ms: float = 0.0,
    ui_changed: bool = True,
    healed: bool = False,
    heal_level: int = 0,
    arguments: Optional[dict[str, Any]] = None,
    sub_steps: Optional[list[dict[str, Any]]] = None,
    underlying_action: str = "",
    healing_details: Optional[dict[str, Any]] = None,
    response_summary: Optional[dict[str, Any]] = None,
    engine: Optional[TelemetryScoreEngine] = None,
) -> ToolExecutionRecord:
    """记录一次工具调用的端侧执行表现并返回记录对象。"""
    rec = ToolExecutionRecord(
        tool_name=tool_name,
        platform=platform,
        device_serial=device_serial,
        ok=ok,
        error=error,
        latency_ms=latency_ms,
        ui_changed=ui_changed,
        healed=healed,
        heal_level=heal_level,
        arguments=arguments or {},
        sub_steps=sub_steps or [],
        underlying_action=underlying_action,
        healing_details=healing_details or {},
        response_summary=response_summary or {},
    )
    target_engine = engine or _default_engine
    return target_engine.record(rec)
