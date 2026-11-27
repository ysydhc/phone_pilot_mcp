# PhonePilot (`pp`) — AI-Native Mobile Automation & Human-Speed Acceptance Engine

**版本**：`0.13.0` | **Python**：`>= 3.13` | **协议**：MCP (`stdio`) + Python `script_api` | **许可**：MIT

`phone_pilot`（简称 `pp`）是一套面向研发与发版验收闭环的**跨平台（Android / HarmonyOS / iOS）AI 原生移动自动化与极速审片基础设施**。它让 AI Agent 与测试脚本在同一套零业务硬编码底座上完成：**编译新鲜度阻断 ➔ 多模态感知与操作 ➔ 本地双端口网络打桩 ➔ 三证合一人眼极速审片 ➔ 受控真机现场复原**。

---

## 一、核心解决痛点与量化基准

| 传统移动自动化痛点 | PhonePilot 核心机制 | 实测工程收益 |
| :--- | :--- | :--- |
| **改完代码测到旧包盲调** | `FreshnessChecker` 核验源码 `mtime`、包体时间、新进程 `PID` 与二进制符号 | 100% 阻断旧二进制假跑 |
| **原生 UI 树撑爆 LLM 上下文且二次 Dump 极慢** | 20 核心 + 75 按需扩展分层工具集、`SemanticProjector` 降噪、0.5s/30s 双层快照 | 工具描述 Token 降 `77%`（10K→2.3K），页面 JSON 降 `>= 70%` |
| **WebView / 游戏 Canvas 无障碍树失明** | 原生控件树（含鸿蒙 `synthetic_id`）➔ 本地 OCR ➔ VLM `[0,1000]` 归一化视觉定位 | 零硬编码像素坐标跨分辨率自适应 |
| **多 Agent 抢占同一台测试机互相切后台** | `DeviceLeaseManager` 内核级 `flock` + TTL 自动回收 + `PHONE_PILOT_DEVICE_SERIAL` 空间绑定 | 并发写操作自动拦截（`device_busy_locked`） |
| **人工验报告需反复滚屏、拖进度条、翻英文日志** | v4.0 极速审片台：生命周期胶囊灯带、靶心标注截图、视频-日志单调时间轴双向联动、键盘 `←/→` 批阅 | 单场景人工核验 `~35s -> <= 3s`，17 场景门禁拍板 `<= 45s` |

---

## 二、3 分钟快速上手

### 1. 安装与环境自检

```bash
# 核心安装（默认含 Android / OpenCV Headless / Jinja2 报告引擎）
pip install phone-pilot

# 按需安装平台与扩展能力
pip install "phone-pilot[harmony]"   # HarmonyOS (hmdriver2 + HDC)
pip install "phone-pilot[ios]"       # iOS (WebDriverAgent + pymobiledevice3, 仅 macOS)
pip install "phone-pilot[restore]"   # Android 现场复原 APK Manifest 静态校验 (androguard)
pip install "phone-pilot[vision]"    # 多语种检测与翻译扩展
pip install -e ".[dev]"              # 本地源码开发模式

# 检查本机 ADB / Tesseract / OpenCV 可选依赖就绪状态
python scripts/check_verification_env.py
```

### 2. 在 MCP 客户端（Cursor / Claude / Antigravity）中挂载

```json
{
  "mcpServers": {
    "phone_pilot": {
      "command": "phone-pilot-mcp",
      "args": [],
      "env": {
        "PHONE_PILOT_PLATFORM": "auto"
      }
    }
  }
}
```

### 3. 四大命令行入口（CLI）

```bash
# 1. 启动 MCP stdio 服务端
phone-pilot-mcp

# 2. 命令行直调任意 MCP 工具或自省公开 API
phone-pilot-call --list-tools
phone-pilot-call --list-script-api
phone-pilot-call phone_get_page_state --args '{"annotate_elements": true}'

# 3. 启动本地可视化验收工作台（默认 http://127.0.0.1:8686）
# 注：默认开启本地有效 run 的复原审批，共享/严格环境请显式传 --no-auto-approve
phone-pilot-web --port 8686

# 4. 命令行只读预检或受控执行设备语义现场复原（非录像回放）
phone-pilot-restore .recordings/runs/<run_dir>
phone-pilot-restore .recordings/runs/<run_dir> --device-serial <serial> --approve-manifest-sha256 <sha256>
```

### 4. 最小 Python 业务验收脚本示例（`script_api`）

