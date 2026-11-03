"""Unit tests for TelemetryScoreEngine, DashboardGenerator, and phone_generate_dashboard."""

from __future__ import annotations

import pathlib
import pytest

from phone_pilot.cli import main as cli_main
from phone_pilot.core.telemetry.dashboard import DashboardGenerator
from phone_pilot.core.telemetry.score_engine import (
    TelemetryScoreEngine,
    ToolExecutionRecord,
    record_tool_telemetry,
)
from phone_pilot.core.ui.graph import UIKnowledgeGraph, UINode
from phone_pilot.core.ui.page import PageSignature
from phone_pilot.mcp._evolution_tools import phone_generate_dashboard


def test_score_calculation_rules():
    # 满分案例: 成功 (40) + 有效变化 (30) + 低时延 <=500ms (20) + 无自愈 (10) = 100
    rec_perfect = ToolExecutionRecord(
        tool_name="phone_tap_text",
        platform="android",
        ok=True,
        ui_changed=True,
        latency_ms=250.0,
        heal_level=0,
    )
    assert rec_perfect.compute_score() == 100.0

    # 失败案例: ok=False -> score=0
    rec_fail = ToolExecutionRecord(
        tool_name="phone_tap_text",
        platform="harmony",
        ok=False,
        error="element_not_found",
        latency_ms=300.0,
    )
    assert rec_fail.compute_score() == 20.0  # 失败时正确性=0, UI=0, 自愈=0, 时延<=500ms=20

    # 空击与中时延案例: ok=True (40) + 无UI变化空击 (10) + 时延1200ms (16) + Level 1关弹窗 (8) = 74
    rec_empty = ToolExecutionRecord(
        tool_name="phone_tap",
        platform="ios",
        ok=True,
        ui_changed=False,
        latency_ms=1200.0,
        heal_level=1,
    )
    assert rec_empty.compute_score() == 74.0

    # 只读工具案例: 无论 ui_changed 是什么，都视同有效变化 (30)
    rec_read = ToolExecutionRecord(
        tool_name="phone_screenshot",
        platform="android",
        ok=True,
        ui_changed=False,
        latency_ms=400.0,
        heal_level=0,
    )
    assert rec_read.compute_score() == 100.0


def test_telemetry_engine_and_aggregation(tmp_path):
    log_file = str(tmp_path / "telemetry.jsonl")
    engine = TelemetryScoreEngine(log_path=log_file)

    # 注入测试样本
    record_tool_telemetry(
        "phone_tap_text",
        platform="android",
        ok=True,
        latency_ms=200.0,
        engine=engine,
    )
    record_tool_telemetry(
        "phone_swipe",
        platform="harmony",
        ok=True,
        latency_ms=600.0,
        engine=engine,
    )
    record_tool_telemetry(
        "phone_go_recents",
        platform="ios",
        ok=False,
        error="timeout_after_3000ms",
        latency_ms=3200.0,
        engine=engine,
    )

    records = engine.load_records()
    assert len(records) == 3

    metrics = engine.aggregate_metrics(records)
    assert metrics["total_calls"] == 3
    assert metrics["grade"] in ("A+", "A", "B", "C", "D")
    assert "android" in metrics["platform_breakdown"]
    assert "harmony" in metrics["platform_breakdown"]
    assert "ios" in metrics["platform_breakdown"]
    assert len(metrics["slow_calls"]) == 3
    assert metrics["slow_calls"][0]["tool_name"] == "phone_go_recents"


def test_dashboard_html_generation(tmp_path):
    log_file = str(tmp_path / "telemetry.jsonl")
    out_html = str(tmp_path / "dashboard.html")
    engine = TelemetryScoreEngine(log_path=log_file)

    record_tool_telemetry("phone_tap_text", platform="android", ok=True, latency_ms=180.0, engine=engine)
    record_tool_telemetry("phone_go_recents", platform="harmony", ok=True, latency_ms=400.0, engine=engine)

    # 构造测试认知图谱
    graph = UIKnowledgeGraph()
    graph.add_or_update_node(
        UINode(
            node_id="home",
            page_signature=PageSignature(name="home", activity_or_controller="MainActivity"),
            activity_or_vc="MainActivity",
        )
    )

    gen = DashboardGenerator(engine=engine)
    res_path = gen.generate_html(
        output_path=out_html,
        graph_data=graph.export_graph_json(),
    )

    assert pathlib.Path(res_path).exists()
    content = pathlib.Path(res_path).read_text(encoding="utf-8")
    assert "Phone Pilot 端侧真实执行质量打分看板" in content
    assert "HarmonyOS" in content
    assert "Android" in content
    assert "MainActivity" in content
    assert "phone_go_recents" in content
    assert 'id="drawer"' in content
    assert "EXECUTION_RECORDS" in content
    assert "openDetail" in content


def test_dashboard_drawer_sub_steps_and_healing(tmp_path):
    log_file = str(tmp_path / "telemetry_drawer.jsonl")
    out_html = str(tmp_path / "dashboard_drawer.html")
    engine = TelemetryScoreEngine(log_path=log_file)

    rec = ToolExecutionRecord(
        tool_name="phone_tap_text",
        platform="android",
        ok=True,
        latency_ms=450.0,
        healed=True,
        heal_level=1,
        underlying_action="adb shell input tap 540 1200",
        sub_steps=[
            {"step": "UIHierarchyDump", "duration_ms": 110, "status": "success", "detail": "dump xml hierarchy"},
            {"step": "DismissOverlay", "duration_ms": 180, "status": "success", "detail": "auto-dismiss permission dialog"},
            {"step": "LowLevelDispatch", "duration_ms": 160, "status": "success", "detail": "adb shell input tap 540 1200"},
        ],
        healing_details={
            "trigger_reason": "目标元素被系统权限遮罩阻挡",
            "healing_strategy": "OverlayDismissal",
            "healing_actions": ["自动点击始终允许", "重新沉淀并Dump"],
            "recovery_status": "成功消除遮罩并命中元素",
        },
    )
    engine.record(rec)

    gen = DashboardGenerator(engine=engine)
    res_path = gen.generate_html(output_path=out_html)
    content = pathlib.Path(res_path).read_text(encoding="utf-8")

    assert "adb shell input tap 540 1200" in content
    assert "目标元素被系统权限遮罩阻挡" in content
    assert "OverlayDismissal" in content
    assert "UIHierarchyDump" in content


@pytest.mark.asyncio
async def test_mcp_generate_dashboard(tmp_path):
    out_html = str(tmp_path / "mcp_dashboard.html")
    res = await phone_generate_dashboard(output_path=out_html)
    assert res["ok"] is True
    assert pathlib.Path(res["dashboard_path"]).exists()
    assert "average_score" in res


def test_cli_dashboard_command(tmp_path):
    out_html = str(tmp_path / "cli_dashboard.html")
    ret = cli_main(["dashboard", "--output", out_html])
    assert ret == 0
    assert pathlib.Path(out_html).exists()
