# phone-pilot MCP 自动化技能

> 当用户需要对手机进行自动化操作、编写自动化测试脚本、或将手动操作转化为可复用脚本时，使用此技能。

## 定位

phone_pilot 是面向客户端开发的移动自动化 MCP 服务（Android / HarmonyOS），核心价值：
- **替代人工**：将重复性测试/验证操作自动化
- **AI 驱动**：AI Agent 根据自然语言需求，先用 MCP 工具交互式完成任务，再转化为可复用脚本
- **全链路记录**：每次运行自动保存截图、日志、录屏、内存快照

## 两种工作模式

### 模式一：MCP 工具直接操作（交互式）

Agent 通过 `phone_*` MCP 工具逐步操作手机，适合探索、调试、一次性任务。

### 模式二：生成自动化脚本（可复用）

在模式一完成操作后，Agent 将操作路径转化为 `phone_pilot.script_api` 格式的 Python 脚本，适合回归测试、CI/CD。

**典型工作流：**
1. 用户描述需求 → Agent 用 MCP 工具交互式操作手机
2. 操作成功 → Agent 分析操作路径，生成自动化脚本
3. 用户可通过 `phone_run_script` 或 `python script.py` 反复执行

> 提示：也可用 `phone_record_script_start` / `phone_record_script_stop`（script_record 类别）直接把一段操作会话录制成 script_api 脚本，再手动打磨（坐标点击转文本点击、加断言/重试）。

---

## MCP 工具速查

### 默认可用工具（核心层，15 个）

以下工具默认加载，覆盖 90% 常规操作场景。**优先使用这些工具。**

| 工具 | 说明 |
|------|------|
| `phone_get_page_state` | **推荐首选**。一次获取完整页面状态（Activity、元素列表、可滚动区域、全部文本）；设置 `annotate_elements=True` 可获取带编号标注的截图 |
| `phone_tap_element` | 通过元素编号点击（配合 `phone_get_page_state`），如 `phone_tap_element(index=3)` |
| `phone_tap_text` | 通过文本查找并点击（find+tap 合一），支持 `exact`/`contains` 匹配 |
| `phone_smart_find` | 智能查找：先 UIA、再 OCR、自动关弹窗重试 |
| `phone_tap` | 坐标点击 |
| `phone_swipe` | 滑动（起点→终点坐标） |
| `phone_input_text` | 输入文本（`enter=True` 可附加回车） |
| `phone_screenshot` | 截取屏幕截图 |
| `phone_list_devices` | 列出已连接设备 |
| `phone_launch_app` | 启动应用 |
| `phone_force_stop` | 强制停止应用 |
| `phone_go_back` | 返回上一页 |
| `phone_go_home` | 回到桌面 |
| `phone_run_script` | 执行 Python 自动化脚本（script_api 格式） |
| `phone_discover_tools` | **按需加载扩展工具**（见下方说明） |

### 扩展工具（按需加载）

当你需要录屏、日志、内存分析等高级功能时，调用 `phone_discover_tools` 加载对应类别：

```
# 1. 查看所有可用类别
phone_discover_tools()

# 2. 按类别加载（加载后即可正常使用）
phone_discover_tools(category="recording")   # 录屏/截图对比/检查点
phone_discover_tools(category="logcat")      # 日志捕获
phone_discover_tools(category="app")         # 应用管理（安装/卸载/清数据/解锁/deeplink）
phone_discover_tools(category="ui_find")     # UI 查找（find_element/OCR/滚动查找/等待）
phone_discover_tools(category="device")      # 设备详情（屏幕尺寸/设备画像）
phone_discover_tools(category="input")       # 高级输入（长按/按键事件）
phone_discover_tools(category="advanced")    # 高级功能（验证/内存/文件/剪贴板/网络/Shell/资源）
phone_discover_tools(category="script_record")  # 录制 MCP 操作生成脚本 / 回放 trace
phone_discover_tools(category="mock")        # 本地网络打桩 mock server

# 3. 按关键词搜索
phone_discover_tools(keyword="memory")       # 搜索并加载匹配的工具
```

### 扩展工具完整列表

以下工具需通过 `phone_discover_tools` 加载后使用：

#### device — 设备信息

