# auto_screenshot 逻辑说明与流程图

## 结论（先看）

- **查找时**：截图发生在 **「查找」成功/失败时**（find_text / find_image / scroll_to_find），即点击/滑动**前**的画面。
- **主动模式（auto_screenshot=True）下，用户输入操作后也会截图**：
  - **点击 / 双击**：`find_text(...).tap()` 或 `chain().find_text(...).tap()` 执行后，**等待 1s** 再截一张图（保证页面 UI 稳定），action 为 `tap_after` / `double_tap_after`。
  - **滑动**：`UIElement.scroll()` 或 `swipe()` / `swipe_up()` 等执行后，**等待 1s** 再截一张图，action 为 `swipe_after`。
- 因此：`find_text(ctx, "开屏").tap(wait=1)` 会产生 **2 张截图**：一张为 find 时（点击前），一张为 tap 完成后 1s（点击后）。

---

## 1. 谁在触发截图？

只有 **`ScriptContext._observe(action, detail, status, element)`** 会按 `auto_screenshot` 决定是否截图并写入 RunSession。

```
┌─────────────────────────────────────────────────────────────────┐
│  ctx._observe(action, detail, status="ok"|"fail", element=None)  │
│  - _step_counter += 1                                            │
│  - if auto_log: 打印步骤日志                                      │
│  - if auto_screenshot: 截屏 → png_bytes                          │
│  - if png_bytes && element && ok: 在截图上标注 element 框         │
│  - if session: session.add_step(step, action, status, detail,   │
│                png_bytes=png_bytes) → 写入 screenshots/NNN_action.png │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 谁在调用 _observe？

**两类调用方：**

| 调用方 | 何时调用 | action 值 |
|--------|----------|-----------|
| **查找类** | 找到/失败时返回前 | `find_text` / `find_image` / `scroll_to_find` |
| **用户输入后（主动模式）** | 点击/双击/滑动执行完后 **等待 1s** 再调用 | `tap_after` / `double_tap_after` / `swipe_after` |

**查找类**（仅在返回前调用一次）：

- `find.py` find_text：找到元素时 / 重试全部失败时
- `find.py` find_image：找到时 / 重试全部失败时
- `runner.py` scroll_to_find：找到时 / 失败时

**用户输入后**（仅当 `ctx.auto_screenshot=True` 时）：

- `core/ui/element.py` UIElement.tap()：tap 完成后 → `_op("observe_after_input")("tap_after"|"double_tap_after", detail)`
- `core/ui/element.py` UIElement.scroll()：swipe 完成后 → `observe_after_input("swipe_after", ...)`
- `script_api/actions.py` swipe()：滑动完成后 → `ctx._observe("swipe_after", "swipe")`

以上「用户输入后」的截图均由内部先 **sleep(1)** 再 `_observe`，以保证页面 UI 稳定。

---

## 3. 脚本里一句 find_text().tap() 的实际执行顺序

```
脚本: find_text(actual_ctx, "开屏", exact=True, ...).tap(wait=1)
```

执行顺序：

```
  ┌──────────────────────────────────────────────────────────────────┐
  │ 1. find_text(ctx, "开屏", ...) 执行                               │
  │    - UIA/OCR 查找 "开屏"                                          │
  │    - 找到 → 返回 UIElement 前调用 ctx._observe("find_text", ...,  │
  │             element=hit)                                         │
  │      → 若 auto_screenshot=True：此时截屏（点击前的画面）            │
  │      → add_step → 保存为 001_find_text.png 等                     │
  │    - 返回 UIElement                                               │
  └──────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │ 2. .tap(wait=1) 执行                                              │
  │    - UIElement.tap() → _op("tap_xy")(x, y) → driver.input.tap()  │
  │    - 若 auto_screenshot=True：_op("observe_after_input")         │
  │      → sleep(1) → ctx._observe("tap_after", detail) → 再截一张图  │
  └──────────────────────────────────────────────────────────────────┘
```

所以：**同一句 find_text(...).tap() 在主动模式下会产生 2 张图：一张点击前（find_text），一张点击后 1s（tap_after）。**

---

## 4. 整体流程图（auto_screenshot 与步骤记录）

```
                    ┌─────────────────────┐
                    │ 脚本调用             │
                    │ find_text / find_image│
                    │ / scroll_to_find     │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │ 查找逻辑执行         │
                    │ (UIA / OCR / 滚动)   │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                 ▼
         [找到元素]       [未找到且还有重试]   [全部重试失败]
              │                     │                 │
              │                     └────────┬────────┘
              │                              │ (不调用 _observe，继续重试)
              ▼                              ▼
    ctx._observe("find_text",           ctx._observe("find_text",
      str(hit), element=hit)             "未找到...", status="fail")
              │                              │
              └──────────────┬───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │ _observe 内部                 │
              │ • _step_counter += 1          │
              │ • auto_log → 打日志           │
              │ • auto_screenshot → 截屏     │  ← 仅在此处截屏
              │ • 有 element 且 ok → 标注框   │
              │ • session.add_step(...)       │
              │   → screenshots/NNN_xxx.png   │
              └──────────────────────────────┘
                             │
                             ▼
              返回 UIElement（或 None）
                             │
              若返回了 UIElement，脚本再调用
                             │
                             ▼
              ┌──────────────────────────────┐
              │ .tap() / .extract() / ...     │
              │ 若 auto_screenshot：          │
              │   observe_after_input         │  ← 点击后 sleep(1) 再截图
              │   → tap_after / swipe_after   │
              └──────────────────────────────┘
```

---

## 5. 脚本里会触发截图的步骤（对照，主动模式）

| 脚本步骤 | 截图时机 | 截图内容 |
|----------|----------|----------|
| find_text("开屏").tap(1) | find 时 + tap 后 1s | 点击前首页 + 点击后 1s 画面 |
| find_text("load").tap(0.6) × N 次 | 每次 find 时 + 每次 tap 后 1s | 点击前 + 点击后 1s |
| find_text("请求成功") / find_text("请求失败") | 仅 find 时（无 tap） | 轮询时的当前页 |
| find_text("show").tap(0.8) | find 时 + tap 后 1s | 点击前 + 点击后 1s |
| chain().swipe_up() 等 | swipe 后 1s | 滑动后 1s 画面 |
| mem_snapshot / screenshot("ad_display_done") | 否（仅手动 screenshot） | ad_display_done.png |

---

## 6. 实现说明（已实现）

主动模式下（`auto_screenshot=True`），点击/双击/滑动后由 **observe_after_input** 统一处理：**先 sleep(1)** 再 `ctx._observe(action, detail)`，保证页面 UI 稳定后再截图。实现位置：

- `script_api/_locate.py`：`_ui_ops` 中提供 `observe_after_input(action, detail)`，内部判断 `ctx.auto_screenshot` 后 sleep(1) 并调用 `ctx._observe`。
- `core/ui/element.py`：`UIElement.tap()` 与 `UIElement.scroll()` 在操作完成后调用 `_op("observe_after_input")`。
- `script_api/actions.py`：`swipe()` 在滑动完成后若 `ctx.auto_screenshot` 则 sleep(1) 并 `ctx._observe("swipe_after", "swipe")`。
