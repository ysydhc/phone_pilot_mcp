# phone_pilot MCP 集成技能

> 将此文件作为 AI Agent 的技能文件（Cursor Skills / Claude Skills）使用，
> 帮助 AI 快速理解 phone_pilot 的能力边界和最佳实践。

## 定位

phone_pilot 是面向客户端开发的移动自动化 MCP 服务，核心价值：
- **替代人工**：将重复性测试/验证操作自动化
- **AI 驱动**：AI Agent 根据自然语言需求生成可执行脚本
- **全链路记录**：每次运行自动保存截图、日志、录屏、内存快照

## 适用场景

- 客户端自动化测试（冒烟测试、回归测试）
- SDK 集成验证（广告 SDK、支付 SDK 等）
- UI 交互验证（点击、滑动、文本输入、页面跳转）
- 内存泄漏检测（Activity 泄漏、hprof dump）
- 多设备批量操作

## 两种使用方式

### 方式一：MCP 工具直接调用（适合简单任务）

```
# ⭐ 推荐：一次获取完整页面状态
state = phone_get_page_state()
# → 返回 Activity、带编号的元素列表、可滚动区域、全部文本

# ⭐ 含元素标注截图（LLM 看图更准确）
state = phone_get_page_state(annotate_elements=True)
# → 额外返回 screenshot_annotated_base64（截图上叠加编号+边框）

# ⭐ 通过编号点击元素（配合 page_state 使用）
phone_tap_element(index=3)
# → 自动定位元素 #3 中心坐标并点击

# 截图
phone_screenshot(device_serial="SERIAL")

# 查找并点击
result = phone_find_element(device_serial="SERIAL", text_contains="设置")
phone_tap(device_serial="SERIAL", x=result.x, y=result.y)

# 启动应用
phone_launch_app(device_serial="SERIAL", package="com.example.app")
```

### 方式二：Python 脚本（适合复杂流程）

当测试流程包含多步骤、条件判断、循环等逻辑时，生成 Python 脚本：

```python
from phone_pilot.script_api import (
    ScriptContext, find_text, find_image, scroll_to_find,
    screenshot, retry, run_script, chain,
    dump_hprof_snapshot,
)
from phone_pilot.memory_analyze import capture_meminfo, diff_meminfo

DEVICE_ID = "auto"  # 自动检测设备
PACKAGE = "com.example.app"

ctx = ScriptContext(
    device_serial=DEVICE_ID,
    auto_screenshot=True,
    auto_dump_hprof=False,
)

def main() -> int:
    # 1. 清理后台并启动应用
    chain(ctx).clear_background().restart_app_pkg(PACKAGE).wait(3).done()

    # 2. 查找元素并交互
    find_text(ctx, "登录").tap(wait=2)
    find_text(ctx, "re:\\d+\\.\\d+K").tap(wait=1)  # 正则匹配动态文本

    # 3. 滚动查找（支持多方向）
    elem = scroll_to_find(ctx, "目标文本", direction="up_down", max_count=5)
    elem.tap(wait=2)

    # 4. 日志与录屏
    ctx.start_logcat("test_log", tag="MyApp", level="D")
    ctx.start_record("test_recording")

    # ... 执行操作 ...

    ctx.stop_record()
    ctx.stop_logcat("test_log")

    # 5. 内存分析
    mem = capture_meminfo(DEVICE_ID, PACKAGE)
    print(f"Total PSS: {mem['summary']['total_pss_kb']}KB")

    # 6. 手动截图
    screenshot(ctx, "final_state")

    return 0

if __name__ == "__main__":
    run_script(main, ctx=ctx)
```

## script_api 核心函数速查

| 函数 | 说明 | 返回值 |
|------|------|--------|
| `find_text(ctx, text)` | 查找文本元素 | `UIElement` 或 `None` |
| `find_image(ctx, path)` | 查找图片元素 | `UIElement` 或 `None` |
| `scroll_to_find(ctx, text)` | 滚动查找 | `UIElement`（失败抛异常） |
| `screenshot(ctx, name)` | 截图保存 | `dict` |
| `screenshot_annotated(ctx, name)` | 截图+元素标注保存 | `dict` |
| `retry(fn, max_attempts)` | 通用重试 | 成功的返回值 |
| `chain(ctx)` | 链式操作 | `ChainResult` |
| `dump_hprof_snapshot(ctx, name)` | 内存 dump | `dict` |
| `restart_app_pkg(ctx, pkg)` | 重启应用 | `dict` |

## UIElement 常用方法

```python
elem = find_text(ctx, "确认")
elem.tap()                      # 点击
elem.tap(wait=3)                # 点击后等待 3 秒
elem.tap(times=2)               # 双击
val = elem.extract(r"(\d+)/5")  # 正则提取文本中的数字
text = elem.display_text        # 获取显示文本
info = elem.summary()           # 获取元素摘要信息
```

## 运行产物

每次通过 `run_script()` 执行脚本后，产物自动保存在：

```
.recordings/runs/{script}_{git}_{timestamp}/
├── script.py          # 脚本副本
├── console.log        # 控制台完整日志
├── steps.json         # 每步执行结果
├── run_meta.json      # 运行元信息
├── screenshots/       # 截图文件
├── recordings/        # 录屏文件
├── logcat/            # Logcat 日志
└── meminfo/           # 内存快照 + hprof
```

## 最佳实践

1. **先简后繁**：简单操作用 MCP 工具，复杂流程写脚本
2. **使用 `@res:` 引用**：避免硬编码图片路径，通过 `phone_res_add` 注册后使用 `@res:key`
3. **善用 chain**：多步连续操作用 `chain(ctx).step1().step2().done()` 简化代码
4. **录屏+日志**：关键步骤前 `start_record()` + `start_logcat()`，失败时有完整证据
5. **内存监控**：长时间运行的测试启用 `auto_dump_hprof=True`，自动检测内存泄漏
6. **正则匹配**：动态文本使用 `re:` 前缀，如 `find_text(ctx, "re:\\d+/5回")`