```python
from phone_pilot.script_api import ScriptContext, find_text, run_script, tap_when_ready

ctx = ScriptContext(platform="auto", auto_screenshot=True, popup_guard=True)

def main() -> int:
    ctx.declare_cases([{
        "case_id": "TC-01",
        "steps": [{
            "business_step_id": "open_target",
            "title": "进入目标业务页",
            "expected": "目标控件可见",
            "required_dimensions": ["ui"],
            "not_applicable": {"callback_log": "纯UI示例", "network": "无网络断言"},
            "required_evidence": ["screenshot"],
        }],
    }])
    with ctx.business_step("TC-01", "open_target", action="点击入口按钮", environment_ref="local-dev") as step:
        res = tap_when_ready(ctx, text="开始体验", timeout_s=10)
        ev = step.capture("target_screen")
        ok = bool(res.get("ok") and find_text(ctx, "目标标题", retry_attempts=2))
        step.add_assertion("ui", "PASS" if ok else "FAIL", "目标标题可见", f"ok={ok}", evidence_refs=[ev["evidence_id"]])
        step.actual = f"ok={ok}"
    return 0

if __name__ == "__main__":
    run_script(main, ctx=ctx)
```

---

## 三、平台成熟度摘要

详细逐项能力请查阅全仓唯一权威矩阵 [FEATURE_MATRIX.md](FEATURE_MATRIX.md)：

| 平台 | 驱动底座 | 当前成熟度分级 | 关键边界说明 |
| :--- | :--- | :---: | :--- |
| **Android** | `adb` + `uiautomator` + `adb reverse` | **`GA`（生产就绪）** | 全量 95 项 MCP 工具、170s 分段无损录屏拼接、Mock 隧道、内存 `hprof` 与受控现场复原完整支持 |
| **HarmonyOS** | `hdc` + `hmdriver2` + `HarmonySessionLock` | **`Beta`（真机就绪）** | 已通过华为真机 7 大领域与 253 项单测；要求执行前解锁屏幕（避免锁屏拦截 AAMS），单机 `uitest` 独占 |
| **iOS Simulator** | `WebDriverAgent` + `xcrun simctl` | **`Beta`（模拟器就绪）** | 支持 DPR 像素/逻辑点自动换算、localhost 免转发 Mock、MP4 录屏、`NSUserDefaults` 模板注入与现场复原 |
| **iOS 真机** | `WebDriverAgent` + `pymobiledevice3` | **`Experimental`（实验态）** | 43 项 `DeviceDriver` 协议已实现；需 macOS + Xcode 签名 WDA + iOS 17+ RSD 开发者隧道；非越狱不支持 `clear_data` 与真机录屏 |

---

## 四、角色文档导航（深入阅读）

- **跨平台能力分级明细**：[FEATURE_MATRIX.md](FEATURE_MATRIX.md)
- **维护者代码架构与模块规范**：[CLAUDE.md](CLAUDE.md)
- **全量产品与技术知识中心（外部 `<DOC_ROOT>/phone_pilot/`，由 `$AGENT_DOC_ROOT` 指定）**：
  - 全景索引与进度看板：`<DOC_ROOT>/phone_pilot/00-index/INDEX.md`
  - 新项目 15 分钟零侵入接入与双 Profile 契约（`pp_profile.v2` / `acceptance_profile.v1`）：`<DOC_ROOT>/phone_pilot/01-knowledge/integration-and-profiles-spec.md`
  - v4.0 极速人眼审片工作台与受控现场复原边界手册：`<DOC_ROOT>/phone_pilot/01-knowledge/human-acceptance-and-restore-guide.md`
  - 六层架构与 L1 零业务耦合宪章：`<DOC_ROOT>/phone_pilot/01-knowledge/product-and-architecture-overview.md`
  - 四维质量打分、新鲜度门禁与排障手册：`<DOC_ROOT>/phone_pilot/01-knowledge/quality-scoring-and-triage-spec.md`
  - 多 Agent 并发设备租约锁与录制转脚本规范：`<DOC_ROOT>/phone_pilot/01-knowledge/agent-concurrency-and-authoring.md`
  - 四大 CLI / 95 项 MCP / 65 项 `script_api` / 环境变量全量字典：`<DOC_ROOT>/phone_pilot/01-knowledge/reference-cli-mcp-script-api.md`

---

## 五、开发与测试门禁

```bash
make lint          # ruff check .
make format-check  # ruff format --check .
make test          # pytest 全量单元与零业务耦合门禁测试
make check         # lint + test
```