| 工具 | 说明 |
|------|------|
| `phone_get_screen_size` | 获取屏幕尺寸 |
| `phone_get_device_info` | 获取设备信息（屏幕、Activity、型号、系统版本） |
| `phone_build_device_profile` | 构建完整设备画像 |
| `phone_export_device_info_md` | 导出设备信息为 Markdown |

#### input — 高级输入

| 工具 | 说明 |
|------|------|
| `phone_long_press` | 长按 |
| `phone_keyevent` | 发送按键事件（KEYCODE_HOME / KEYCODE_BACK 等） |

#### ui_find — UI 查找

| 工具 | 说明 |
|------|------|
| `phone_find_element` | 通过 text/text_contains/desc/resource_id/class_name 查找 |
| `phone_find_image` | 通过图片模板匹配查找 |
| `phone_ocr_find` | 通过 OCR 识别文字查找 |
| `phone_scroll_to_find` | 滚动查找（支持 up_down/left_right 方向） |
| `phone_wait_for_element` | 等待元素出现（带超时） |
| `phone_dismiss_popup` | 检测并关闭弹窗 |
| `phone_get_current_activity` | 获取当前 Activity |

#### app — 应用管理

| 工具 | 说明 |
|------|------|
| `phone_clear_data` | 清除应用数据 |
| `phone_clear_background` | 清理后台应用 |
| `phone_unlock` | 解锁设备 |
| `phone_open_deeplink` | 通过 deeplink/scheme 打开页面 |
| `phone_launch_from_home` | 从桌面搜索并启动应用 |
| `phone_install_app` / `phone_uninstall_app` | 安装/卸载应用 |
| `phone_list_packages` | 列出已安装应用 |

#### recording — 录屏与检查点

录屏采用异步模式，流程：`start → poll status → 执行操作 → stop`

| 工具 | 说明 |
|------|------|
| `phone_start_recording` | 开始录屏（立即返回 `status="starting"`） |
| `phone_recording_status` | 轮询录屏状态（等待变为 `started`） |
| `phone_stop_recording` | 停止录屏并拉取到本地 |
| `phone_compare_screenshot` | 截图相似度对比 |
| `phone_checkpoint_save` | 保存屏幕状态检查点 |
| `phone_checkpoint_diff` | 对比检查点差异 |

#### logcat — 日志捕获与断言

| 工具 | 说明 |
|------|------|
| `phone_start_logcat` | 开始后台捕获 logcat |
| `phone_stop_logcat` | 停止 logcat 捕获 |
| `phone_search_logcat` | 搜索 logcat 日志 |
| `phone_logcat_since` | 读取某个时间点之后的日志（`logcat -T`） |
| `phone_logcat_assert_sequence` | 验证多个模式按顺序出现（子序列匹配） |

#### advanced — 高级功能

| 工具 | 说明 |
|------|------|
| `phone_verify` | 批量断言验证 |
| `phone_memory_snapshot` | 采集应用内存快照 |
| `phone_memory_check_leak` | 检测 Activity 内存泄漏 |
| `phone_pull_file` / `phone_push_file` | 设备文件传输 |
| `phone_read_clipboard` | 读取剪贴板 |
| `phone_get_notifications` | 获取通知列表 |
| `phone_toggle_wifi` / `phone_toggle_airplane` | 网络控制 |
| `phone_execute_shell` | 执行 shell 命令（白名单限制） |
| `phone_res_add` / `phone_res_resolve` | 图片资源注册与解析 |
| `phone_res_list` / `phone_res_get` / `phone_res_update` / `phone_res_delete` | 资源 CRUD |
| `phone_recordings_list` / `phone_recordings_get` | 录屏文件管理 |
| `phone_replay_recording` | 回放录屏 |
| `phone_push_and_run_monkey` | Monkey 测试 |

#### script_record — 录制生成脚本

将一段 MCP 操作会话直接录制成可运行的 script_api 脚本，或回放已捕获的 trace。

| 工具 | 说明 |
|------|------|
| `phone_record_script_start` | 开始录制（此后每个白名单动作工具的调用被捕获） |
| `phone_record_script_status` | 查询录制状态与已捕获的动作数 |
| `phone_record_script_stop` | 停止录制，返回自动生成的 script_api 脚本源码 |
| `phone_replay_trace` | 回放已捕获的动作 trace |

> 注意：只捕获**动作类**工具（tap/swipe/input/launch/…），纯查询类（如 `phone_get_page_state`）不入录。

