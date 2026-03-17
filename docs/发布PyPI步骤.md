# 发布 phone-pilot 到 PyPI

## 当前状态

- **当前版本**：0.5.9（见 `pyproject.toml`）。
- **构建**：执行 `uv build` 后 `dist/` 下会生成对应版本的 wheel 与 tar.gz。
- **发布**：需在本地配置 PyPI 凭证后执行 `uv publish`（见下方「发布前准备」）。

## 发布前准备

1. **PyPI 账号**：在 [pypi.org](https://pypi.org) 注册并登录。
2. **API Token**：PyPI 网站 → Account settings → API tokens → Add API token，生成一个 token（发布权限），复制保存。
3. **凭证二选一**：
   - **推荐**：环境变量  
     ```bash
     export UV_PUBLISH_TOKEN=pypi-xxxxxxxx
     ```
   - 或使用 [keyring](https://pypi.org/project/keyring/) 配置后，`uv publish` 会自动读取。

## 发布命令

在项目根目录执行：

```bash
# 使用 uv（推荐）
uv publish

# 或使用 twine（需先 pip install twine）
twine upload dist/phone_pilot-0.5.9*
# 按提示输入用户名 __token__ 和密码（即 API token）
```

## 外部如何更新

发布成功后，**外部用户**可以这样升级并使用：

### 1. 升级安装

```bash
# 使用 pip
pip install -U phone-pilot

# 或使用 uv
uv pip install -U phone-pilot
```

若通过 **uvx** 直接跑 MCP（不先 pip 安装），每次会拉取 PyPI 最新版，一般无需单独升级；若要固定到新版本可显式指定：

```bash
uvx phone-pilot-mcp@0.5.9
```

### 2. 环境变量（推荐）

为避免「首次成功、后续 Not connected」，建议在启动 MCP 的环境里设置 ADB 相关变量之一（或保证 `adb` 在 PATH 中）：

- `ADB_PATH`：adb 可执行文件完整路径；或  
- `ANDROID_HOME`：Android SDK 根目录（我们会用 `$ANDROID_HOME/platform-tools/adb`）。

未设置时，0.5.2 会尝试从常见路径自动发现并写入环境，多数场景可工作；若仍报 Not connected，请按 [MCP工具不可用排查与修复](MCP工具不可用排查与修复.md) 检查。

### 3. 使新版本生效

- 若 Cursor/IDE 通过 MCP 连接 phone_pilot：**重载 MCP** 或 **重启客户端** 后，会使用新安装的版本。
- 若用 `phone-pilot-mcp` 命令行：重新执行一次即可。

## 版本与重新发布

- 当前版本：`0.5.9`（见 `pyproject.toml`）。
- 若需发布新版本：修改 `pyproject.toml` 中 `version`，重新执行 `uv build` 和 `uv publish`。
- PyPI 不允许同一版本号重复上传，改版本后需重新 build 再 publish。
