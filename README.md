# phone_pilot - Mobile Automation via MCP

通过 MCP (Model Context Protocol) 让 AI Agent 控制 Android 手机。支持 Cursor、Claude Desktop 等 MCP 客户端。

## 一行安装

```bash
# 从 PyPI 安装（推荐）
pip install phone-pilot

# 直接运行 MCP 服务器（无需安装，需要 uv）
uvx phone-pilot-mcp
```

## 安装选项

```bash
# 核心安装（仅 Android 支持）
pip install phone-pilot

# 含 HarmonyOS 支持
pip install "phone-pilot[harmony]"

# 含语言检测与翻译
pip install "phone-pilot[vision]"

# 全部安装
pip install "phone-pilot[all]"

# 开发模式（本地源码安装）
pip install -e .
```

## 架构概述

```
phone_pilot/
├── core/                    # 核心协调层
│   ├── protocols.py         # Driver 接口定义
│   ├── skills.py            # 统一技能 API
│   ├── storage.py           # 存储路径管理（.recordings/）
│   ├── run_store.py         # RunSession 运行记录管理
│   ├── resource.py          # @res: 资源缓存管理
│   ├── verify.py            # 验证引擎（多维度断言 + 证据收集）
│   ├── checkpoint.py        # 屏幕状态检查点（保存/对比）
│   └── ui/
│       ├── chain.py         # UIChain 流式 API
│       ├── graph.py         # UIGraph DAG 拓扑引擎
│       └── element.py       # UIElement 数据模型
├── extensions/              # 平台无关的扩展
│   ├── vision/              # 图像处理（模板匹配）
│   ├── ocr/                 # 文字识别（Tesseract）
│   ├── lang/                # 语言检测与翻译（可选）
│   ├── diff/                # 截图对比
│   └── llm/                 # LLM 全局页面分析（自愈兜底，可选）
├── android/                 # Android 平台实现（ADB + UIAutomator）
├── harmony/                 # HarmonyOS 平台实现（hmdriver2 + HDC，功能暂不完善，待开发）
├── ios/                     # iOS 平台（计划中）
├── memory_analyze/          # 内存分析（meminfo / hprof / Activity 泄漏检测）
├── mcp/                     # MCP Server（phone_* 统一工具）
├── script_api/              # Python 脚本编写唯一入口（模块包）
└── FEATURE_MATRIX.md        # 三平台功能对照表
```

## 快速开始

### MCP 服务器启动

```bash
# 启动 MCP 服务器（stdio 传输）
phone-pilot-mcp

# 命令行调用工具
phone-pilot-call --list-tools
phone-pilot-call phone_list_devices
phone-pilot-call phone_screenshot --args '{"device_serial":"<serial>"}'
```

### MCP 工具一览

工具使用 `phone_*` 前缀，支持多平台：