#### mock — 本地网络打桩

宿主机侧 HTTP mock server，用于给 App 的网络请求打桩（控制面/数据面分离，仅数据面经 `adb reverse` 暴露给设备）。

| 工具 | 说明 |
|------|------|
| `phone_mock_start` | 启动 mock server（自动建立 adb reverse 隧道） |
| `phone_mock_stop` | 停止 mock server 并拆除隧道 |
| `phone_mock_set_rule` | 添加/覆盖一条规则（method + path） |
| `phone_mock_delete_rule` | 删除一条规则 |
| `phone_mock_load_seed` | 批量加载规则集 |
| `phone_mock_get_rules` | 查看当前所有规则 |
| `phone_mock_get_requests` | 查看已收到的请求日志（含未命中的 404） |
| `phone_mock_status` | 查询 server 状态与已收请求数 |
| `phone_mock_assert_switched` | 断言 App 已把网络请求切到 mock（收到 ≥ N 个请求） |

> 典型用法：`start → set_rule → 触发 App 请求 → assert_switched → get_requests`。请求日志是判断 App 是否已切换的信号。

---

## MCP 操作 → 自动化脚本映射表

当 Agent 完成 MCP 操作后，按以下映射关系生成脚本代码：

| MCP 工具调用 | script_api 脚本代码 |
|-------------|-------------------|
| `phone_go_home()` | `chain(ctx).clear_background().done()` 或 `keyevent(ctx, "KEYCODE_HOME")` |
| `phone_clear_background()` | `clear_background(ctx)` |
| `phone_force_stop(package=P)` + `phone_launch_app(package=P)` | `restart_app_pkg(ctx, P)` 或 `chain(ctx).restart_app_pkg(P).done()` |
| `phone_launch_app(package=P)` | `restart_app_pkg(ctx, P)` |
| `phone_tap_text(text=T, match_mode="contains")` | `find_text(ctx, T).tap()` |
| `phone_tap_text(text=T, match_mode="exact")` | `find_text(ctx, T, exact=True).tap()` |
| `phone_tap(x=X, y=Y)` | `tap_xy(ctx, X, Y)` |
| `phone_tap_element(index=N)` | 先分析元素文本，转化为 `find_text(ctx, "元素文本").tap()` |
| `phone_find_element(text_contains=T)` | `find_text(ctx, T)` |
| `phone_swipe(x1,y1,x2,y2)` | `swipe(ctx, x1=x1, y1=y1, x2=x2, y2=y2)` |
| `phone_input_text(text=T)` | `type_text(ctx, T)` |
| `phone_input_text(text=T, enter=True)` | `type_text(ctx, T, enter=True)` |
| `phone_keyevent(keycode=K)` | `keyevent(ctx, K)` |
| `phone_screenshot()` | `screenshot(ctx, "step_name")` |
| `phone_scroll_to_find(text=T)` | `scroll_to_find(ctx, T)` |
| `phone_wait_for_element(text=T, timeout_s=N)` 重复轮询 | `retry(lambda: find_text(ctx, T, retry_attempts=1), max_attempts=N)` |
| `phone_start_recording()` ... `phone_stop_recording()` | `ctx.start_record("name")` ... `ctx.stop_record()` |
| `phone_start_logcat()` ... `phone_stop_logcat()` | `ctx.start_logcat("name", tag=T)` ... `ctx.stop_logcat("name")` |
| `phone_memory_snapshot(package=P)` | `mem_snapshot(ctx, package=P)` |
| `phone_memory_check_leak(package=P)` | `mem_check_leak(ctx, package=P)` |
| `phone_open_deeplink(uri=U)` | `keyevent(ctx, "KEYCODE_HOME")` 后 shell 调用，或用 `chain` |
| `phone_unlock()` | `unlock_device(ctx)` |
| `phone_wait_for_element(text=T, require_clickable=True)` + `phone_tap_*` | `tap_when_ready(ctx, text=T)`（等待+点击一体） |
| `phone_logcat_since(mark=M)` | `logcat_since(ctx, M)` |
| `phone_logcat_assert_sequence(patterns=[...])` | `logcat_assert_sequence(ctx, *patterns)` |
| `phone_mock_start()` | `mock_server_start(ctx)` |
| `phone_mock_set_rule(...)` | `mock_set_rule(ctx, method, path, body)` |
| `phone_mock_assert_switched()` | `mock_assert_switched(ctx)` |
| 等待 N 秒 | `chain(ctx).wait(N).done()` 或 `import time; time.sleep(N)` |

