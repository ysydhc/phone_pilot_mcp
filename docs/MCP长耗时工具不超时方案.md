# MCP 长耗时工具「保证不超时断开」修改方案

## 一、现状与根因

- **现象**：外部客户端调用时，如「获取设备信息」成功，但「开始录屏」或后续步骤报 `Connection closed` / `Not connected`。
- **根因**：MCP 是**请求-响应同步**模型，客户端发一次请求后会一直等服务器返回。多数客户端/传输层有**读超时**（常见 30～60s）。若某次工具执行时间超过该超时，客户端会主动断开，后续所有调用都变成 Not connected。
- **“减少耗时”的局限**：通过快速路径、to_thread 等把单次调用压到 1～3s，只能**降低**超时概率，无法在协议层面**保证**不超时（网络抖动、设备慢、客户端超时设得很短时仍会断）。

---

## 二、本仓库已有可参考做法

### 2.1 Logcat 工具（phone_start_logcat / phone_stop_logcat）

- **行为**：`phone_start_logcat` 内部调 `driver.start_log_capture(...)`，该调用在设备上**启动后台进程**后立即返回，不等待日志写完；结果写入 `_logcat_state`，`phone_stop_logcat` 用该状态停止。
- **为何不易超时**：真正“长耗时”的是**设备上的 logcat 进程**，不在 MCP 请求内；MCP 请求只做「启动子进程 + 写状态」，耗时短。
- **与录屏的差异**：录屏的「启动」在部分环境下会偏慢（如 `_resolve_device_serial` 的 adb、或设备上 screenrecord 起得慢），且当前实现是**等整段同步逻辑跑完才返回**，容易顶到客户端超时。

### 2.2 业界推荐：异步交接（Asynchronous Hand-Off）

