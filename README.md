## Android 触摸录制 → Monkey `.mks`（Cursor MCP）

这个 workspace 里提供了一个 MCP stdio server：`../mcp_android.py`，可以：

- 后台录制 `adb shell getevent -lt` 的触摸日志
- Stop 时把 raw log 转成可被 `adb shell monkey -f <script>` 执行的 `.mks`
- 支持录制命名/查询（`recordings/index.jsonl`）

### 前置条件

- 本机已安装并可用 `adb`
- Android 设备已连接并授权调试（`adb devices` 能看到设备）
- 建议用 `uv` 运行（本目录已包含 `pyproject.toml` + `uv.lock`）
- 建议设置默认命令延迟，避免 App 启动/页面切换期间注入事件丢失：`PHONE_TOUCH_CMD_DELAY_S=0.4`（单位秒，可按需调大）

### 本地手动验证（推荐先做一次）

在 repo 根目录执行也可以，但推荐明确指定 project（更稳）：

```bash
uv run --project /Users/yeshouyou/Work/agent/phone_touch /Users/yeshouyou/Work/agent/phone_touch/mcp_android.py
```

### 打包成可复用能力（给其它项目使用）

你可以把本仓库当做一个可安装的 Python 包（distribution 名称：`phone-touch`）。

- **本机以“可编辑模式”安装（开发/调试最方便）**：

```bash
python -m pip install -e /Users/yeshouyou/Work/agent/phone_touch
```

- **安装后启动 MCP server（stdio）**：

```bash
phone-touch-mcp
```

- **安装后在命令行调用 tool（stdio client）**：

```bash
phone-touch-call --list-tools
phone-touch-call android_list_devices
phone-touch-call android_record_start --args '{"name":"输入法_搜索","device_serial":"<serial>"}'
```

### 在 Cursor 里注册 MCP（stdio）

在 Cursor 的 MCP 配置里新增一个 server（下面示例用**绝对路径**，最稳）：

```json
{
  "mcpServers": {
    "android_touch": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/Users/yeshouyou/Work/agent/phone_touch",
        "/Users/yeshouyou/Work/agent/phone_touch/mcp_android.py"
      ]
    }
  }
}
```

如果你想用相对路径，请确保 Cursor 启动 MCP 时的 working directory 能正确解析（不同环境可能不一致）。

你也可以在“已安装”后，直接用可执行入口（无需写死 repo 路径）：

```json
{
  "mcpServers": {
    "android_touch": {
      "command": "phone-touch-mcp",
      "args": []
    }
  }
}
```

### 可用工具（Tools）