```python
# ⭐ 推荐首选：一次获取完整页面状态（LLM 最优）
phone_get_page_state(device_serial)
# → 返回: Activity、元素列表(带编号)、可滚动区域、全部文本

# ⭐ 含元素标注的截图（编号+边框叠加在截图上，LLM 看图更准确）
phone_get_page_state(device_serial, annotate_elements=True)
# → 额外返回 screenshot_annotated_base64

# 通过元素编号直接点击（配合 phone_get_page_state 使用）
phone_tap_element(index=3)
# → 自动定位元素 #3 的中心坐标并点击

# 基础操作
phone_tap(device_serial, x, y)
phone_swipe(device_serial, x1, y1, x2, y2)
phone_screenshot(device_serial)

# UI 查找
phone_find_element(device_serial, text_contains="设置")

# 图像/OCR
phone_find_image(device_serial, template_path="@res:<key>")  # key 由 phone_res_add 添加时得到
phone_ocr_find(device_serial, query="登录", lang="chi_sim")

# 应用管理
phone_launch_app(device_serial, package="com.example.app")
phone_force_stop(device_serial, package="com.example.app")

# 验证管道
phone_run_script(path="workflow/navigate.py")
phone_verify(device_serial, assertions='[...]')
phone_checkpoint_save(device_serial, name="before")
phone_checkpoint_diff(device_serial, name="before")

# 导航与设备控制
phone_go_home(device_serial)
phone_go_back(device_serial)
phone_unlock(device_serial, pin="1234")
phone_clear_background(device_serial)
phone_clear_data(device_serial, package="com.example.app")
phone_open_deeplink(device_serial, uri="myapp://page/detail")

# 录屏与日志
phone_start_recording(device_serial, name="test")
phone_stop_recording(device_serial)  # 自动拉取到本地
phone_start_logcat(device_serial, tags="MyTag", level="D")
phone_stop_logcat(device_serial)
phone_search_logcat(device_serial, pattern="Error", regex=True)

# 智能查找与等待
phone_scroll_to_find(device_serial, text="目标文本", direction="up_down")
phone_wait_for_element(device_serial, text="加载完成", timeout_s=15)
phone_dismiss_popup(device_serial)
phone_smart_find(device_serial, text="目标", use_ocr=True)

# 应用管理（扩展）
phone_install_app(device_serial, apk_path="/path/to/app.apk")
phone_uninstall_app(device_serial, package="com.example.app")
phone_launch_from_home(device_serial, query="微信")

# 内存分析
phone_memory_snapshot(device_serial, package="com.example.app")
phone_memory_check_leak(device_serial, package="com.example.app")

# 文件操作
phone_pull_file(device_serial, remote_path="/sdcard/log.txt")
phone_push_file(device_serial, local_path="test.txt", remote_path="/sdcard/test.txt")

# 高级功能
phone_read_clipboard(device_serial)
phone_get_notifications(device_serial)
phone_toggle_wifi(device_serial, enabled=False)
phone_execute_shell(device_serial, command="dumpsys battery")
phone_get_device_info(device_serial)
```

`phone_get_page_state` 是 LLM 交互的核心工具：一次调用获取当前页面的 Activity、所有可交互元素（带编号和坐标）、可滚动区域和全部文本。设置 `annotate_elements=True` 会在截图上叠加元素编号和边框标注，LLM 看图后可直接用 `phone_tap_element(index=3)` 点击目标元素，无需多次往返查询。

### Python 脚本方案（script_api）

面向非程序员的简洁 API，通过 `script_api.py` 编写自动化脚本：

```python
from phone_pilot.script_api import (
    ScriptContext, find_text, find_image, scroll_to_find,
    screenshot, retry, run_script, chain,
)

ctx = ScriptContext(
    device_serial="your_device",
    auto_screenshot=True,
    auto_dump_hprof=False,  # 脚本成功结束后自动 dump 内存快照
    popup_guard=True,       # 启用弹窗自动检测与关闭（默认开启）
    llm_healing=False,      # 启用 LLM 全局分析兜底（需 OPENAI_API_KEY）
    max_heal_attempts=3,    # 单步最大自愈尝试次数
)

def main() -> int:
    # 清理后台并启动应用
    chain(ctx).clear_background().restart_app_pkg("com.example").wait(5).done()

    # 查找并点击文本
    find_text(ctx, "任务中心").tap(wait=3)

    # 滚动查找（支持双向搜索）
    elem = scroll_to_find(ctx, "目标文本", direction="up_down", max_count=5)
    elem.tap(wait=2)

    # 日志监控
    ctx.start_logcat("verify", tag="MyTag", level="D")
    # ... 执行操作 ...
    ctx.stop_logcat("verify")

    # 录屏
    ctx.start_record("demo")
    # ... 执行操作 ...
    ctx.stop_record()

    # 手动截图
    screenshot(ctx, "final_state")

    return 0

if __name__ == "__main__":
    run_script(main, ctx=ctx)
```

## 存储结构

所有运行时产物统一存储在 `.recordings/` 目录下（通过 `.gitignore` 排除）：

