# 后续开发计划 / Backlog

记录当前已知问题与待做项，便于后续排期与实现。

---

## 1. 性能与稳定性

### 1.1 dumpsys meminfo 耗时

- **现象**：`run_script` 默认 `auto_meminfo=True` 时，在调用脚本 `main()` 前会执行 `mem_snapshot(ctx, "before")`，内部使用 `adb shell dumpsys meminfo <当前前台包>`，在部分设备上执行很慢或长时间不返回，导致脚本“卡在 banner 后”。
- **当前规避**：脚本侧可设置 `auto_meminfo=False`；通用默认是否改为 `False` 待定。
- **后续可做**：
  - 为 `capture_meminfo` / `mem_snapshot` 调用增加合理 `timeout_s`，超时则放弃并打日志，不阻塞主流程。
  - 评估是否提供“轻量 meminfo”（若系统有更快的接口或采样方式）。
  - 文档说明：meminfo 适合在需要分析内存时单独开启，日常自动化脚本建议关闭。

---

## 2. 清空后台

### 2.1 仅关闭当前在运行的应用

- **已实现**：Android `clear_background()` 已改为调用 `clear_background_processes(..., mode="force_stop_running_3p")`，只对当前在运行的三方进程做 force-stop（通过 `ps -A` 解析），不再全量 `pm list packages -3`。

---

## 3. Git 与 RunSession 记录

### 3.1 记录“改了哪些代码”且不拖慢启动

- **已实现**：RunSession 创建时只同步写 commit + dirty（`git_info.json`）；完整 `git diff HEAD` 改为在**后台线程**中执行并写入 `git_diff.patch`，不阻塞脚本启动。

---

## 4. 应用启动 (launch_app)

### 4.1 query-activities 超时与启动慢

- **已实现**：新增 `get_launcher_component_for_package(device_serial, package)`，使用 `cmd package resolve-activity --brief -a MAIN -c LAUNCHER <package>` 只查**单个包**的 LAUNCHER，超时 5s。`restart_app` 时先走该快路径，拿不到再 fallback 到 `list_launcher_components`（10s 超时）或 monkey；并对 `am start` / monkey 的 CommandRunner 增加 `timeout_s=10`/`15`，避免无限阻塞。
- **后续可做**：设备画像/缓存“包名 → component”，进一步减少 resolve-activity 调用。

### 4.2 超时时间是否足够

- 10–15s 在部分设备上仍可能不够（query-activities 全量枚举），且与已安装应用数量强相关。
- 更稳妥的方式是：不依赖长超时，而是**优先用设备画像/缓存 component，查不到再 fallback 到 query-activities（带短超时）或 monkey**。

---

## 5. 脚本运行体验

### 5.1 运行过程中弹窗处理

- **现状**：ScriptContext 有 `popup_guard=True`、`popup_rules`、自愈逻辑，可在步骤失败时尝试识别并关闭弹窗。
- **可改进**：
  - 在关键步骤（如启动应用后、滑动前）做一次“弹窗检测”：截图或 dump UI，匹配常见弹窗（登录、权限、更新、青少年模式等），若命中则自动点“稍后/跳过/同意”或记录并中止，避免脚本在无效页面上继续滑动。
  - 弹窗规则可配置（文案、resource-id、坐标范围），并支持“仅记录不操作”模式，便于调试。
  - 文档说明：哪些场景会触发弹窗检测、默认行为是什么、如何关闭或自定义。

### 5.2 登录/引导页检测（如抖音脚本）

- 打开抖音后若落在登录页或引导页，当前无法滑动，脚本会“空转”。
- **待做**：在“打开应用并等待”之后，做一次页面状态检测（OCR/UI 文本或关键节点），若识别到“登录”“同意”“青少年”等，则报错退出或打日志并 return 非零，明确提示“当前为登录/引导页，请先手动登录或跳过”。

---

## 文档维护

- 本文档随需求与实现情况更新，新问题或计划可追加章节或条目。
- 完成项可注明“已实现”并移到“已完成”小节或删除，保持 backlog 可执行。
