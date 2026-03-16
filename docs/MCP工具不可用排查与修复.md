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

按上述方式修正 MCP 启动配置并重启 Cursor 后，phone_pilot 的 MCP 工具应可正常使用。