---

## 生成脚本的规则

### 脚本模板

```python
from phone_pilot.script_api import (
    ScriptContext, find_text, find_image, scroll_to_find,
    screenshot, retry, run_script, chain,
    dump_hprof_snapshot,
)

PACKAGE = "com.example.app"

ctx = ScriptContext(
    auto_screenshot=True,
    auto_report=True,
)

def main() -> int:
    # 1. 初始化：清理后台 + 启动应用
    chain(ctx).clear_background().restart_app_pkg(PACKAGE).wait(3).done()

    # 2. 操作步骤...
    find_text(ctx, "目标文本").tap(wait=2)

    # 3. 截图记录
    screenshot(ctx, "final")

    return 0

if __name__ == "__main__":
    run_script(main, ctx=ctx)
```

### 脚本编写原则

1. **用 `find_text` 代替坐标点击**：MCP 操作中的 `phone_tap(x, y)` 应尽量转化为 `find_text(ctx, "按钮文本").tap()`，提高脚本在不同设备/分辨率上的通用性
2. **用 `chain` 简化连续操作**：多步连续操作写成 `chain(ctx).step1().step2().wait(N).done()`
3. **用 `retry` 处理不稳定步骤**：网络请求、动态加载等不确定场景用 `retry(lambda: ..., max_attempts=N, interval=2.0)`
4. **用正则匹配动态文本**：`find_text(ctx, "re:\\d+\\.\\d+K")` 匹配动态数字
5. **避免硬编码坐标**：只有无文本/无 ID 的纯图形元素才用 `tap_xy(ctx, x, y)`
6. **关键步骤加截图**：`screenshot(ctx, "step_name")` 留下证据
7. **录屏包裹核心流程**：`ctx.start_record("name")` ... `ctx.stop_record()`
8. **返回值约定**：`main()` 返回 `0` 表示成功，非 `0` 表示失败
9. **用健壮交互替代固定 sleep**：时序不稳/易误触的步骤用 `tap_when_ready(ctx, text=...)`（等到可点再点）、`safe_tap(...)`（误触自动回退）、`safe_close(...)`（完整关闭流程），少用裸 `time.sleep` + `tap_xy`
10. **多用例用 `run_cases` 编排**：区分被测缺陷（fail）与环境问题（`EnvError`，可重试），环境不满足抛 `SkipCase`

### 从 MCP 操作转化为脚本的步骤

Agent 完成 MCP 操作后，按以下步骤生成脚本：

1. **回顾操作序列**：列出所有 MCP 工具调用及其参数
2. **合并初始化步骤**：`go_home` + `force_stop` + `launch_app` → `chain(ctx).clear_background().restart_app_pkg(PACKAGE).wait(3).done()`
3. **坐标点击转文本点击**：`phone_tap(x, y)` 或 `phone_tap_element(index=N)` → 查看当时的元素文本 → `find_text(ctx, "文本").tap()`
4. **等待逻辑转 retry**：MCP 中多次轮询某个元素出现 → `retry(lambda: find_text(ctx, "目标"), max_attempts=N, interval=2.0)`
5. **条件判断保留**：如"请求成功则继续，失败则重试" → `if/else` + `retry`
6. **添加录屏和截图**：在脚本首尾添加 `start_record`/`stop_record`，关键步骤加 `screenshot`

---

## script_api 完整能力参考

### ScriptContext 配置

```python
ctx = ScriptContext(
    device_serial=None,         # 设备序列号，空则自动检测
    platform="auto",            # "android" / "harmony" / "auto"
    auto_screenshot=True,       # 每步自动截图
    auto_dump_hprof=False,      # 成功结束后自动 dump 内存
    auto_meminfo=True,          # 自动 before/after 内存快照
    auto_report=True,           # 结束后自动生成 HTML 报告
    popup_guard=True,           # 弹窗自动检测与关闭
    llm_healing=False,          # LLM 分析兜底（需 OPENAI_API_KEY）
    max_heal_attempts=3,        # 单步最大自愈尝试次数
    on_step_fail="abort",       # 失败策略："abort" / "retry" / "screenshot_and_continue"
)
```

### ScriptContext 方法