- `android_list_devices()`
- `android_list_launchable_apps(device_serial?, query?, limit?)`（用于录制前选择要操作的 App；不选则默认不强制回桌面，需显式传 reset_home=true 才会回桌面）
- `android_wake_and_unlock(device_serial?, wakeup?, swipe?, swipe_times?, pin?, swipe_duration_ms?, swipe_wait_s?, settle_s?)`（best-effort 解锁：**仅支持 PIN**；不支持“仅滑动解锁/图案解锁/指纹/人脸”；swipe=true 仅用于唤起 PIN 键盘）
- `android_lock_screen(device_serial?, settle_s?)`（锁屏/熄屏 best-effort：优先 KEYCODE_SLEEP，必要时 fallback KEYCODE_POWER）
- `android_clear_background_processes(device_serial?, mode?, exclude_packages?, max_packages?, run_kill_all_first?)`（清后台：kill-all 或逐个 force-stop 三方应用）
- `android_ui_dump(device_serial?, out_dir?, name?, compressed?)`（导出当前页面的控件树 XML，用于“找文字/找按钮”）
- `android_find_on_screen(device_serial, query, exact?, case_sensitive?, limit?, out_dir?, name?, debug_annotate?)`（基于控件树查找文字/desc，返回坐标）
- `android_element_query(device_serial, selector, attributes?, case_sensitive?, limit?)`（高级元素查询，支持丰富选择器：text/desc/resource_id/class_name/clickable/enabled 等）
- `android_element_exists(device_serial, query?, template_path?, selector?, exact?, ocr_fallback?)`（检查元素是否存在，支持 UIAutomator/OCR/图片三级回退）
- `android_wait_for(device_serial, condition, query?, template_path?, activity?, timeout_s?, interval_s?)`（等待条件满足：element_appear/element_disappear/activity/page_stable）
- `android_assert(device_serial, assertion_type, query?, template_path?, expected?, activity?, logcat_pattern?, baseline_path?, ...)`（执行断言：element_exists/element_not_exists/text_equals/text_contains/activity/logcat/screenshot_match）
- `android_screenshot_baseline(device_serial, name, out_dir?, mask_regions?)`（保存当前截图作为基准，用于视觉回归测试）
- `android_screenshot_compare(device_serial, baseline_path, similarity_threshold?, mask_regions?)`（比较当前截图与基准，返回相似度）
- `android_tap(device_serial, x, y, wait_s?)`（点击坐标）
- `android_find_and_tap(device_serial, query?, template_path?, exact?, match_index?, ocr_fallback?, ocr_lang?, threshold?, ...)`（**增强版查找点击，三级回退：UIAutomator → OCR → 图片**）
- `android_find_text_and_tap(device_serial, query, exact?, match_index?, ocr_fallback?, ocr_lang?, ...)`（纯文字查找点击，两级回退：UIAutomator → OCR）
- `android_find_image_on_screen(device_serial, template_path, threshold?, grayscale?, roi?, scales?, method?, max_results?, out_dir?, name?, debug_annotate?)`（先特征匹配再模板匹配）
- `android_find_multi_stage(device_serial, stages, out_dir?, name?, tap_final?, tap_wait_s?, debug_annotate?)`（多级找图/找字，单次截图级联匹配）
- `android_find_image_and_tap(device_serial, template_path, threshold?, ..., match_index?, pre_screenshot?, post_screenshot?, out_dir?, name?, debug_annotate?)`（纯图片匹配点击）
- `android_ocr_find_on_screen(device_serial, query, out_dir?, name?, lang?, psm?, exact?, case_sensitive?, limit?, debug_annotate?)`（纯 OCR 找字，返回坐标）
- `android_ocr_find_and_tap(device_serial, query, out_dir?, name?, lang?, psm?, exact?, match_index?, pre_screenshot?, post_screenshot?, debug_annotate?)`（纯 OCR 找字并点击）
- `android_screenshot(device_serial?, out_dir?, name?, include_base64?)`（截图保存为 PNG；可选返回 base64）
- `android_logcat_clear(device_serial?)`（清空 logcat buffer）
  - `android_launch_from_home(device_serial, query, out_dir?, ...)`（从桌面找图标并启动；启动后记录 package/activity/component，生成可复用 adb am start 命令）
  - `android_launch_profiles_list(out_dir?, query?, limit?)`（查询已记录的启动 Activity/component）
  - `android_launch_using_profile(device_serial, out_dir?, query?, package?, wait_s?)`（使用已记录的 component 直接启动 App，不依赖桌面图标位置）
