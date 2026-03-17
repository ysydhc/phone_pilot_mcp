# MCP 工具大量不可用 — 排查与修复

## 现象

- Cursor 里 **phone_pilot** MCP 显示为 **errored**（设置 → MCP 中该服务器报错）。
- 调用时提示 **Tool ... was not found**，或整批 `phone_*` 工具不可用。
- `mcps/user-phone_pilot/` 下只有 `SERVER_METADATA.json` 和 `STATUS.md`，**没有 `tools/` 目录**（说明 Cursor 从未成功连上该 MCP 服务器，因此没有拉取到工具列表）。

## 根本原因

**MCP 服务器没有成功启动**，所以 Cursor 拿不到工具列表，所有 `phone_*` 工具都不可用。  
常见原因是 Cursor 用 **uvx** 或错误命令启动进程，导致进程起不来或立即退出。

### 1. 使用 `uvx phone-pilot-mcp` 或 `uvx phone-pilot` 会失败

- 本项目的 **PyPI 包名** 是 **`phone-pilot`**，入口脚本是 **`phone-pilot-mcp`**。
- `uvx phone-pilot-mcp` 会让 uv 去拉取名为 **`phone-pilot-mcp`** 的包，该包在 PyPI 上**不存在**，会报错：
  ```text
  No solution found when resolving tool dependencies:
  Because phone-pilot-mcp was not found in the package registry ...
  ```
- `uvx phone-pilot` 会找 **`phone-pilot`** 包；若该包未发布到 PyPI 或当前环境解析不到，同样会报错，服务器无法启动。

因此：**只要 Cursor 的 MCP 配置里用的是 `uvx phone-pilot-mcp` 或 `uvx phone-pilot`，且依赖公共 PyPI，当前就会导致 MCP 服务器启动失败 → 工具全部不可用。**

### 2. 其他可能原因

- **命令或路径错误**：例如 `phone-pilot-mcp` 未加入 PATH，或 Cursor 使用的 Python 环境里未安装 `phone-pilot`。
- **工作目录/项目路径错误**：用 `uv run --project <path>` 时，若 `<path>` 不是本仓库根目录，可能依赖解析失败或跑错包。
- **启动超时或崩溃**：服务器进程启动时依赖缺失（如 ADB、opencv）或 import 报错，进程退出，Cursor 会认为该 MCP 出错。

## 修复方式

让 Cursor 用**本机已可用的方式**启动 MCP 服务器，而不要依赖 `uvx` 拉取不存在的包。

### 方式 A：用本地项目 + uv（推荐）

在 Cursor 的 MCP 配置（如 **Cursor Settings → MCP**，或项目/用户目录下的 `mcp.json`）中，将 phone_pilot 配置为用 **uv run** 从本仓库启动：