```
.recordings/
├── cache/                   # 图片缓存、SQLite DB
├── devices/                 # 设备信息
├── hprof/                   # 非脚本环境的 hprof dump
├── verify/                  # 验证报告
└── runs/                    # RunSession 脚本运行产物
    └── {script}_{git}_{ts}/
        ├── script.py        # 脚本副本
        ├── git_info.json    # Git 信息（commit, branch, dirty）
        ├── git_diff.patch   # 未提交变更
        ├── console.log      # 完整控制台日志
        ├── steps.json       # 所有步骤结果汇总
        ├── run_meta.json    # 运行元信息
        ├── report.html      # HTML 可视化报告（自动生成）
        ├── screenshots/     # 步骤截图 + 手动截图
        ├── recordings/      # 录屏文件
        ├── logcat/          # Logcat 日志文件
        ├── meminfo/         # 内存快照与泄漏分析
        └── healing_events.json  # 自愈事件记录
```

## HarmonyOS 支持

需要额外安装：`pip install phone-pilot[harmony]`

通过 [hmdriver2](https://github.com/codematrixer/hmdriver2) 库实现完整 UI 自动化：

- 输入操作：tap/swipe/long_press/double_click/input_text/keyevent
- 屏幕操作：screenshot、get_screen_size、screenrecord
- UI 树：dump_hierarchy、find_elements
- 应用管理：launch/stop/clear/install/uninstall
- 设备管理：unlock/screen_on/screen_off、device_info

详见 `phone_pilot/FEATURE_MATRIX.md` 获取完整三平台功能对照。

## 错误自愈机制

脚本执行过程中遇到元素查找失败等错误时，自动尝试修复：

1. **经验库查询** — 查找之前同一页面同一操作的成功纠正记录，零成本复用
2. **PopupGuard 弹窗检测** — Window 层级 + UI 树结构 + 文本规则三层检测，自动关闭弹窗
3. **LLM 全局分析（可选）** — 截图 + UI 树 + 错误上下文综合诊断，给出修复建议

```python
# 启用自愈（默认 popup_guard=True）
ctx = ScriptContext(
    popup_guard=True,         # 弹窗自动检测（默认开启）
    llm_healing=True,         # LLM 兜底分析（需 OPENAI_API_KEY）
    max_heal_attempts=3,      # 单步最大自愈次数
)

# retry() 也支持自愈
result = retry(lambda: find_text(ctx, "目标"), desc="查找目标", ctx=ctx)
```

纠正经验按 `action + query + app_package + activity` 维度存储，跨脚本共享。
LLM 修复成功后自动写入经验库，下次遇到相同问题可直接复用。

## 在 Cursor / Claude Desktop 中注册 MCP

### 方式一：uvx（推荐，无需安装）

```json
{
  "mcpServers": {
    "phone_pilot": {
      "command": "uvx",
      "args": ["phone-pilot-mcp"]
    }
  }
}
```

### 方式二：pip 安装后直接使用

```json
{
  "mcpServers": {
    "phone_pilot": {
      "command": "phone-pilot-mcp",
      "args": []
    }
  }
}
```

### 方式三：开发模式（本地源码）

```json
{
  "mcpServers": {
    "phone_pilot": {
      "command": "uv",
      "args": ["run", "--project", "<project_path>", "phone-pilot-mcp"],
      "cwd": "<project_path>"
    }
  }
}
```

详细配置示例见 `cursor_mcp_config.example.json`。

## 资源缓存与 @res 引用

```bash
# 添加资源（图片等，path 为本地文件路径）
phone-pilot-call phone_res_add --args '{"path":"/path/to/your/template.png"}'

# 通过 @res: 引用（在脚本或 MCP 中均可使用，key 为添加时自动生成的键名）
phone-pilot-call phone_find_image --args '{"device_serial":"<serial>","template_path":"@res:<key>"}'
```

资源缓存存储在 `.recordings/cache/pic/` 目录下。

## 本地 Web Dashboard

提供一个本地网页查看运行记录和 `@res` 图片资源，支持浏览、删除和生成 HTML 报告。

### 启动方式

```bash
# CLI 命令（推荐，安装后可直接使用）
phone-pilot-web

# 模块运行
python -m phone_pilot.web

# 指定端口
phone-pilot-web --port 9090

# 不自动打开浏览器
phone-pilot-web --no-browser
```

启动后浏览器自动打开 `http://127.0.0.1:8686`，即可查看：

- **运行记录** — 列出所有脚本运行历史，显示状态、耗时、设备、Git 信息；可一键生成/查看 HTML 可视化报告
- **图片资源** — 显示所有 `@res:` 缓存的图片，支持复制引用路径、预览缩略图、删除

> 无额外依赖，仅使用 Python 标准库 `http.server` + 项目已有的 `jinja2`。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PHONE_PILOT_CACHE_DIR` | 缓存根目录（设备信息、图片缓存等），设置后使用 `<值>/.recordings/` | 项目根目录 |
| `PHONE_PILOT_RECORDINGS_DIR` | RunSession 运行记录根目录 | `<cwd>/.recordings/` |
| `PHONE_PILOT_LOG_LEVEL` | 日志级别 (quiet/normal/verbose) | `normal` |
| `PHONE_PILOT_CMD_DELAY_S` | ADB/HDC 命令间延迟秒数 | `0.4` |
| `PHONE_PILOT_DISABLE_TRANSLATION` | 禁用翻译功能 | 未设置 |
| `OPENAI_API_KEY` | OpenAI API Key（LLM 自愈分析用） | 未设置 |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | LLM 模型名称 | `gpt-4o-mini` |

## 前置条件

- Python >= 3.13
- `adb` 已安装且设备已连接授权
- HarmonyOS 支持需要 `hdc` 命令行工具

### 可选依赖

| 依赖 | 用途 | 安装方式 |
|------|------|----------|
| OpenCV (`cv2`) | 图像模板匹配 (`phone_find_image`)、截图对比 (`phone_compare_screenshot`) | `pip install opencv-python` |
| Tesseract + pytesseract | OCR 文字识别 (`phone_ocr_find`) | 系统安装 `tesseract-ocr`（macOS: `brew install tesseract`；Ubuntu: `apt install tesseract-ocr`），再 `pip install pytesseract` |
| 中文语言包 | OCR 中文识别 | macOS: `brew install tesseract-lang`；Ubuntu: `apt install tesseract-ocr-chi-sim` |

> 未安装可选依赖时，相关 MCP 工具会返回错误提示而非崩溃。可通过 `python scripts/check_verification_env.py` 检查当前环境。

### MCP 工具验证（A 类真机，本地可选）

`docs/` 与部分验证用脚本已移出 Git 管理；若需在本地跑 A 类真机验证：

1. **环境自检**：`python scripts/check_verification_env.py`（通过后再跑验证）
2. **资源清单**：在本地维护 `docs/verification/verification_resources.json`（若保留 `docs/`），配置 `device_serial`、`apk_path`、`package`、`resource_file`、`resource_key` 等
3. **A 类验证脚本**：若本地保留 `scripts/verify_mcp_tools_a.py`，可运行并加 `--yes` 跳过确认、`--write-checklist` 写回检查表

### 示例脚本（script_api）

- **基础示例**：`easy_use/example_script.py` — 清理启动、查找点击、滚动、录屏、日志、自愈等，使用 **phone_pilot.script_api**，安装后可直接运行：`python easy_use/example_script.py`
- **演示脚本**：抖音观看 5 视频等演示位于 `scripts/`（已移出 Git 管理，若本地保留可运行，如 `scripts/douyin_watch_5_videos.py`，需设置 `DOUYIN_PACKAGE` 等环境变量）

## 开发

```bash
# 安装开发依赖（含 lint/test）
pip install -e ".[dev]"
# 或完整可选依赖
pip install -e ".[all]"

# 使用 Makefile（推荐）
make lint   # ruff check
make test   # pytest
make check  # lint + test
```

---
**版本**: 0.5.9
**许可**: MIT