- `android_logcat_dump(device_serial?, out_dir?, name?, lines?, filter_spec?)`（保存 logcat 到本地 txt）
- `android_collect_artifacts(device_serial?, out_dir?, name?, logcat_lines?, logcat_filter_spec?, include_focus?, include_screenshot?)`（一键保存：截图 + focus + logcat）
- `android_run_workflow(device_serial?, out_dir?, name?, steps?, clear_logcat_first?, final_collect_artifacts?, logcat_lines?, logcat_filter_spec?)`（高阶：按 steps 串联执行，并保存 run.jsonl/meta.json/截图/日志）
- `android_parse_workflow_doc(doc_text?, doc_path?)`（把“测试任务文档”解析为 workflow/steps）
- `android_run_workflow_from_doc(doc_text?, doc_path?, device_serial?, out_dir?, name?, ...)`（从文档解析并直接执行）
- `android_device_capture(device_serial?, out_dir?, keep_device?)`（单独采集并存储设备信息：名称/系统版本/屏幕等）
- `android_devices_list(out_dir?, query?, limit?)`（列出已采集的设备）
- `android_device_get(device_serial, out_dir?, force_update?, include_system?)`（读取某个设备的详情；device_serial 默认就是 serial；force_update=true 强制更新；include_system=false 仅三方应用）
- `android_record_start(device_serial?, out_dir?, name?, tags?, restart_app?, app_package?, app_component?, app_deeplink?, app_force_stop?, app_wait_s?, reset_home?, home_presses?, keep_device?, slot?, wait_device?)`
- `android_record_stop(recording_id, convert?, mks_name?)`
- `android_convert_to_mks(raw_log_path, mks_out_path?, keep_device?, slot?)`
- `android_push_and_run_monkey(device_serial, local_mks_path, remote_script?, package?, restore_start_from?, out_dir?, force_device_mismatch?, auto_scale_by_screen?, reset_home?, home_presses?, pre_inject_wait_s?, throttle_ms?, seed?, count?)`
- `android_replay_recording(key, device_serial?, out_dir?, remote_script?, package?, pre_inject_wait_s?, force_device_mismatch?, auto_scale_by_screen?, throttle_ms?, seed?, count?)`
- `android_recordings_list(out_dir?, query?, device_serial?, limit?)`
- `android_recordings_get(key, out_dir?)`
- `android_recordings_rename(recording_id, new_name, out_dir?)`

### 多级找图/找字（共享截图）

可在同一张截图内做多级查找（图 → 图/字），避免重复截图开销：

```python
from android_tool.multi_finder import MultiStageFinder

finder = MultiStageFinder(device_serial="SERIAL")
first = finder.find_image(template_path="icon.png")
roi = finder.box_to_roi(first["matches"][0], padding=4)
second = finder.find_text(query="登录", roi=roi)
```

### Cursor 对话示例

- “列出当前 adb 设备”
- “先解锁手机（PIN 仅支持纯数字）：android_wake_and_unlock(device_serial='XXX', pin='1234')”
- “锁屏一下”：android_lock_screen(device_serial='XXX')
- “我要开始录制，请先列出可操作的 App（我不选就从桌面开始）”
- “开始录制我对手机的操作，设备 serial 是 `XXX`，命名为 `输入法_设置`，tags 是 `['ime','settings']`（开始前先回到桌面首页）”
- “开始录制我对手机的操作，命名为 `容器_主题详情`，录制前重启 App：restart_app=true，app_package 是 `com.example.app`”
- “停止录制并生成 mks”
- “回放某条录制时，先恢复到录制起点：android_push_and_run_monkey(..., restore_start_from='容器_主题详情')”
- “一键回放（自动恢复起点 + push + monkey）：android_replay_recording('容器_主题详情')”
- “列出我最近的录制”
- “查找名字包含 `输入法` 的录制”
- “获取 recording_id 为 `abcd1234efgh` 的录制详情”
- “把 `abcd1234efgh` 这条录制重命名为 `输入法_切换语言`”
- “跑一个工作流：回到桌面 → 重启 App → 截图 → 保存 logcat（产物落在 recordings/workflows 下）”
- “我把测试任务写在一个 Markdown 文档里（含 ```json ... ```），你用 android_run_workflow_from_doc 解析并执行”

#### Workflow steps 支持 if/else（条件分支）

从 `0.1.1` 起，`android_run_workflow` 的 `steps` 支持：

