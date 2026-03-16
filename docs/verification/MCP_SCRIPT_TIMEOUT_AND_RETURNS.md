# MCP 脚本调用：为何 phone_go_home / phone_launch_app 返回与预期不一致

## 结论（无需重新部署 MCP）

- **不需要重新部署 MCP**，服务与契约正常。
- 返回 `None` 或“与预期不一致”的主要原因是：**每次 `phone-pilot-call` 都会启动一个新的 MCP 服务进程**，冷启动耗时较长，脚本侧超时时间偏短，导致拿不到正常返回。

## 原因说明

1. **stdio 模式下一调用一进程**
   - `python -m phone_pilot.mcp_call <tool_name> --args '...'` 会启动 **一个新的** `python -m phone_pilot.cli`（MCP 服务）子进程。
   - 每次调用都要经历：进程启动 → 加载 server → 初始化 ADB/设备 → 执行工具 → 通过 stdio 回写 JSON。
   - 冷启动通常需要 **20–60 秒**（视机器与设备而定），`phone_list_packages` 等可能更久。

2. **脚本里得到 `None` 的典型情况**
   - 脚本用 `subprocess.run(..., timeout=45)`（或更短）调用 `phone-pilot-call`。
   - 若 45 秒内子进程未结束（例如服务刚启动完、工具还在执行），会触发 **TimeoutExpired**，脚本将这次调用视为失败并得到 **None**。
   - 因此 **phone_go_home**、**phone_launch_app**（以及 **phone_clear_background** 等）在脚本里表现为“返回和预期不一致”（实为超时未拿到任何返回）。

3. **返回结构本身**
   - MCP 的 CallToolResult 会序列化成 JSON 打印到 stdout，形如：`{"content":[{"type":"text","text":"{\"ok\":true,...}"}],...}`。
   - 脚本从 stdout 解析该 JSON，再取 `content[0].text` 做第二次 JSON 解析，得到工具返回的 `{"ok": true, ...}`。  
   - 只要在超时前能拿到完整 stdout，解析逻辑与预期一致；问题在于**超时导致拿不到 stdout**，而不是 MCP 返回格式错误。

## 建议做法

1. **加大单次调用超时**
   - 在通过 `phone-pilot-call` 调用的脚本中，将每次调用的 timeout 设为 **≥90 秒**（例如首几次调用或设备较慢时用 90–120 秒），避免冷启动阶段就被杀进程。
   - 例如 `scripts/douyin_watch_5_videos.py` 中已把 `mcp_call` 的默认 timeout 改为 90 秒。

2. **减少首次冷启动次数**
   - 若脚本会多次调用 MCP（如先 `phone_list_packages` 再 `phone_go_home`、`phone_launch_app`），可尽量合并或减少调用，或对“可推断”的入参不依赖 MCP（例如已知抖音包名时用 `--package`，避免调用 `phone_list_packages`），从而减少冷启动次数、提高成功率。

3. **可选：更稳健地解析 stdout**
   - 若环境里 MCP 服务端日志混入 stdout，可在解析前从 stdout 中只取**最后一段**形如 `{...}` 的 JSON 再解析，避免被前面的日志行干扰（脚本中已对非 `{` 开头的 stdout 做了此类处理）。

## 小结

| 现象 | 原因 | 是否需要重新部署 MCP |
|------|------|----------------------|
| phone_go_home / phone_launch_app 返回 None | 每次调用新建 MCP 进程，冷启动时间长，脚本超时过短 | **否** |
| 返回与预期不一致 | 多为超时未拿到 stdout，或 stdout 中混入非 JSON | **否** |

通过**延长超时**、**减少不必要的 MCP 调用**并在脚本中**只解析合法 JSON**，即可让 phone_go_home、phone_launch_app 等调用的返回与预期一致，无需重新部署 MCP。
