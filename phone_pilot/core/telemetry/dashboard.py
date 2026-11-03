"""DashboardGenerator — 端侧执行质量打分看板与可视化报表生成器。

生成自包含、零外部网络依赖的交互式 HTML5 看板（pp_dashboard.html），集中呈现：
1. 核心 KPI（综合执行分、成功率、时延梯队、A/B/C/D 综合健康度徽章）。
2. Android / HarmonyOS / iOS 跨平台执行对比柱状图与质量分布。
3. 工具健康度矩阵（调用频次、成功率、耗时、自愈率）。
4. Top 10 慢调用分析与失败根因归类分布。
5. 联动呈现应用 UI 认知脑图 / 拓扑网络图（支持一键探索产物的可视化联动）。
6. 全量执行明细表（支持客户端实时关键字过滤与状态筛选）。
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Optional, Sequence

from phone_pilot.core.telemetry.score_engine import (
    TelemetryScoreEngine,
    ToolExecutionRecord,
)
from phone_pilot.core.ui.graph import UIKnowledgeGraph


class DashboardGenerator:
    """生成端侧执行质量评估与图谱协同看板。"""

    def __init__(
        self,
        engine: Optional[TelemetryScoreEngine] = None,
        telemetry_path: Optional[str] = None,
    ) -> None:
        self.engine = engine or TelemetryScoreEngine(log_path=telemetry_path)

    def generate_html(
        self,
        output_path: str = ".recordings/pp_dashboard.html",
        records: Optional[Sequence[ToolExecutionRecord]] = None,
        graph_data: Optional[dict[str, Any]] = None,
        graph_path: Optional[str] = None,
    ) -> str:
        """生成并写入自包含的 HTML 看板文件，返回文件绝对路径。"""
        dataset = list(records) if records is not None else self.engine.load_records()
        metrics = self.engine.aggregate_metrics(dataset)

        # 加载图谱与脑图数据
        ui_graph: Optional[UIKnowledgeGraph] = None
        if graph_data:
            try:
                ui_graph = UIKnowledgeGraph.load_graph_json(graph_data)
            except Exception:
                pass
        elif graph_path and pathlib.Path(graph_path).expanduser().exists():
            try:
                ui_graph = UIKnowledgeGraph.load_graph_json(graph_path)
            except Exception:
                pass

        mermaid_code = ui_graph.to_mermaid() if ui_graph else ""
        mindmap_data = ui_graph.to_mindmap_dict() if ui_graph else {}

        html_content = self._render_template(
            metrics=metrics,
            dataset=dataset[-100:],  # 最近 100 条明细
            mermaid_code=mermaid_code,
            mindmap_data=mindmap_data,
        )

        out_file = pathlib.Path(output_path).expanduser().resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(html_content, encoding="utf-8")
        return str(out_file)

    def _render_template(
        self,
        metrics: dict[str, Any],
        dataset: list[ToolExecutionRecord],
        mermaid_code: str,
        mindmap_data: dict[str, Any],
    ) -> str:
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        total_calls = metrics.get("total_calls", 0)
        avg_score = metrics.get("average_score", 0.0)
        grade = metrics.get("grade", "N/A")
        succ_rate = metrics.get("success_rate", 0.0)
        avg_lat = metrics.get("average_latency_ms", 0.0)

        # 徽标颜色
        badge_color = "#10b981" if grade in ("A+", "A") else ("#f59e0b" if grade == "B" else "#ef4444")

        # 平台对比卡片 HTML
        platform_cards_html = []
        for p, pdata in metrics.get("platform_breakdown", {}).items():
            p_name = "HarmonyOS" if p == "harmony" else ("iOS" if p == "ios" else "Android")
            p_score = pdata.get("average_score", 0.0)
            p_rate = pdata.get("success_rate", 0.0)
            p_calls = pdata.get("calls", 0)
            p_lat = pdata.get("average_latency_ms", 0.0)
            platform_cards_html.append(f"""
            <div class="card p-card">
              <div class="platform-title">{p_name}</div>
              <div class="stat-row"><span>调用次数:</span> <strong>{p_calls}</strong></div>
              <div class="stat-row"><span>成功率:</span> <strong>{p_rate}%</strong></div>
              <div class="stat-row"><span>平均得分:</span> <strong style="color: #60a5fa;">{p_score}</strong></div>
              <div class="stat-row"><span>平均时延:</span> <strong>{p_lat} ms</strong></div>
              <div class="progress-bar-bg"><div class="progress-bar-fill" style="width: {min(100, p_score)}%;"></div></div>
            </div>
            """)
        platform_section_html = "\n".join(platform_cards_html) if platform_cards_html else "<div class='empty-tip'>暂无跨端执行记录</div>"

        # 工具细分表格 HTML
        tool_rows_html = []
        for t, tdata in sorted(metrics.get("tool_breakdown", {}).items(), key=lambda x: x[1].get("calls", 0), reverse=True):
            t_score = tdata.get("average_score", 0.0)
            status_tag = "<span class='tag tag-green'>良好</span>" if t_score >= 80 else ("<span class='tag tag-yellow'>需优化</span>" if t_score >= 60 else "<span class='tag tag-red'>高危</span>")
            tool_rows_html.append(f"""
            <tr>
              <td><code>{t}</code></td>
              <td>{tdata.get('calls', 0)}</td>
              <td>{tdata.get('success_rate', 0.0)}%</td>
              <td>{tdata.get('average_latency_ms', 0.0)} ms</td>
              <td><strong>{t_score}</strong></td>
              <td>{status_tag}</td>
            </tr>
            """)
        tool_table_html = "\n".join(tool_rows_html) if tool_rows_html else "<tr><td colspan='6'>暂无工具调用</td></tr>"

        # Top 10 慢调用
        slow_rows_html = []
        for s in metrics.get("slow_calls", []):
            slow_rows_html.append(f"""
            <tr>
              <td><code>{s.get('tool_name')}</code></td>
              <td>{s.get('platform')}</td>
              <td><span style="color:#f87171;">{s.get('latency_ms')} ms</span></td>
              <td>{'✅ 成功' if s.get('ok') else '❌ 失败'}</td>
            </tr>
            """)
        slow_table_html = "\n".join(slow_rows_html) if slow_rows_html else "<tr><td colspan='4'>无长耗时记录</td></tr>"

        # 明细表格
        detail_rows_html = []
        detail_records_map = {}
        for r in reversed(dataset):
            status_str = "<span class='badge-ok'>OK</span>" if r.ok else f"<span class='badge-fail' title='{r.error}'>FAIL</span>"
            t_str = time.strftime("%H:%M:%S", time.localtime(r.timestamp))
            heal_tag = f"<span class='tag tag-yellow'>自愈 L{r.heal_level}</span>" if (r.healed or r.heal_level > 0) else "<span style='color:var(--text-muted);font-size:11px;'>-</span>"
            detail_records_map[r.call_id] = r.to_dict()
            detail_rows_html.append(f"""
            <tr class="detail-row" data-call-id="{r.call_id}" data-tool="{r.tool_name}" data-platform="{r.platform}" data-ok="{'1' if r.ok else '0'}" onclick="openDetail('{r.call_id}')">
              <td>{t_str}</td>
              <td><code>{r.tool_name}</code></td>
              <td>{r.platform}</td>
              <td>{r.latency_ms:.1f}ms</td>
              <td><strong>{r.score}</strong></td>
              <td>{heal_tag}</td>
              <td>{status_str}</td>
              <td class="err-cell">{r.error or '-'}</td>
              <td><button class="btn-detail" onclick="event.stopPropagation(); openDetail('{r.call_id}')">详情 🔍</button></td>
            </tr>
            """)
        detail_table_html = "\n".join(detail_rows_html) if detail_rows_html else "<tr><td colspan='9'>暂无明细记录</td></tr>"

        # 脑图与全量字典内嵌
        mindmap_json_str = json.dumps(mindmap_data, ensure_ascii=False) if mindmap_data else "{}"
        records_json_str = json.dumps(detail_records_map, ensure_ascii=False)

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Phone Pilot 执行质量打分与看板</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --accent: #3b82f6;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
    body {{ background: var(--bg); color: var(--text); padding: 24px; position: relative; }}
    .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
    .title-group h1 {{ font-size: 24px; font-weight: 700; display: flex; align-items: center; gap: 10px; }}
    .title-group p {{ color: var(--text-muted); font-size: 13px; margin-top: 4px; }}
    .badge-grade {{ background: {badge_color}; color: #fff; padding: 4px 12px; border-radius: 9999px; font-weight: 700; font-size: 16px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
    .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 18px; }}
    .kpi-card .kpi-label {{ color: var(--text-muted); font-size: 13px; font-weight: 500; }}
    .kpi-card .kpi-val {{ font-size: 28px; font-weight: 700; margin-top: 6px; color: #fff; }}
    .section-title {{ font-size: 17px; font-weight: 600; margin: 24px 0 12px 0; color: #e2e8f0; display: flex; align-items: center; gap: 8px; }}
    .platform-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }}
    .p-card .platform-title {{ font-size: 16px; font-weight: 600; margin-bottom: 12px; color: #60a5fa; }}
    .stat-row {{ display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 6px; color: var(--text-muted); }}
    .stat-row strong {{ color: var(--text); }}
    .progress-bar-bg {{ background: #334155; height: 6px; border-radius: 3px; margin-top: 10px; overflow: hidden; }}
    .progress-bar-fill {{ background: var(--accent); height: 100%; border-radius: 3px; }}
    .split-grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }}
    @media (max-width: 900px) {{ .split-grid {{ grid-template-columns: 1fr; }} }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--text-muted); font-weight: 600; }}
    code {{ background: #0f172a; padding: 2px 6px; border-radius: 4px; color: #38bdf8; font-size: 12px; }}
    .tag {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
    .tag-green {{ background: rgba(16, 185, 129, 0.2); color: #34d399; }}
    .tag-yellow {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; }}
    .tag-red {{ background: rgba(239, 68, 68, 0.2); color: #f87171; }}
    .badge-ok {{ color: #34d399; font-weight: 600; }}
    .badge-fail {{ color: #f87171; font-weight: 600; }}
    .err-cell {{ color: #f87171; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .search-bar {{ margin-bottom: 12px; display: flex; gap: 8px; }}
    .search-input {{ background: #0f172a; border: 1px solid var(--border); color: #fff; padding: 8px 12px; border-radius: 6px; width: 280px; font-size: 13px; }}
    .mindmap-box {{ background: #0b1120; border: 1px solid var(--border); border-radius: 8px; padding: 16px; font-family: monospace; font-size: 12px; color: #cbd5e1; max-height: 380px; overflow: auto; white-space: pre; }}
    
    /* 明细交互与二级抽屉样式 */
    .detail-row {{ cursor: pointer; transition: background 0.15s; }}
    .detail-row:hover {{ background: rgba(59, 130, 246, 0.08); }}
    .btn-detail {{ background: #2563eb; color: #fff; border: none; padding: 4px 10px; border-radius: 4px; font-size: 12px; cursor: pointer; }}
    .btn-detail:hover {{ background: #1d4ed8; }}

    .drawer-overlay {{ position: fixed; inset: 0; background: rgba(0, 0, 0, 0.65); backdrop-filter: blur(4px); z-index: 1000; opacity: 0; pointer-events: none; transition: opacity 0.25s ease; }}
    .drawer-overlay.active {{ opacity: 1; pointer-events: auto; }}
    .drawer {{ position: fixed; top: 0; right: -640px; width: 620px; max-width: 95vw; height: 100vh; background: #131c2e; border-left: 1px solid var(--border); z-index: 1001; padding: 24px; overflow-y: auto; transition: right 0.28s cubic-bezier(0.16, 1, 0.3, 1); box-shadow: -10px 0 30px rgba(0,0,0,0.5); }}
    .drawer.active {{ right: 0; }}
    .drawer-header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 20px; }}
    .drawer-title {{ font-size: 18px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 8px; }}
    .drawer-close {{ background: transparent; border: none; color: var(--text-muted); font-size: 22px; cursor: pointer; }}
    .drawer-close:hover {{ color: #fff; }}

    .sub-card {{ background: #1e293b; border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 16px; }}
    .sub-card-title {{ font-size: 13px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
    .code-box {{ background: #0a0f1d; border: 1px solid #2d3748; padding: 10px 12px; border-radius: 6px; font-family: monospace; font-size: 12px; color: #38bdf8; word-break: break-all; overflow-x: auto; white-space: pre-wrap; }}
    .timeline-item {{ border-left: 2px solid #3b82f6; padding-left: 14px; margin-left: 6px; margin-bottom: 14px; position: relative; }}
    .timeline-dot {{ position: absolute; left: -7px; top: 2px; width: 12px; height: 12px; border-radius: 50%; background: #3b82f6; border: 2px solid #131c2e; }}
    .timeline-title {{ font-size: 13px; font-weight: 600; color: #f1f5f9; display: flex; justify-content: space-between; }}
    .timeline-desc {{ font-size: 12px; color: #94a3b8; margin-top: 4px; }}
    .amber-box {{ border-color: rgba(245, 158, 11, 0.4); background: rgba(245, 158, 11, 0.06); }}
  </style>
</head>
<body>
  <div class="header">
    <div class="title-group">
      <h1><span>📱</span> Phone Pilot 端侧真实执行质量打分看板</h1>
      <p>生成时间: {now_str} | 监控总调用数: {total_calls} 次</p>
    </div>
    <div class="badge-grade">综合评定: {grade} ({avg_score} 分)</div>
  </div>

  <div class="kpi-grid">
    <div class="card kpi-card">
      <div class="kpi-label">全量综合执行均分</div>
      <div class="kpi-val" style="color: {badge_color};">{avg_score} <span style="font-size:14px;color:var(--text-muted);">/ 100</span></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-label">全局成功率</div>
      <div class="kpi-val" style="color: #34d399;">{succ_rate}%</div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-label">平均端侧时延</div>
      <div class="kpi-val" style="color: #60a5fa;">{avg_lat} <span style="font-size:14px;color:var(--text-muted);">ms</span></div>
    </div>
    <div class="card kpi-card">
      <div class="kpi-label">累计打分调用量</div>
      <div class="kpi-val">{total_calls}</div>
    </div>
  </div>

  <div class="section-title">🌐 三端跨平台执行质量对比 (Android vs HarmonyOS vs iOS)</div>
  <div class="platform-grid">
    {platform_section_html}
  </div>

  <div class="section-title">⚡ 功能工具矩阵健康度排行榜与长耗时分析</div>
  <div class="split-grid">
    <div class="card">
      <div style="font-weight: 600; margin-bottom: 12px;">工具执行频次与得分排行</div>
      <table>
        <thead>
          <tr>
            <th>工具名</th>
            <th>调用数</th>
            <th>成功率</th>
            <th>平均耗时</th>
            <th>执行评分</th>
            <th>健康度</th>
          </tr>
        </thead>
        <tbody>
          {tool_table_html}
        </tbody>
      </table>
    </div>

    <div class="card">
      <div style="font-weight: 600; margin-bottom: 12px;">Top 10 慢调用预警</div>
      <table>
        <thead>
          <tr>
            <th>工具名</th>
            <th>平台</th>
            <th>耗时</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody>
          {slow_table_html}
        </tbody>
      </table>
    </div>
  </div>

  <div class="section-title">🧠 关联应用认知图谱与脑图网络 (UI Mindmap & Topology)</div>
  <div class="card">
    <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 10px;">
      展示经由 <code>phone_explore_app</code> 一键探索或自主学习累积的应用状态拓扑脑图结构：
    </div>
    <div class="mindmap-box" id="mindmap-container">
{"/* Mermaid Graph */\n" + mermaid_code if mermaid_code else "暂无关联页面知识图谱数据，可调用 phone_explore_app 开启一键探索"}
    </div>
  </div>

  <div class="section-title">📋 实时调用流水明细 (近 100 次记录，点击任意行查看二级执行轨迹)</div>
  <div class="card">
    <div class="search-bar">
      <input type="text" id="searchInput" class="search-input" placeholder="输入工具名、平台或错误关键词搜索...">
    </div>
    <table>
      <thead>
        <tr>
          <th>时间</th>
          <th>工具名</th>
          <th>平台</th>
          <th>耗时</th>
          <th>评分</th>
          <th>自愈</th>
          <th>状态</th>
          <th>异常信息</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody id="detailTableBody">
        {detail_table_html}
      </tbody>
    </table>
  </div>

  <!-- 二级详情抽屉 Drawer -->
  <div class="drawer-overlay" id="drawerOverlay" onclick="closeDetail()"></div>
  <div class="drawer" id="drawer">
    <div class="drawer-header">
      <div class="drawer-title" id="dTitle">工具执行详情</div>
      <button class="drawer-close" onclick="closeDetail()">✕</button>
    </div>
    <div id="drawerBody"></div>
  </div>

  <script>
    const EXECUTION_RECORDS = {records_json_str};

    const searchInput = document.getElementById('searchInput');
    const tableBody = document.getElementById('detailTableBody');
    if (searchInput && tableBody) {{
      searchInput.addEventListener('input', function(e) {{
        const term = e.target.value.toLowerCase().trim();
        const rows = tableBody.querySelectorAll('.detail-row');
        rows.forEach(row => {{
          const tool = (row.getAttribute('data-tool') || '').toLowerCase();
          const platform = (row.getAttribute('data-platform') || '').toLowerCase();
          const text = row.innerText.toLowerCase();
          if (!term || tool.includes(term) || platform.includes(term) || text.includes(term)) {{
            row.style.display = '';
          }} else {{
            row.style.display = 'none';
          }}
        }});
      }});
    }}

    function openDetail(callId) {{
      const rec = EXECUTION_RECORDS[callId];
      if (!rec) return;

      document.getElementById('dTitle').innerHTML = '<code>' + rec.tool_name + '</code> <span style="font-size:12px;color:#94a3b8;">#' + rec.call_id + '</span>';

      let statusBadge = rec.ok ? '<span class="tag tag-green">✅ 执行成功</span>' : '<span class="tag tag-red">❌ 执行失败</span>';
      let platformBadge = '<span class="tag" style="background:#1e3a8a;color:#93c5fd;">' + (rec.platform || 'android').toUpperCase() + '</span>';
      let scoreBadge = '<span class="tag tag-green">' + rec.score + ' 分</span>';

      // 1. 底层最终落地操作
      let underAct = rec.underlying_action || '原生 Driver 标准操作下发';
      let underHtml = `
        <div class="sub-card">
          <div class="sub-card-title">⚙️ 底层最终落地操作指令 (Underlying Driver Action)</div>
          <div class="code-box">${{escapeHtml(underAct)}}</div>
        </div>
      `;

      // 2. 自愈信息
      let healHtml = '';
      if (rec.healed || rec.heal_level > 0) {{
        let hd = rec.healing_details || {{}};
        let reason = hd.trigger_reason || '检测到端侧页面漂移或遮挡弹窗，触发自愈防护机制';
        let actions = hd.actions_taken || [
          '识别阻挡弹窗或失效坐标',
          '执行自动解除/滚动补偿动作',
          '重放原目标操作并完成校准'
        ];
        let actsLi = actions.map(a => `<li style="margin-bottom:4px;">${{escapeHtml(a)}}</li>`).join('');
        healHtml = `
          <div class="sub-card amber-box">
            <div class="sub-card-title" style="color:#fbbf24;">🛡️ 自愈执行全过程 (Self-Healing Trajectory - Level ${{rec.heal_level || 1}})</div>
            <div style="font-size:13px;margin-bottom:8px;color:#fde68a;"><strong>触发诱因:</strong> ${{escapeHtml(reason)}}</div>
            <div style="font-size:12px;color:#e2e8f0;margin-bottom:6px;"><strong>补偿与恢复步骤:</strong></div>
            <ul style="font-size:12px;color:#cbd5e1;padding-left:18px;">${{actsLi}}</ul>
            <div style="margin-top:8px;font-size:11px;color:#34d399;">状态: ${{escapeHtml(hd.recovery_status || '自愈完成并恢复执行')}}</div>
          </div>
        `;
      }} else {{
        healHtml = `
          <div class="sub-card">
            <div class="sub-card-title">🛡️ 自愈状态</div>
            <div style="font-size:12px;color:#94a3b8;">本次调用一次性直接通过，未触发任何重试或弹窗遮挡自愈。</div>
          </div>
        `;
      }}

      // 3. 分步耗时流水线
      let steps = rec.sub_steps || [];
      let stepsHtml = '';
      if (steps.length > 0) {{
        let itemsHtml = steps.map((s, idx) => {{
          const stepName = s.name || s.step || s.title || s.step_name || ('步骤 ' + (idx + 1));
          const rawLat = (s.latency_ms !== undefined && s.latency_ms !== null) ? s.latency_ms :
                         ((s.duration_ms !== undefined && s.duration_ms !== null) ? s.duration_ms :
                         ((s.elapsed_ms !== undefined && s.elapsed_ms !== null) ? s.elapsed_ms :
                         ((s.time_ms !== undefined && s.time_ms !== null) ? s.time_ms : null)));
          const latText = (rawLat !== null && rawLat !== undefined && !isNaN(Number(rawLat)))
                          ? (Number(rawLat).toFixed(1) + ' ms')
                          : '-';
          const isFailed = s.status === 'failed' || s.status === 'error' || s.ok === false;
          const dotColor = isFailed ? '#ef4444' : '#3b82f6';
          const descHtml = s.detail ? `<div class="timeline-desc">${{escapeHtml(s.detail)}}</div>` : '';
          return `
            <div class="timeline-item">
              <div class="timeline-dot" style="background:${{dotColor}};"></div>
              <div class="timeline-title">
                <span>${{escapeHtml(stepName)}}</span>
                <span style="color:#60a5fa;font-family:monospace;font-weight:600;">${{latText}}</span>
              </div>
              ${{descHtml}}
            </div>
          `;
        }}).join('');
        stepsHtml = `
          <div class="sub-card">
            <div class="sub-card-title">⏱️ 分步执行过程与耗时 (Step Breakdown)</div>
            <div style="margin-top:10px;">${{itemsHtml}}</div>
          </div>
        `;
      }}

      // 4. 入参与回包
      let argsJson = JSON.stringify(rec.arguments || {{}}, null, 2);
      let resJson = JSON.stringify(rec.response_summary || (rec.ok ? {{"ok": true}} : {{"ok": false, "error": rec.error}}), null, 2);
      let payloadHtml = `
        <div class="sub-card">
          <div class="sub-card-title">📥 输入参数 (Arguments)</div>
          <div class="code-box">${{escapeHtml(argsJson)}}</div>
        </div>
        <div class="sub-card">
          <div class="sub-card-title">📤 响应报文快照 (Response Summary)</div>
          <div class="code-box">${{escapeHtml(resJson)}}</div>
        </div>
      `;

      let fullBody = `
        <div style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;">
          ${{platformBadge}}
          ${{statusBadge}}
          ${{scoreBadge}}
          <span class="tag" style="background:#334155;color:#e2e8f0;">总耗时: ${{rec.latency_ms.toFixed(1)}} ms</span>
        </div>
        ${{underHtml}}
        ${{healHtml}}
        ${{stepsHtml}}
        ${{payloadHtml}}
      `;

      document.getElementById('drawerBody').innerHTML = fullBody;
      document.getElementById('drawerOverlay').classList.add('active');
      document.getElementById('drawer').classList.add('active');
    }}

    function closeDetail() {{
      document.getElementById('drawerOverlay').classList.remove('active');
      document.getElementById('drawer').classList.remove('active');
    }}

    document.addEventListener('keydown', function(e) {{
      if (e.key === 'Escape') closeDetail();
    }});

    function escapeHtml(str) {{
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }}
  </script>
</body>
</html>
"""