| 方法 | 说明 |
|------|------|
| `ctx.start_record(name)` | 开始录屏 |
| `ctx.stop_record()` | 停止录屏并保存 |
| `ctx.start_logcat(name, tag=, level=)` | 开始 logcat 捕获 |
| `ctx.stop_logcat(name)` | 停止 logcat |
| `ctx.driver` | 获取底层设备驱动 |

### 查找函数

| 函数 | 说明 |
|------|------|
| `find_text(ctx, text)` | 查找文本元素，支持 `re:` 前缀正则。返回 `UIElement` 或 `None` |
| `find_text(ctx, text, exact=True)` | 精确匹配文本 |
| `find_text(ctx, text, retry_attempts=5, retry_interval_ms=1000)` | 带重试的查找 |
| `find_image(ctx, path)` | 查找图片元素。返回 `UIElement` 或 `None` |
| `find_text_list(ctx, text)` | 查找所有匹配文本。返回 `UIElementList` |
| `find_image_list(ctx, path)` | 查找所有匹配图片。返回 `UIElementList` |
| `scroll_to_find(ctx, text, direction="up_down", max_count=10)` | 滚动查找 |

### UIElement 方法

```python
elem = find_text(ctx, "确认")
elem.tap()                       # 点击
elem.tap(wait=3)                 # 点击后等待 3 秒
elem.tap(times=2)                # 双击
elem.display_text                # 获取显示文本
elem.extract(r"(\d+)/5")        # 正则提取
elem.summary()                   # 简短描述
elem.exists()                    # 是否仍在页面上
elem.wait(timeout_s=10)          # 等待元素出现
elem.refresh()                   # 重新定位
elem.right(text="值")            # 右侧查找
elem.left() / elem.up() / elem.down()  # 相对方向查找
elem.screenshot_crop(name="xx")  # 裁剪元素区域截图
```

### 操作函数

| 函数 | 说明 |
|------|------|
| `tap_xy(ctx, x, y)` | 坐标点击 |
| `type_text(ctx, text, enter=False)` | 输入文本 |
| `keyevent(ctx, keycode)` | 发送按键 |
| `swipe_up(ctx)` / `swipe_down(ctx)` / `swipe_left(ctx)` / `swipe_right(ctx)` | 方向滑动 |
| `swipe(ctx, x1=, y1=, x2=, y2=)` | 自定义滑动 |
| `screenshot(ctx, name)` | 截图保存 |
| `screenshot_annotated(ctx, name)` | 截图+元素标注 |

### 健壮交互（推荐，解决时序与误触）

固定 `sleep` 时序不稳、元素刚出现就点导致误触、误点广告跳出 app 等问题，用以下函数替代裸 `tap`：

| 函数 | 说明 |
|------|------|
| `tap_when_ready(ctx, text=/resource_id=, timeout_s=10, require_clickable=True)` | 轮询等到元素**可点击**再点；点击坐标与匹配来自同一次查询（无漂移）；超时**不点击** |
| `safe_tap(ctx, x, y, expect_activity=/expect_package=)` | 点击后校验前台状态；若跳出 app 或页面不符 → 自动 `go_back` 并复验恢复 |
| `safe_close(ctx, resource_id=/text=, fallback_coord=, max_attempts=3)` | 完整关闭流程：等按钮就绪 → 点击 → 验证消失 → 失败回退重试；可回退到固定坐标 |

### 等待条件

| 函数 | 说明 |
|------|------|
| `wait_for_element_appear(ctx, text=/resource_id=, timeout_s=, require_clickable=, require_enabled=)` | 等待元素出现（可要求可交互态） |
| `wait_for_element_disappear(ctx, ...)` | 等待元素消失 |
| `wait_for_activity(ctx, activity, timeout_s=)` | 等待到达目标 Activity（已在则立即成功） |
| `wait_for_activity_change(ctx, from_activity=None, to_activity=None)` | 确认发生了**真实跳转**（基线+离开），不同于 `wait_for_activity` |
| `wait_for_page_stable(ctx, timeout_s=)` | 等待页面稳定（连续多帧无变化） |

### UIAutomator 逃生通道（不常用）

无文本/无 ID 的极端场景兜底，日常应优先用 `find_text`：

