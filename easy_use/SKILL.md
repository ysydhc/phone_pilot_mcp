# phone-pilot 自动化技能指引（已迁移至统一 SSoT）

> **重要提示**：本目录仅保留基础示例脚本 [example_script.py](example_script.py) 以兼容历史引用，不再维护重复的旧版 `SKILL.md` 工具表。
>
> 请按使用场景直接读取以下最新单一事实源（`v0.12.0+`）：
>
> 1. **仓库门面与快速开始**：[../README.md](../README.md)
> 2. **跨端能力成熟度唯一权威矩阵（Android / HarmonyOS / iOS）**：[../FEATURE_MATRIX.md](../FEATURE_MATRIX.md)
> 3. **95 项 MCP 工具 + 65 项 `script_api` 函数 + `ScriptContext` 默认值全量字典**：`<DOC_ROOT>/phone_pilot/01-knowledge/reference-cli-mcp-script-api.md`
> 4. **新项目 15 分钟零侵入接入（`pp_profile.v2` + `acceptance_profile.v1` + 三维业务断言）**：`<DOC_ROOT>/phone_pilot/01-knowledge/integration-and-profiles-spec.md`
>
> ### 常见历史签名防坑提醒
> - `ScriptContext` 中 `auto_screenshot`、`auto_log`、`auto_meminfo` 默认均为 `False`，`auto_report`、`popup_guard`、`clear_logcat_on_start` 默认为 `True`；
> - 跨平台等待并点击请优先使用 `tap_when_ready(ctx, text="...", timeout_s=10)`；旧版 `wait_for_*` 系列函数当前首参为 `device_serial: str`（而非 `ctx`）且仅限 Android。