- **set_vars / set_var**：设置工作流变量（供条件表达式使用）
- **if**：基于表达式执行 `then` / `elif` / `else` 分支（分支里仍然是 `steps` 数组）
- **when**：条件成立才执行 `do`（否则 no-op）
- **assert**：断言表达式为真（可选 `fatal=true` 触发 fail-fast）
- **set_options**：设置工作流选项（目前支持 `fail_fast`）
  - **点击后重试（推荐）**：对 `find_and_tap` / `find_image_and_tap` / `tap` 等“点击类 step”，可以增加 `verify` 条件：
    - 若 `verify` 未满足，会等待 `retry_delay_s` 秒后重试，最多 `retry_max_attempts` 次（默认 3 次）
    - `verify` 支持：`find_on_screen` / `find_image_on_screen` / `assert`
    - `retry_max_attempts` / `retry_delay_s` 可写在 step 里，也可用 `set_options` 设为全局默认
  - **多级找图/找字 + 点击**：`find_multi_stage_and_tap` 支持 `stages`，按顺序在同一张截图中查找图/字并点击最终匹配
  - **调试标注**：`debug_annotate=true` 时会把匹配框画到原截图并落盘到 `recordings`

表达式示例（安全子集，不支持函数调用）：

- `vars.need_login == True`
- `last is None or last.ok == True`
- `last.count > 0 and not vars.skip`

表达式额外支持少量安全函数（无 kwargs）：

- `len(x)`
- `get(d, "k", default)`
- `has(d, "k")`
- `contains(a, b)`（等价于 `b in a`，但更易写）

示例：

```json
{
  "name": "example_if_else",
  "steps": [
    { "type": "set_options", "values": { "fail_fast": true } },
    { "type": "set_vars", "values": { "need_login": true } },
    { "type": "find_and_tap", "query": "个人中心" },
    {
      "type": "if",
      "expr": "vars.need_login == True",
      "then": [
        { "type": "find_and_tap", "query": "登录" }
      ],
      "elif": [
        { "expr": "last is not None and last.ok == False", "then": [ { "type": "screenshot", "name": "previous_step_failed" } ] }
      ],
      "else": [
        { "type": "screenshot", "name": "already_logged_in" }
      ]
    },
    {
      "type": "when",
      "expr": "has(vars, \"need_login\") and vars.need_login == True",
      "do": [
        { "type": "assert", "expr": "last is not None", "message": "expect last result exists", "fatal": false }
      ]
    }
  ]
}
```

#### 测试验证工具（Testing Verification Tools）

从 `0.1.7` 起，新增完整的测试验证能力：

**元素查询增强**：
- `android_element_query`：支持丰富选择器（text/desc/resource_id/class_name/clickable/enabled 等）
- `android_element_exists`：快速检查元素是否存在（不点击），支持三级回退

**等待条件**（`android_wait_for`）：
```json
// 等待元素出现
{ "type": "wait_for", "condition": "element_appear", "query": "登录成功", "timeout_s": 15 }

// 等待元素消失（如加载指示器）
{ "type": "wait_for", "condition": "element_disappear", "query": "加载中..." }

// 等待进入特定 Activity
{ "type": "wait_for", "condition": "activity", "activity": ".MainActivity" }

// 等待页面稳定（UI 树不再变化）
{ "type": "wait_for", "condition": "page_stable", "stable_count": 3 }
```

**断言机制**（`android_assert`）：
```json
// 断言元素存在
{ "type": "assert_element_exists", "query": "登录按钮", "fatal": true }

// 断言元素不存在
{ "type": "assert_element_not_exists", "query": "错误提示" }

// 断言文本内容
{ "type": "text_equals", "query": "欢迎", "expected": "欢迎回来" }
{ "type": "text_contains", "query": "状态", "expected": "成功" }

// 断言当前 Activity
{ "type": "assert_activity", "activity": ".MainActivity" }

// 断言日志内容
{ "type": "logcat", "logcat_pattern": "登录成功" }
```