```json
{
  "mcpServers": {
    "phone_pilot": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/Users/yeshouyou/Work/agent/phone_touch",
        "phone-pilot-mcp"
      ],
      "cwd": "/Users/yeshouyou/Work/agent/phone_touch",
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

注意：

- 把 **`/Users/yeshouyou/Work/agent/phone_touch`** 换成你机器上本仓库的**实际绝对路径**。
- 若 Cursor 的 MCP 配置不支持 `cwd`，可省略；优先保证 `args` 里的 `--project` 路径正确。

这样 Cursor 会直接用当前仓库的 `pyproject.toml` 和虚拟环境跑 `phone-pilot-mcp`，不依赖 PyPI 上是否有 `phone-pilot-mcp` 包。

### 方式 B：用已安装的 `phone-pilot-mcp` 命令

若你已在**当前环境**执行过 `pip install phone-pilot`（或 `pip install -e .`），且 Cursor 使用的 Python/终端能解析到 `phone-pilot-mcp`，可直接用命令名：

```json
{
  "mcpServers": {
    "phone_pilot": {
      "command": "phone-pilot-mcp",
      "args": [],
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

请确认：

- 在 Cursor 使用的终端里执行 `which phone-pilot-mcp` 能输出路径。
- 若 Cursor 固定用某个 Python，用该 Python 的 `-m pip install phone-pilot` 安装后再试。

### 方式 C：用 Python 模块直接跑

若本仓库已在 Python 路径中（例如在仓库根目录用 IDE 打开），可用模块方式：

```json
{
  "mcpServers": {
    "phone_pilot": {
      "command": "python",
      "args": ["-m", "phone_pilot.cli"],
      "cwd": "/Users/yeshouyou/Work/agent/phone_touch",
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    }
  }
}
```

同样把 `cwd` 和（如有）`command` 中的 `python` 换成你实际使用的解释器路径（如 `/path/to/python3`）。

## 外部接入时「首次成功、后续 Not connected」的修复

### 现象

在**外部客户端**（非 Cursor 或未传 env）启动 MCP 时：`phone_get_device_info`、`phone_start_recording` 等第一次成功，后续 `phone_go_home`、`phone_force_stop` 等报 **Not connected**（对应底层返回 `no_device_connected` / `device_not_found`）。

### 原因

- 外部进程启动 MCP 时若**未传入** `ADB_PATH`、`ANDROID_HOME`、`PATH`、`HOME`，则首次解析设备时可能用到 `adb_executable()` 的 fallback（如 `expanduser("~")` 或 `which("adb")`），并缓存路径。
- 若进程环境不一致（例如不同请求在不同子进程、或 PATH 被裁剪），后续调用可能拿到不同的 adb 或空设备列表，导致 `_resolve_device_serial` 返回「未发现设备」。

### 代码侧修复（已实现）

- 在 **`phone_pilot.android.adb.utils`** 中新增 **`ensure_adb_env()`**：在未设置 `ADB_PATH`/`ANDROID_HOME` 时，从常见路径发现 adb 并写入 `os.environ`（含 `PATH` 前置 platform-tools），并清空 adb 路径缓存，保证同进程内后续调用一致。
- **`_resolve_device_serial`** 与 **`phone_list_devices`** 在入口处均调用 **`ensure_adb_env()`**，因此无论先调用哪个工具，都会先统一 env，再执行 adb，避免「首调成功、后续 Not connected」。

### 外部接入建议

- 若启动 MCP 时能传 env，建议仍设置：`ADB_PATH`、`ANDROID_HOME`、`PATH`（含 platform-tools）、`HOME`，与 Cursor 的 mcp.json 中 `phone_pilot.env` 一致，兼容性最好。
- 即使不传，服务端也会在首次设备相关调用时自动发现并设置 env，多数单机部署可恢复正常。

## 外部调用时 Connection closed / Client closed

### 现象

使用 **PyPI 包**在外部客户端连接 MCP 时，日志出现：

- `connected -> error: Client closed`
- `Error calling tool 'phone_go_home': MCP error -32000: Connection closed`
- 后续再调工具或 `listOfferingsForUI` 报 **Not connected**

即：先连接成功，随后连接被关闭，之后所有调用都报「Not connected」（实为客户端已无 MCP 连接）。

### 原因

- MCP 服务器是 **async**，但部分工具（如 `phone_go_home`、`phone_force_stop`）内部直接执行**同步阻塞**的 adb/设备调用（`_resolve_device_serial`、`get_driver()`、`driver.go_home()` 等）。
- 这些调用会**阻塞 asyncio 事件循环**，导致服务器在工具执行期间无法及时响应客户端（如心跳、其他请求）。客户端或传输层**超时**后主动断开 → 表现为「Client closed」/「Connection closed」。
- 断开后客户端处于未连接状态，后续任何调用（含 listOfferingsForUI）都会报 **Not connected**。

### 代码侧修复（已实现）

- 在 **`phone_pilot.mcp.server`** 中，将以下工具的同步设备逻辑放入 **`asyncio.to_thread()`** 中执行，事件循环不再被阻塞，客户端不易因超时断开：
  - `phone_go_home`、`phone_force_stop`
  - **`phone_start_recording`、`phone_stop_recording`**（录屏与后续步骤连续调用时最易阻塞，已一并修复）
- 若你使用的版本仍出现 Connection closed 或「获取设备信息成功、开始录屏起 Not connected」，请升级到 **0.5.4 及以上**。

### 外部接入建议

- **升级**：`pip install -U phone-pilot` 或使用带上述修复的版本。
- **环境变量**：启动 MCP 时尽量传入 `ADB_PATH` 或 `ANDROID_HOME`，减少 adb 发现耗时，进一步降低首包延迟。
- **客户端超时**：若客户端可配置 MCP 请求/读超时，可适当调大，避免偶发慢设备下仍被误判超时。

## 验证是否修复

1. 保存上述配置后，**完全重启 Cursor**（或重载 MCP 配置，视 Cursor 版本而定）。
2. 打开 **Cursor Settings → MCP**，查看 **phone_pilot** 是否仍报错；若显示为已连接/正常，则说明服务器已启动。
3. 在对话中尝试调用任意 `phone_*` 工具（如 `phone_list_devices`）；若不再报 “Tool not found”，说明工具已可用。
4. 可选：查看 `mcps/user-phone_pilot/` 下是否出现 **tools/** 目录及工具描述文件，若有则说明 Cursor 已成功拉取工具列表。

## 小结

| 现象 | 原因 | 修复要点 |
|------|------|----------|
| MCP 工具大量不可用 / 全部找不到 | MCP 服务器未成功启动 | 让 Cursor 用**本地项目**或**已安装命令**启动，不要用 `uvx phone-pilot-mcp`（或依赖未发布包的 uvx） |
| 配置里用了 uvx | PyPI 无 `phone-pilot-mcp` 包（或 `phone-pilot` 不可用） | 改为 `uv run --project <本仓库路径> phone-pilot-mcp` 或 `phone-pilot-mcp` / `python -m phone_pilot.cli` |
| 无 tools/ 目录 | Cursor 从未与该 MCP 建立连接 | 服务器启动成功后，Cursor 会拉取工具列表并生成 tools/ |
| 外部调用时 Connection closed / Client closed，随后 Not connected；或「获取设备信息成功、开始录屏起 Not connected」 | 工具内同步 adb/录屏 调用阻塞事件循环，客户端超时断开 | 升级到 0.5.4+（含录屏等 to_thread 修复）；必要时设 ADB_PATH/ANDROID_HOME、调大客户端超时 |

按上述方式修正 MCP 启动配置并重启 Cursor 后，phone_pilot 的 MCP 工具应可正常使用。
