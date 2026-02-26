# phone_pilot MCP 使用规则

> 将此文件添加到 Cursor `.cursor/rules/` 或作为系统提示词使用。

## 核心原则

- phone_pilot 是一个多平台移动自动化 MCP 服务，当前支持 **Android** 和 **HarmonyOS**。
- 所有 MCP 工具使用 `phone_*` 前缀，参数含义见工具描述。
- 自动选择设备：若只连接一台设备，无需指定 `device_serial`，系统自动识别。

## MCP 工具速查

### 推荐首选
| 工具 | 说明 |
|------|------|
| `phone_get_page_state` | **一次获取完整页面状态**（Activity、元素列表、可滚动区域、全部文本），LLM 交互最优工具。设置 `annotate_elements=True` 可获取带元素编号标注的截图 |
| `phone_tap_element` | **通过元素编号点击**（配合 `phone_get_page_state` 使用），如 `phone_tap_element(index=3)` |

### 设备与截图
| 工具 | 说明 |
|------|------|
| `phone_list_devices` | 列出已连接设备 |
| `phone_screenshot` | 截取屏幕截图 |
| `phone_get_screen_size` | 获取屏幕尺寸 |

### 输入操作
| 工具 | 说明 |
|------|------|
| `phone_tap` | 坐标点击 |
| `phone_swipe` | 滑动 |
| `phone_long_press` | 长按 |
| `phone_input_text` | 输入文本 |
| `phone_keyevent` | 发送按键事件 |

### UI 查找
| 工具 | 说明 |
|------|------|
| `phone_find_element` | 通过文本/ID 查找 UI 元素 |
| `phone_find_image` | 通过图片模板匹配查找元素 |
| `phone_ocr_find` | 通过 OCR 识别文字查找元素 |

### 应用管理
| 工具 | 说明 |
|------|------|
| `phone_launch_app` | 启动应用 |
| `phone_force_stop` | 强制停止应用 |
| `phone_list_packages` | 列出已安装应用 |

### 资源缓存
| 工具 | 说明 |
|------|------|
| `phone_res_add` | 注册图片资源 |
| `phone_res_resolve` | 解析 `@res:key` 引用 |

### 验证管道
| 工具 | 说明 |
|------|------|
| `phone_run_script` | 执行 Python 自动化脚本 |
| `phone_verify` | 批量断言验证 |
| `phone_checkpoint_save` | 保存屏幕状态检查点 |
| `phone_checkpoint_diff` | 对比检查点差异 |

## 编写自动化脚本

当需要编写可复用的自动化脚本时，使用 `phone_pilot.script_api`：

```python
from phone_pilot.script_api import (
    ScriptContext, find_text, find_image, scroll_to_find,
    screenshot, retry, run_script, chain,
)

ctx = ScriptContext(
    device_serial="your_device",  # 可选，自动检测
    auto_screenshot=True,          # 每步自动截图
    auto_dump_hprof=False,         # 成功后自动 dump 内存
)

def main() -> int:
    chain(ctx).clear_background().restart_app_pkg("com.example").wait(3).done()
    find_text(ctx, "目标文本").tap(wait=2)
    return 0

if __name__ == "__main__":
    run_script(main, ctx=ctx)
```

## 关键约定

1. **文本查找优先级**：精确匹配 → 包含匹配 → OCR（若启用）
2. **`@res:` 引用**：使用 `phone_res_add` 注册图片后，通过 `@res:key` 引用，无需硬编码路径
3. **存储位置**：所有运行产物存储在 `.recordings/` 下，不会污染项目代码目录
4. **正则支持**：`find_text` 支持 `re:` 前缀使用正则表达式，如 `find_text(ctx, "re:^load$")`
5. **重试机制**：`retry()` 函数提供通用重试，失败抛出 `RetryExhausted` 异常
6. **链式 API**：`chain(ctx).clear_background().restart_app_pkg("com.example").wait(3).done()` 一行完成多步操作