**截图对比断言**（Visual Regression Testing）：
```json
// 保存基准截图
{ "type": "save_baseline", "name": "login_screen" }

// 断言截图与基准匹配
{ "type": "assert_screenshot_match", "baseline_path": "recordings/baselines/login_screen.png", "similarity_threshold": 0.95 }
```

**工作流 verify 新增类型**：
```json
{
  "type": "find_and_tap",
  "query": "提交",
  "verify": [
    { "type": "element_not_exists", "query": "加载中" },
    { "type": "activity", "activity": ".SuccessActivity" },
    { "type": "screenshot_match", "baseline_path": "recordings/baselines/success.png" }
  ]
}
```

---

#### find_and_tap 增强版（智能回退）

从最新版本起，`find_and_tap` 支持智能回退查找，**OCR 和图片匹配优先于滑动**：

1. **UIAutomator**（主要）：通过 UI 节点查找 text/content_desc
2. **OCR**（回退1）：UIAutomator 失败时，立即尝试 OCR 识别（适用于键盘等 IME 元素）
3. **图片匹配**（回退2）：如果提供了 `template_path`，尝试 OpenCV 模板匹配
4. **滑动查找**（回退3）：以上方法都失败且 `scroll_on_fail=true` 时，才开始滑动屏幕重试

**步骤类型对比：**

| 步骤类型 | UIAutomator | OCR | 图片 | 用途 |
|----------|:-----------:|:---:|:----:|------|
| `find_and_tap` | ✅ | ✅ | ✅ | 通用查找（最强） |
| `find_text_and_tap` | ✅ | ✅ | ❌ | 纯文字查找 |
| `find_image_and_tap` | ❌ | ❌ | ✅ | 纯图片查找 |
| `ocr_find_and_tap` | ❌ | ✅ | ❌ | 纯 OCR 查找 |

**使用示例：**

```json
// 查找文字（自动 UIAutomator → OCR 回退）
{ "type": "find_and_tap", "query": "设置" }

// 查找键盘上的文字（UIAutomator 无法访问，自动使用 OCR）
{ "type": "find_and_tap", "query": "GIF" }

// 文字+图片双保险（三级回退）
{ "type": "find_and_tap", "query": "GIF", "template_path": "keyboard_gif.png" }

// 纯图片查找（无文字）
{ "type": "find_and_tap", "template_path": "icon.png", "threshold": 0.8 }

// 指定 OCR 语言
{ "type": "find_and_tap", "query": "发送", "ocr_lang": "chi_sim" }
```

**新参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `ocr_fallback` | bool | true | 是否启用 OCR 回退 |
| `ocr_lang` | string | "eng+chi_sim" | OCR 语言 |
| `template_path` | string | null | 图片模板路径（用于图片回退） |
| `threshold` | float | 0.80 | 图片匹配阈值 |

> **注意**: `field` 参数已废弃，默认使用 `text_or_desc`（优先 text，然后 desc）。

---

### 📝 简易脚本格式（面向非程序员）

除了 JSON 格式，还支持**人类可读的简易脚本格式** `.script`，适合设计、产品、运营人员阅读和编写。

#### 示例对比

**JSON 格式（程序员友好）：**
```json
{
  "type": "find_and_tap",
  "query": "设置键盘",
  "tap_offset_x": 50,
  "tap_offset_y": 100,
  "comment": "点击输入框"
}
```

**简易脚本格式（所有人友好）：**
```
4. 点击文字 "设置键盘"
   - 偏移: 向右50像素, 向下100像素
   - 说明: 点击输入框
```

#### 简易脚本语法

```markdown
# 工作流: 名称
# 说明: 描述信息

---

## 阶段标题（可选）

1. 清空后台进程

2. 从桌面启动应用 "微信"
   - 等待 2秒

3. 点击文字 "发现"

4. 点击图片 @templates/icon.png
   - 相似度: 80%
   - 搜索区域: 屏幕下半部分

5. 等待 1.5秒

6. 向上滑动页面
   - 起点: (50%, 85%)
   - 终点: (50%, 40%)

7. 截图 "result"

8. 重复 3次:
    - 向下滑动页面
    - 等待 2秒

---
# 完成
```

