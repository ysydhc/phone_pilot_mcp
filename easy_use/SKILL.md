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

---

## MCP 工具速查

### 推荐首选（LLM 友好）

| 工具 | 说明 |
|------|------|
| `phone_get_page_state` | 一次获取完整页面状态（Activity、元素列表、可滚动区域、全部文本）；设置 `annotate_elements=True` 可获取带编号标注的截图 |
| `phone_tap_element` | 通过元素编号点击（配合 `phone_get_page_state`），如 `phone_tap_element(index=3)` |
| `phone_tap_text` | 通过文本查找并点击（find+tap 合一），支持 `exact`/`contains` 匹配 |
| `phone_smart_find` | 智能查找：先 UIA、再 OCR、自动关弹窗重试 |

### 设备与截图

| 工具 | 说明 |
|------|------|
| `phone_list_devices` | 列出已连接设备 |
| `phone_screenshot` | 截取屏幕截图 |
| `phone_get_screen_size` | 获取屏幕尺寸 |
| `phone_get_device_info` | 获取设备信息（屏幕、Activity、型号、系统版本） |

### 输入操作

| 工具 | 说明 |
|------|------|
| `phone_tap` | 坐标点击 |
| `phone_tap_text` | 按文本查找并点击 |
| `phone_swipe` | 滑动（起点→终点坐标） |
| `phone_long_press` | 长按 |
| `phone_input_text` | 输入文本（`enter=True` 可附加回车） |
| `phone_keyevent` | 发送按键事件（KEYCODE_HOME / KEYCODE_BACK 等） |

### UI 查找

| 工具 | 说明 |
|------|------|
| `phone_find_element` | 通过 text/text_contains/desc/resource_id/class_name 查找 |
| `phone_find_image` | 通过图片模板匹配查找 |
| `phone_ocr_find` | 通过 OCR 识别文字查找 |
| `phone_scroll_to_find` | 滚动查找（支持 up_down/left_right 方向） |
| `phone_wait_for_element` | 等待元素出现（带超时） |
| `phone_dismiss_popup` | 检测并关闭弹窗 |

### 应用管理

| 工具 | 说明 |
|------|------|
| `phone_launch_app` | 启动应用 |
| `phone_force_stop` | 强制停止应用 |
| `phone_clear_data` | 清除应用数据 |
| `phone_clear_background` | 清理后台应用 |
| `phone_go_home` | 回到桌面 |
| `phone_go_back` | 返回上一页 |
| `phone_unlock` | 解锁设备 |
| `phone_open_deeplink` | 通过 deeplink/scheme 打开页面 |
| `phone_launch_from_home` | 从桌面搜索并启动应用 |
| `phone_install_app` / `phone_uninstall_app` | 安装/卸载应用 |
| `phone_list_packages` | 列出已安装应用 |

### 录屏

录屏采用异步模式（方案 B），流程：`start → poll status → 执行操作 → stop`

| 工具 | 说明 |
|------|------|
| `phone_start_recording` | 开始录屏（立即返回 `status="starting"`） |
| `phone_recording_status` | 轮询录屏状态（等待变为 `started`） |
| `phone_stop_recording` | 停止录屏并拉取到本地 |

### 日志（Logcat）

| 工具 | 说明 |
|------|------|
| `phone_start_logcat` | 开始后台捕获 logcat |
| `phone_stop_logcat` | 停止 logcat 捕获 |
| `phone_search_logcat` | 搜索 logcat 日志 |

### 内存分析

| 工具 | 说明 |
|------|------|
| `phone_memory_snapshot` | 采集应用内存快照 |
| `phone_memory_check_leak` | 检测 Activity 内存泄漏 |

### 验证与检查点

| 工具 | 说明 |
|------|------|
| `phone_verify` | 批量断言验证 |
| `phone_checkpoint_save` | 保存屏幕状态检查点 |
| `phone_checkpoint_diff` | 对比检查点差异 |
| `phone_compare_screenshot` | 截图相似度对比 |

### 资源缓存

| 工具 | 说明 |
|------|------|
| `phone_res_add` | 注册图片资源 |
| `phone_res_resolve` | 解析 `@res:key` 引用 |
| `phone_res_list` / `phone_res_get` / `phone_res_update` / `phone_res_delete` | 资源 CRUD |

### 文件与系统

| 工具 | 说明 |
|------|------|
| `phone_pull_file` / `phone_push_file` | 设备文件传输 |
| `phone_read_clipboard` | 读取剪贴板 |
| `phone_get_notifications` | 获取通知列表 |
| `phone_toggle_wifi` / `phone_toggle_airplane` | 网络控制 |
| `phone_execute_shell` | 执行 shell 命令（白名单限制） |

### 脚本执行

| 工具 | 说明 |
|------|------|
| `phone_run_script` | 执行 Python 自动化脚本（script_api 格式） |

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
- `easy_use/example_sdk_verify.py` — SDK 集成验证示例（广告 SDK 开屏→加载→展示完整流程）

---

## 关键约定

1. **设备自动检测**：单设备连接时无需指定 `device_serial`
2. **文本查找优先级**：精确匹配 → 包含匹配 → OCR
3. **`@res:` 引用**：用 `phone_res_add` 注册图片后，通过 `@res:key` 引用
4. **`re:` 正则**：`find_text(ctx, "re:^load$")` 使用正则表达式
5. **存储位置**：所有运行产物在 `.recordings/` 下
6. **返回值**：所有工具/函数返回 `dict`，含 `ok: bool` 字段
7. **错误处理**：`retry` 耗尽抛出 `RetryExhausted` 异常