| 函数 | 说明 |
|------|------|
| `dump_ui(ctx)` | dump 当前 UI 树 |
| `raw_uia(ctx, selector, by=)` | 直接用 UIAutomator selector 定位 |

### App 控制

| 函数 | 说明 |
|------|------|
| `clear_background(ctx)` | 清理后台 |
| `restart_app_pkg(ctx, package)` | 强停+重启应用 |
| `reset_home_screen(ctx)` | 回到桌面 |
| `launch_from_home(ctx, query)` | 从桌面搜索启动 |
| `unlock_device(ctx)` | 解锁设备 |

### 内存分析

| 函数 | 说明 |
|------|------|
| `mem_snapshot(ctx, name, package=)` | 内存快照 |
| `mem_diff(ctx, before_name, after_name)` | 对比两次快照 |
| `mem_check_leak(ctx, package=)` | 泄漏检测 |
| `dump_hprof_snapshot(ctx, name, package=)` | hprof dump |

### 日志分析

| 函数 | 说明 |
|------|------|
| `logcat_find(ctx, pattern, regex=False)` | 搜索 logcat |
| `logcat_wait_for(ctx, *patterns, timeout_s=30)` | 等待日志出现 |
| `logcat_mark(ctx, tag=)` | 记录设备当前时间点（配合 `logcat_since`） |
| `logcat_since(ctx, mark, tag_filter=)` | 读取某时间点之后的日志（`logcat -T`） |
| `logcat_assert_sequence(ctx, *patterns, timeout_s=)` | 验证多模式按顺序出现（子序列匹配） |
| `assert_event(ctx, event, ...)` | 断言某业务事件日志出现 |

### 广播命令总线（Android）

给被测 App 发 `am broadcast` 触发内部命令，并可等待 ACK 日志闭环；含注入加固（action/extras 白名单校验）。

| 函数 | 说明 |
|------|------|
| `send_broadcast(ctx, action, extras=, package=, wait_ack=, ack_timeout_s=8, ack_tag=)` | 发广播；`wait_ack` 非空时发送前打标、发送后区间轮询 ACK 子串。返回 `{ok, ack_seen, line, reason, cmd}` |
| `ensure_app_alive(ctx, package, scheme_url=, ready_event=, timeout_s=15, force_restart=False)` | 确保进程存活：`pidof`→`ps` 两级探测，必要时 scheme 冷启动并等就绪日志。返回 `{ok, pid, launched, ready_seen, reason}` |

> Android 8+ 会静默丢弃隐式广播，务必传 `package=` 或 `component=` 做定向。

### 用例编排框架

`setup → act → check → teardown` 的用例基类，`run_cases` 批量执行；区分「用例失败(fail)」与「环境失败(env_error，可重试)」。

```python
from phone_pilot.script_api import TestCase, run_cases, EnvError, SkipCase

class LoginCase(TestCase):
    name = "登录流程"
    def setup(self, ctx):
        # 环境准备；不满足抛 EnvError（触发清场重试）或 SkipCase（跳过）
        if not find_text(ctx, "登录入口"):
            raise EnvError("app_not_ready")
    def act(self, ctx):
        find_text(ctx, "登录").tap(wait=2)
    def check(self, ctx):
        # 返回 None/True → pass；False/含 ok=False 的 dict → fail
        return find_text(ctx, "首页") is not None
    def teardown(self, ctx):
        restart_app_pkg(ctx, PACKAGE)  # setup 必须能自足重建环境

result = run_cases(ctx, [LoginCase()], retry_env_errors=1, max_total_s=300)
# result = {"ok", "total", "pass", "fail", "env_error", "error", "skip", "results": [...]}
```

| 组件 | 说明 |
|------|------|
| `TestCase` | 用例基类，覆写 `setup/act/check/teardown`，`setup_retries` 控制 setup 重试次数 |
| `EnvError` | 环境级失败异常（可重试），从 setup/act 抛出触发清场重试 |
| `SkipCase` | 前置条件缺失，跳过用例 |
| `run_cases(ctx, cases, retry_env_errors=1, max_total_s=None)` | 批量执行；env_error 整例重跑，超预算的剩余用例记 skip |

### 网络 Mock（宿主机侧打桩）

给 App 网络请求打桩：`mock_server_start` 启动 server 并建立 `adb reverse` 隧道，`run_script` 结束时自动拆除。