- 思路：**不要让客户端“等长耗时做完”**，而是「先立刻返回一个句柄，长耗时在后台做，客户端用句柄查状态/取结果」。
- 典型形态（参见 [MCP 长耗时工具实践](https://www.arsturn.com/blog/no-more-timeouts-how-to-build-long-running-mcp-tools-that-actually-finish-the-job)）：
  - `start_X(...)` → 服务器**立即**返回 `task_id`（或等效句柄），后台 `asyncio.create_task` 真正执行；
  - `query_X(task_id)` → 快速返回当前状态（running / completed / failed）；
  - `wait_X(task_id)` 或轮询 → 客户端在需要结果时再查；
  - （可选）`cancel_X(task_id)` → 取消任务。
- 这样**每次 MCP 调用都只做轻量逻辑**，响应时间远小于客户端超时，从设计上**保证不会因“本次调用太慢”而断开**。

---

## 三、可选方案对比

### 方案 A：立即返回 + 后台执行，stop 时短时等待 start 完成（推荐作为折中）

**做法**：

- **phone_start_recording**  
  - 收到请求后：`asyncio.create_task(执行现有的 _phone_start_recording_sync)`，**不 await**；  
  - 立刻返回：`{ "ok": True, "status": "starting", "device_serial": "<传入或解析出的 serial>", "message": "录屏已在后台启动，可稍后调用 phone_stop_recording 停止" }`（可选带 `expected_local_path` 等）。
- **phone_stop_recording**  
  - 若当前没有该设备的 `_recording_state`：认为 start 可能仍在后台执行，**在服务器内等待**该设备对应的「start 任务」完成（例如用 `asyncio.Future` 或 `Event`，最多等 10～15s）；  
  - 若超时仍无 state：返回 `not_recording`；  
  - 若有 state：用现有逻辑（含 driver 缓存）执行 stop、拉文件、清状态并返回。

**优点**：

- 从客户端看：**start 几乎一定在 1s 内返回**，不会因「录屏启动慢」导致 Connection closed。
- 客户端流程**无需改**：仍可先调 start 再调 stop；stop 在内部“等 start 完成再停”，对调用方透明。
- 实现量适中：主要改 start/stop 两处，并增加「按 device 的 start 完成 Future/Event」。

**缺点**：

- 若客户端在 start 后**立刻**调 stop，stop 的返回会延迟到「后台 start 完成」之后（最多约 10～15s），这段时间内**客户端若也有较短读超时**，仍可能断连（但多数客户端 15s+ 可接受）。
- 语义上 start 返回时录屏未必已真正开始，若客户端不调 stop 就断开，需要依赖「后台任务跑完并写入 state」才能保证 stop 可用。

---

### 方案 B：完整异步交接（start → 立即返回；status 查询；stop 仅在有 state 时调用）

**做法**：

- **phone_start_recording**  
  - 与方案 A 相同：`create_task` 执行实际逻辑，**立即**返回 `{ ok, status: "starting", device_serial, task_id? }`。
- **新增 phone_recording_status(device_serial)**  
  - 返回：`{ status: "starting" | "started" | "not_recording" | "failed", local_path?, error? }`；  
  - 客户端可轮询，直到 `status === "started"` 再调 stop。
- **phone_stop_recording**  
  - 仅当该设备已有 `_recording_state`（即 status 已为 started）时才执行停止逻辑；  
  - 若为 `starting`：返回 `{ ok: False, error: "recording_still_starting", message: "请稍后重试或先调用 phone_recording_status 确认" }`。

**优点**：

- **每次 MCP 调用都极短**（只读内存状态或发轻量指令），从设计上**保证不因工具执行时间长而超时断开**。
- 语义清晰：start = 提交任务；status = 查状态；stop = 在已 started 时停止，符合业界「异步 hand-off」模式。
- 便于扩展：以后其他长耗时工具也可复用「task_id / status / 轮询」模式。

**缺点**：

- **客户端/脚本必须改**：需要先 start → 轮询 status（或重试）→ 再 stop；不能「start 后立刻 stop」而不处理 starting。
- 多一个工具与约定，对外文档和示例都要更新。

---

### 方案 C：不改协议，仅“尽量缩短单次调用”（当前已做 + 快速路径）

**做法**：

- 保持「start 和 stop 都是同步等结果再返回」；  
- 继续通过 to_thread、快速路径（有 device_serial 时跳过 resolve）、driver 缓存等，把单次耗时压到 1～3s 内。

**优点**：

- 无需改接口、无需新工具、客户端无感知；实现已完成大部分。

**缺点**：

- **无法保证**不超时：设备慢、网络差或客户端超时设得短（如 2s）时，仍可能 Connection closed；你已明确需要的是「真正保证不会超时断开」，故该方案不满足目标。

---

## 四、建议与结论

- 若目标为**从机制上保证不因“单次调用太慢”而超时断开**，应采用**异步交接**思路：**至少**对「开始录屏」做「立即返回 + 后台执行」。
- **推荐优先落地方案 A**：  
  - start 立即返回，后台执行；  
  - stop 在无 state 时短时等待 start 完成再执行；  
  - 不新增工具、不强制客户端改流程，又能显著降低 start 导致的断连。
- 若希望**彻底**与客户端超时解耦、且能接受改客户端逻辑，再在方案 A 基础上增加 **phone_recording_status**（方案 B），并约定：客户端先 start → 轮询 status 为 started → 再 stop，这样可**严格保证**每次请求都在极短时间内返回。

**方案 B 已实现**（当前代码）：

- **phone_start_recording(device_serial=...)**：当传入非空 `device_serial` 时**立即**返回 `{ "ok": True, "status": "starting", "device_serial": "<serial>", "message": "..." }`，录屏在后台启动；未传 `device_serial` 时保持原有同步行为（等待完成后返回），兼容旧调用方。
- **phone_recording_status(device_serial)**：新增工具，返回 `{ "ok": True, "status": "starting"|"started"|"not_recording"|"failed", "local_path"?, "error"? }`，客户端可轮询直至 `status === "started"`。
- **phone_stop_recording**：仅当该设备已处于 `started`（即 `_recording_state` 中已有该设备）时执行停止；若为 `starting` 返回 `recording_still_starting`，否则返回 `not_recording`，并提示先轮询 status。

**推荐客户端流程**（保证不因读超时断开）：  
1. 调用 `phone_list_devices` 获取 `device_serial`；  
2. 调用 `phone_start_recording(device_serial=<serial>)`，得到 `status: "starting"`；  
3. 轮询 `phone_recording_status(device_serial=<serial>)` 直至 `status === "started"`；  
4. 调用 `phone_stop_recording(device_serial=<serial>)`。

---

## 五、各方案对应的已开源工具/实现选择

以下是与各方案相关的**已开源**协议、SDK 或运行时，便于选型或后续对齐标准。

### 方案 A（立即返回 + 后台执行，stop 内等 start）

| 类型 | 工具/项目 | 说明 | 链接 |
|------|-----------|------|------|
| 运行时 | **Python 标准库 asyncio** | 无需新依赖：`asyncio.create_task()` + `asyncio.Future` / `Event` 即可实现「立即返回、后台执行、stop 内等待」。当前仓库已用 asyncio，可直接在现有 FastMCP 上实现。 | — |
| 服务端框架 | **FastMCP (Prefect)** | 支持 `@mcp.tool(task=True)`，工具可声明为“后台任务”；若客户端支持 MCP 的 task 协议，可自动获得「启动即返回、轮询/取结果」的交互。可选依赖 Docket 做分布式队列。 | [FastMCP Background Tasks](https://gofastmcp.com/servers/tasks)、[PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp) |

**小结**：方案 A 用 **asyncio 即可自实现**，不强制引入新协议；若希望与 MCP 标准任务模型对齐，可评估 FastMCP 的 `task=True` 及客户端支持情况。

---

### 方案 B（完整异步交接：start + status + stop）

| 类型 | 工具/项目 | 说明 | 链接 |
|------|-----------|------|------|
| 协议标准 | **MCP 2025-11-25 Tasks** | 官方规范中的 **Tasks** 能力：请求可返回 task 句柄，客户端通过 `tasks/async/status` 轮询、`tasks/async/result` 取结果；状态含 `working` / `completed` / `failed` / `cancelled` 等。 | [MCP Spec - Tasks](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)、[Discussion #491](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/491) |
| Python SDK | **modelcontextprotocol/python-sdk** | 官方 Python SDK，PR/路线图中包含 **异步工具执行**：`tools/call` 返回 operation token，`tools/async/status` 轮询，`tools/async/result` 取结果。与方案 B 的「start → status → result」一致。 | [python-sdk](https://github.com/modelcontextprotocol/python-sdk)、[PR #1398 (async tools)](https://github.com/modelcontextprotocol/python-sdk/pull/1398) |
| 服务端/传输 | **FastMCP (task=True)** | 同上；声明为 task 的工具由协议层返回任务句柄，客户端轮询/取结果，无需自己维护 status 接口（若客户端支持）。 | [FastMCP Background Tasks](https://gofastmcp.com/servers/tasks) |
| 异步传输 | **asyncmcp** | 面向队列/异步的 MCP 传输层，支持 SQS、SNS、webhooks、流式 HTTP 等，便于“长耗时不占住连接”。 | [PyPI: asyncmcp](https://pypi.org/project/asyncmcp/) |

**小结**：方案 B 与 **MCP Tasks / 异步工具规范** 对齐；实现上可用官方 Python SDK 的异步工具能力，或 FastMCP 的 task 支持；若仅自建 status 接口（如 `phone_recording_status`），也可不换 SDK，在现有服务上扩展。

---

### 方案 C（仅优化耗时，不改协议）

无需额外开源“工具”；继续用当前 **phone_pilot + FastMCP + asyncio.to_thread** 即可。无新选型。

---

### 可选增强：持久化/可恢复执行（Durable Execution）

若未来希望「任务可跨进程/重启恢复、可审计、可扩展多 worker」，可考虑与“长耗时 + 不超时”配合的**持久化执行**方案：

| 类型 | 工具/项目 | 说明 | 链接 |
|------|-----------|------|------|
| 工作流引擎 | **Temporal** | 开源、可自托管；Python SDK 提供 **durable asyncio**，工作流可崩溃恢复、可重放。适合“录屏/脚本等长流程”做成 Activity + Workflow，MCP 只负责触发与查询。 | [temporalio/sdk-python](https://github.com/temporalio/sdk-python)、[Temporal 文档](https://docs.temporal.io/develop/python/) |
| 轻量任务队列 | **Docket (pydocket)** | 基于 Redis Streams 的 Python 后台任务队列；FastMCP 的 task 可选 Docket 做分布式执行。比 Temporal 轻，适合“仅需队列 + 后台执行、不需完整工作流”的场景。 | [chrisguidry/docket](https://github.com/chrisguidry/docket)、[docket.lol](https://docket.lol/) |

**小结**：方案 A/B 不依赖 Temporal/Docket；若后续要「服务重启不丢任务、多实例部署」，再引入 Durable 层更合适。

---

## 六、现有开源 Android MCP 接入工具的不阻塞实现方式

以下为**已开源的、面向 Android 的 MCP 服务**在「不阻塞 / 长耗时」上的常见实现方式（按语言/运行时区分）。便于对照本仓库的 to_thread / 异步方案，并理解生态里普遍做法。

### 6.1 JavaScript/Node 系

| 项目 | 不阻塞方式 | 说明 |
|------|------------|------|
| **landicefu/android-adb-mcp-server** | **原生 async/await + 异步 I/O** | 工具实现为 **async 函数**，ADB 通过 `child_process` 的异步接口（如 `exec` 返回 Promise、或 spawn + 事件）调用，不占用主线程；Node 单线程事件循环下每次请求快速返回。 |
| **isseikz/mcp-adb** | 同上（Node 惯例） | 典型 Node MCP 服务：工具为 async，外部命令/ADB 用异步子进程或封装成 Promise，天然不阻塞。 |

**小结**：JS/Node 生态里 MCP 工具普遍是 **async 工具 + 异步子进程/IO**，不依赖“线程池包装”，事件循环不会被一次长 adb 调用占满。

---

### 6.2 Kotlin/JVM 系

| 项目 | 不阻塞方式 | 说明 |
|------|------------|------|
| **ulcica/android-mcp** | **Kotlin 协程（Coroutines）** | 文档明确写 **“performance optimization using Kotlin coroutines and caching”**、**“Uses coroutines for non-blocking operations”**；ADB/设备 I/O 放在挂起函数中，由协程调度器在后台线程执行，不阻塞连接处理线程。 |
| **InakiBes/adb-mcp-server** | 同上（JVM 常见） | Kotlin/JVM 上 MCP 工具通常用协程或 `runBlocking` 封装阻塞调用；若用协程 + `withContext(Dispatchers.IO)` 等，则等效“不阻塞主调度器”。 |

**小结**：Kotlin 系通过 **协程 + IO 调度器** 把 adb/设备操作放到后台，主线程只做协议与调度，实现不阻塞。

---

### 6.3 Python 系（经源码确认）

以下结论基于对三个开源仓库 **源码** 的查看（克隆后检索工具定义与 adb/u2 调用方式）。

| 项目 | 工具定义 | 设备/ADB 调用方式 | 是否做不阻塞处理 |
|------|----------|-------------------|------------------|
| **minhalvp/android-mcp-server** | 全部 **同步** `def`（如 `get_packages`, `execute_adb_shell_command`, `get_uilayout`, `get_screenshot`）。 | 通过 **ppadb**（Pure Python ADB）：`self.device.shell(command)`、`self.device.pull()` 等，均为阻塞调用；`check_adb_installed()` 使用 `subprocess.run(["adb", "version"], ...)`。 | **否**。无 `asyncio.to_thread`、无 `run_in_executor`、无异步子进程。工具在 FastMCP 中同步执行，会阻塞事件循环。 |
| **nim444/mcp-android-server-python** | 绝大多数为 **同步** `def`（如 `get_device_status`, `connect_device`, `get_element_info`, `screen_on`, `wait_activity` 等）。仅 **一个** 工具为 `async def`：`wait_for_screen_on`。 | 使用 **uiautomator2**（`u2.connect()`, `d.shell()`, `d.screen_on()` 等）和 **subprocess.run**（如 `subprocess.run([adb_path, "devices"], ...)`）。u2 与 subprocess 调用均为阻塞。 | **否**。`wait_for_screen_on` 虽为 async，其循环内仍直接调用阻塞的 `d.screen_on()`，再 `await asyncio.sleep(1)`，并未将设备 I/O 放入线程或异步子进程；其余工具全为 sync + 阻塞调用，无 to_thread。 |
| **CursorTouch/Android-MCP** | 全部 **同步** `def`（如 `list_devices_tool`, `connect_device_tool`, `click_tool`, `state_tool`, `wait_tool` 等）。 | **Mobile.list_devices()** 使用 `subprocess.run(['adb', 'devices'], ..., timeout=10)`；设备操作通过 **uiautomator2**（`u2.connect(serial)`, `device.click()`, `device.screenshot()` 等），均为阻塞。 | **否**。无任何 to_thread、run_in_executor 或异步子进程；工具在 FastMCP 中同步执行，长耗时调用会阻塞事件循环。 |
| **phone_pilot（本仓库）** | 设备相关工具为 **async def**，内部将整段阻塞逻辑放入 **asyncio.to_thread(...)** 执行。 | 部分工具（如 `phone_start_recording`, `phone_go_home`, `phone_force_stop`, `phone_list_devices` 等）在 **to_thread** 中调用 `_resolve_device_serial`、`get_driver()`、adb/驱动等；另有快速路径减少单次耗时。 | **是**。通过 **async 工具 + asyncio.to_thread** 避免阻塞事件循环；仍属“缩短单次耗时”，无法从协议上保证任意客户端超时下都不断开。 |

**源码依据摘要**：

- **minhalvp**：`server.py` 中所有工具均为 `def`，无 `async`；`adbdevicemanager.py` 使用 `AdbClient().device().shell()`、`subprocess.run`，无线程/异步封装。
- **nim444**：`tools/device_tools.py`、`screen_tools.py`、`advanced_tools.py` 等中工具为 `def`，内部直接 `subprocess.run`、`u2.connect()`、`d.info`、`d.screen_on()` 等；仅 `screen_tools.py` 中 `wait_for_screen_on` 为 `async def`，但循环内调用阻塞的 `d.screen_on()`。
- **CursorTouch**：`__main__.py` 中所有工具为 `def`；`mobile/service.py` 中 `list_devices` 为 `subprocess.run`，`connect`/`get_state` 等使用 `u2.connect()` 与 `device.screenshot()`，无异步或线程封装。

**Python MCP 的通用问题**（[python-sdk #1646](https://github.com/modelcontextprotocol/python-sdk/issues/1646) 等）：FastMCP 对 **同步** `@mcp.tool` **直接调用、不自动 to_thread**，因此 sync 工具内任何阻塞调用都会占满 asyncio 事件循环。经源码确认，**当前开源的 Python Android MCP 中，仅 phone_pilot 显式使用 asyncio.to_thread 做不阻塞处理**；其余均未做不阻塞处理，存在与“未改版前 phone_pilot”相同的阻塞/超时风险。

---

### 6.4 对照汇总

| 语言/运行时 | 常见不阻塞手段 | 典型开源 Android MCP（经源码确认） |
|-------------|----------------|-----------------------------------|
| **JavaScript/Node** | async 工具 + 异步子进程 / Promise 化 I/O | landicefu、isseikz 等为 async 工具 + 异步 I/O（前述文档描述）。 |
| **Kotlin/JVM** | 协程 + Dispatchers.IO / 挂起函数 | ulcica、InakiBes 等文档写明使用协程做非阻塞（前述文档描述）。 |
| **Python** | 若不处理：**sync 工具 + 阻塞调用 → 会阻塞事件循环**；若要做不阻塞：**async 工具 + asyncio.to_thread** 或 **立即返回 + 后台任务**。 | **minhalvp、nim444、CursorTouch**：源码中均为 sync 工具 + ppadb/u2/subprocess 阻塞调用，**未使用 to_thread 或 run_in_executor**。**phone_pilot**：async 工具 + **asyncio.to_thread**，目前唯一经源码确认做了不阻塞处理的 Python Android MCP。 |

若要**从机制上保证不超时**，Python 侧需要向「立即返回 + 后台执行」（方案 A）或「start + status 轮询」（方案 B）演进，与 Node/Kotlin 的“异步 I/O + 短响应”思路一致。
