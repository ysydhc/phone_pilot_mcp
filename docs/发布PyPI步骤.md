# 发布 phone-pilot 到 PyPI

## 当前状态

- **本地已提交**：变更已 commit（`chore: 提交 MCP 文档与配置，准备发布`）。
- **构建已完成**：`dist/` 下已有 `phone_pilot-0.5.1-py3-none-any.whl` 和 `phone_pilot-0.5.1.tar.gz`。
- **发布未完成**：需在本地配置 PyPI 凭证后执行一次发布命令。

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
twine upload dist/phone_pilot-0.5.1*
# 按提示输入用户名 __token__ 和密码（即 API token）
```

发布成功后，外部可通过以下方式安装并使用 MCP：

```bash
pip install phone-pilot
phone-pilot-mcp
```

或：

```bash
uvx phone-pilot-mcp
# 注：uvx 会拉取 PyPI 上的 phone-pilot 包并执行其入口 phone-pilot-mcp
```

## 版本与重新发布

- 当前版本：`0.5.1`（见 `pyproject.toml`）。
- 若需发布新版本：修改 `pyproject.toml` 中 `version`，重新执行 `uv build` 和 `uv publish`。
- PyPI 不允许同一版本号重复上传，改版本后需重新 build 再 publish。