#### 支持的动作

| 动作 | 语法示例 |
|------|----------|
| 清空后台 | `清空后台进程` |
| 启动应用 | `从桌面启动应用 "微信"` |
| 点击文字 | `点击文字 "确定"` 或 `点击 "确定"` |
| 点击图片 | `点击图片 @templates/icon.png` |
| 点击坐标 | `点击坐标 (540, 1200)` |
| 输入文字 | `输入文字 "hello"` |
| 滑动 | `向上滑动页面` / `向下滑动页面` / `向左滑动页面` / `向右滑动页面` |
| 等待 | `等待 2秒` |
| 截图 | `截图 "名称"` |
| 内存快照 | `保存内存快照 "名称"` |
| 按键 | `按返回键` / `按主页键` |
| 重复 | `重复 3次:` |

#### 支持的选项

| 选项 | 语法示例 |
|------|----------|
| 偏移 | `偏移: 向右50像素, 向下100像素` |
| 相似度 | `相似度: 80%` |
| 搜索区域 | `搜索区域: 屏幕下半部分` |
| 语言 | `语言: 中文` / `语言: 英文` |
| 说明 | `说明: 这是注释` |
| 等待 | `等待 2秒` |
| 起点/终点 | `起点: (50%, 85%)` / `终点: (50%, 40%)` |

#### 格式转换命令

```bash
# 将 JSON 转为简易脚本（方便阅读）
python android_tool/script_parser.py convert workflow/my_test.json

# 将简易脚本转为 JSON（执行需要）
python android_tool/script_parser.py convert workflow/my_test.script

# 指定输出路径
python android_tool/script_parser.py convert input.script -o output.json
```

#### 文件命名约定

- `.json` - 程序执行用的 JSON 格式
- `.script` - 人类可读的简易格式
- `.md` - Markdown 格式（内嵌脚本代码块）

#### MCP 工具调用

```python
# 直接执行脚本文件（支持 .json, .script, .md）
android_run_script(file_path="/path/to/workflow.script")
android_run_script(file_path="/path/to/workflow.md")
android_run_script(file_path="/path/to/workflow.json")

# 转换脚本格式
android_convert_script(input_path="workflow.json")           # → workflow.script
android_convert_script(input_path="workflow.script")         # → workflow.json
android_convert_script(input_path="workflow.md", to_format="md_simple")  # MD内JSON→简易脚本
```

#### 命令行转换

```bash
# JSON ↔ 简易脚本互转
python android_tool/script_parser.py convert workflow/my_test.json
python android_tool/script_parser.py convert workflow/my_test.script

# 批量转换 workflow_docs 目录
for f in recordings/workflow_docs/*.md; do
  python -c "from android_tool.script_parser import convert_md_to_simple_script; convert_md_to_simple_script('$f')"
done
```

---

生成的目录结构默认类似：

- `./recordings/<timestamp>_<serial>_<name>_<recording_id>/getevent.log`
- `./recordings/<timestamp>_<serial>_<name>_<recording_id>/touch.mks`
- `./recordings/<timestamp>_<serial>_<name>_<recording_id>/meta.json`
- `./recordings/index.jsonl`（索引：用于查询历史录制，追加写）

设备信息会单独存储（供多条录制复用），并由每条录制通过 `device_id` 关联：

- `./recordings/devices/<device_id>.json`（设备信息快照，device_id 默认是 adb serial）
- `./recordings/devices/index.jsonl`（设备信息索引，追加写）

说明：
- `meta.json` 会记录 `device_id`（默认就是 adb serial），用于关联 `devices/<device_id>.json` 中的设备信息。
  - `android_record_start` 会自动触发一次设备信息采集并写入 `devices/`，也可以手动调用 `android_device_capture()` 先采集。