```python
mock_server_start(ctx)                                    # 启动 + reverse 隧道
mock_set_rule(ctx, "GET", "/api/config", {"enabled": True})  # 写规则
# ... 触发 App 请求 ...
mock_assert_switched(ctx, min_hits=1, timeout_s=10)       # 断言 App 已切到 mock
```

| 函数 | 说明 |
|------|------|
| `mock_server_start(ctx, data_port=0, ctrl_port=0, seed=None)` | 启动 mock server + adb reverse |
| `mock_server_stop(ctx)` | 停止并拆隧道（幂等，run_script 自动调用） |
| `mock_set_rule(ctx, method, path, body, status=200, headers=, delay_ms=0)` | 添加/覆盖规则 |
| `mock_switch_rule(ctx, method, path, body, ...)` | 切换规则（先删旧再设新，常用于"无数据→有数据") |
| `mock_delete_rule(ctx, rule_id)` | 删除规则（`rule_id` 形如 `GET:/path`) |
| `mock_load_seed(ctx, seed)` | 批量加载规则集 |
| `mock_assert_switched(ctx, min_hits=1, timeout_s=10)` | 断言 App 已把请求切到 mock；超时返回诊断提示 |

> 仅支持明文 HTTP（TLS 被绕过），App debug 包需允许对 `127.0.0.1` 的 cleartext 流量。

### 执行器

| 函数 | 说明 |
|------|------|
| `retry(fn, max_attempts=3, interval=2.0, ctx=None)` | 通用重试，传入 `ctx` 启用自愈 |
| `run_script(main, ctx=ctx)` | 运行脚本入口（自动记录产物、生成报告） |

### chain 链式 API

```python
chain(ctx) \
    .clear_background() \
    .restart_app_pkg("com.example") \
    .wait(3) \
    .find_text("登录") \
    .tap() \
    .wait(2) \
    .find_text("re:\\d+条") \
    .tap() \
    .screenshot_save("after_tap") \
    .assert_text_exists("成功") \
    .done()
```

chain 支持的方法：
- 导航：`clear_background()`, `restart_app_pkg(pkg)`, `unlock_device(pin=)`, `wait(seconds)`
- 查找：`find_text(text)`, `find_image(path)`, `right()`, `left()`, `up()`, `down()`
- 操作：`tap()`, `swipe_up()`, `swipe_down()`, `swipe_left()`, `swipe_right()`
- 变量：`save(name)`, `get(name)`, `require(name)`, `export(name)`, `import_(name, payload)`
- 断言：`assert_text_exists(text)`, `assert_text_not_exists(text)`, `assert_image_exists(path)`, `assert_image_not_exists(path)`, `assert_logcat_contains(pattern)`, `assert_logcat_not_contains(pattern)`, `assert_memory_no_growth(package, threshold_mb=50)`
- 记录：`screenshot_save(name)`, `checkpoint(name)`
- 循环：`repeat(times, block=lambda c: c.find_text(...).tap())`
- 终止：`done()` → 返回 `{ok, error, vars, last}`

---

## 运行产物

通过 `run_script()` 执行脚本后，产物自动保存：

```
.recordings/runs/{script}_{git}_{timestamp}/
├── script.py          # 脚本副本
├── console.log        # 控制台完整日志
├── steps.json         # 每步执行结果
├── run_meta.json      # 运行元信息
├── report.html        # 可视化报告（auto_report=True）
├── screenshots/       # 截图文件
├── recordings/        # 录屏文件
├── logcat/            # Logcat 日志
└── meminfo/           # 内存快照 + hprof
```

---

## 示例脚本

完整示例见同目录下的独立文件，Agent 需要参考时读取：

- `easy_use/example_script.py` — 基础示例（清理启动、查找点击、滚动、录屏、日志、自愈）

---

## 关键约定

1. **设备自动检测**：单设备连接时无需指定 `device_serial`
2. **文本查找优先级**：精确匹配 → 包含匹配 → OCR
3. **`@res:` 引用**：用 `phone_res_add` 注册图片后，通过 `@res:key` 引用
4. **`re:` 正则**：`find_text(ctx, "re:^load$")` 使用正则表达式
5. **存储位置**：所有运行产物在 `.recordings/` 下
6. **返回值**：所有工具/函数返回 `dict`，含 `ok: bool` 字段
7. **错误处理**：`retry` 耗尽抛出 `RetryExhausted` 异常
