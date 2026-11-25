# PhonePilot 跨平台能力成熟度矩阵（Single Source of Truth）

> **最后核验日期**：`2026-11-25` | **对应版本**：`v0.12.0+`  
> **治理规则**：本文件是 `phone_pilot` 三端能力状态的**唯一权威事实源**（原 `phone_pilot/FEATURE_MATRIX.md` 已收敛指向本文件）。任何跨平台驱动、MCP 工具或现场复原能力的变更必须同步更新本表。

---

## 一、背景与成熟度四级定义

### 问题定义
早期仓库并存两份 `FEATURE_MATRIX.md`，对 iOS 与 HarmonyOS 的支持程度采用含糊的“✅ / 未实现(有方案)”标记且结论互相冲突，导致研发排期与 AI Agent 跨端调用误判。本矩阵将平台能力拆分为 **Android**、**HarmonyOS**、**iOS Simulator**、**iOS Real Device（真机）** 四个真实交付环境，严格区分“模拟器可用”与“真机生产就绪”。

### 适用范围与非目标（Non-Goals）
- **适用范围**：`DeviceDriver` 协议层、MCP 95 项工具、`script_api` 65 项公开接口、本地双端口 `MockServer`、v4.0 业务报告与 `scene.v1` 现场复原。
- **非目标**：不覆盖越狱（Jailbreak）或 Root 专属能力；所有端侧能力均建立在官方调试通道（ADB / HDC / WDA + simctl + pymobiledevice3）之上。

### 四级成熟度标签规范

| 等级标签 | 判定标准（必须同时满足） | 调用预期 |
| :---: | :--- | :--- |
| **`GA`** | 真实设备 E2E 验证通过 + 自动化单测覆盖 + 异常路径 Fail-Closed | 生产回归门禁可直接依赖 |
| **`Beta`** | 真机或模拟器主链路已跑通 + 单测覆盖，但存在明确的系统前置约束或边角限制 | 满足前置条件后可稳定使用 |
| **`Exp`** | **Experimental（实验态）**：协议与 Mock 单测已覆盖，**待真机完整签名/隧道 E2E 闭环验证** | 仅供实验联调，不可作为无人值守发版门禁 |
| **`N/S`** | **Unsupported（系统机制限制）**：受限于非越狱 OS 沙盒机制，显式返回 `{"ok": False, "error": "not_supported"}` | 上层脚本须按契约降级或跳过 |

---

## 二、跨端能力详细对齐矩阵

### 1. 设备发现、会话与多 Agent 并发治理

| 能力项 | Android | HarmonyOS | iOS Sim | iOS 真机 | 实现机制与前置边界 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| 设备连接探测与 10s 缓存 | `GA` | `Beta` | `Beta` | `Exp` | `adb devices` / `hdc list targets` / `simctl list` / `pmd3 usbmux list` |
| `platform="auto"` 自动路由 | `GA` | `Beta` | `Beta` | `Exp` | `_resolve_device_serial` + `PHONE_PILOT_DEVICE_SERIAL` 环境变量优先绑定 |
| 多 Agent 租约互斥锁 (`DeviceLease`) | `GA` | `GA` | `GA` | `GA` | 宿主机内核级 `fcntl.flock` + PID 存活探测 + TTL 过期自动回收，平台无关 |
| 单机 UI 服务并发保护 | `GA` | `Beta` | `Beta` | `Exp` | 鸿蒙通过 `HarmonySessionLock` 保护单实例 `uitest`；iOS 通过 `wda_bridge` Session 复用与 2.5s 防重连风暴冷却 |
| 息屏唤醒与解锁 (`unlock`) | `GA` | `Beta` | `Beta` | `Exp` | 鸿蒙锁屏下系统拦截 AAMS，**Dump UI 前必须先解锁亮屏** |

### 2. UI 感知、快照缓存与多模态定位

| 能力项 | Android | HarmonyOS | iOS Sim | iOS 真机 | 实现机制与前置边界 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| 原生 UI 树导出 (`dump_ui_nodes`) | `GA` | `Beta` | `Beta` | `Exp` | Android `uiautomator`（支持 `compressed`）；鸿蒙 `uitest dumpLayout`；iOS WDA PageSource 递归剪枝 |
| 无 ID 节点虚拟标识 (`synthetic_id`) | `GA` (原生) | `Beta` (合成) | `Beta` (XCUI) | `Exp` (XCUI) | 鸿蒙基于控件类型、深度与兄弟序号合成稳定 `synthetic_id` |
| 可见比例富化 (`visible_ratio`) | `GA` | `Beta` | `Beta` | `Exp` | `core/geometry.compute_visibility` + iOS DPR (`_get_scale`) 物理像素/逻辑点双向对齐 |
| 0.5s L1 + 30s L2 双层快照缓存 | `GA` | `GA` | `Beta` | `Exp` | `UISnapshotCache`，任意写操作（`tap`/`swipe`/`launch`）即时触发精准失效 |
| `phone_get_page_state` + 编号标注图 | `GA` | `Beta` | `Beta` | `Exp` | 叠加编号边框截图 + `phone_tap_element(index)` 零二次 Dump 直点 |
| `SemanticProjector` 焦点语义降噪 | `GA` | `GA` | `GA` | `GA` | 保序剪枝冗余容器（`index` 不变），上下文体积缩减 `>= 70%` |
| Tier 2 本地 OCR 定位 (`Tesseract`) | `GA` | `GA` | `GA` | `GA` | 需宿主机安装 `tesseract` 与对应语言包（缺失时优雅返回错误字典不崩溃） |
| Tier 3 VLM 视觉定位 (`VisionGrounding`) | `GA` | `GA` | `GA` | `GA` | OpenAI 兼容多模态接口输出 `[0,1000]` 归一化框；未配置 API Key 时静默安全旁路 |
| LLM 语义导航自愈 (`SemanticNavigator`) | `GA` | `Beta` | `Beta` | `Exp` | 精确/别名 ➔ LLM UI 树跨语种对齐（如“激励视频”↔`Rewarded`）➔ VLM 视觉兜底，已接入 `scene_restore` |

### 3. 交互控制、应用管理与系统能力

| 能力项 | Android | HarmonyOS | iOS Sim | iOS 真机 | 实现机制与前置边界 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| 点击 / 滑动 / 长按 / 文本输入 | `GA` | `Beta` | `Beta` | `Exp` | iOS 输入层通过 `_to_pt(px, scale)` 自动将像素换算为 WDA 逻辑点 |
| 导航键 (`go_home` / `go_back` / `go_recents`) | `GA` | `Beta` | `Beta` | `Exp` | 鸿蒙与 iOS 通过底端上滑悬停手势唤出多任务视图 |
| 健壮交互 (`tap_when_ready` / `safe_tap` / `safe_close`) | `GA` | `Beta` | `Beta` | `Exp` | 轮询可点击态防漂移；跳出目标包名自动 `go_back` 恢复 |
| 应用安装 / 卸载 / 启停 (`launch` / `force_stop`) | `GA` | `Beta` | `Beta` (`.app`) | `Exp` (`.ipa`) | iOS 启停后自动调用 `_refresh_wda_after_app_change()` 同步 WDA 上下文，支持 `args` 启动参数 |
| DeepLink 路由拉起 (`open_deeplink`) | `GA` | `Beta` | `Beta` | `Exp` | 已对 URI/包名做 `shlex.quote` 防注入加固 |
| 清空应用沙盒数据 (`clear_data`) | `GA` | `Beta` | `N/S` | `N/S` | iOS 非越狱无系统级清空沙盒 API（返回 `not_supported`，需卸载重装或通过启动参数重置） |
| 清空后台进程 (`clear_background`) | `GA` | `Beta` | `N/S` | `N/S` | iOS 返回 `not_supported` |
| 文件推拉 (`push_file` / `pull_file`) | `GA` | `Beta` | `Beta` | `Exp` | Android `adb` / 鸿蒙 `hdc file` / iOS 真机 `pmd3 afc` |
| 剪贴板读写与受限白名单 Shell | `GA` | `Beta` | `Beta` | `N/S` | iOS 真机无设备端 Shell 与通用剪贴板读取，返回 `not_supported` |
| 广播命令总线 (`send_broadcast`) | `GA` | `Beta` (`ces`) | `N/S` | `N/S` | Android `am broadcast`（带 ACK 区间等待）；鸿蒙 `ces pub`；iOS 需改用 `open_deeplink` 或启动参数 |

### 4. 录屏、日志捕获、网络打桩与性能诊断

| 能力项 | Android | HarmonyOS | iOS Sim | iOS 真机 | 实现机制与前置边界 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| 截图与无 WDA 降级截图 | `GA` | `Beta` | `Beta` | `Exp` | iOS 在 WDA 未就绪时自动降级 `simctl io screenshot` / `pmd3` 并校验非空字节 |
| 屏幕录制 (`start/stop_recording`) | `GA` | `Beta` | `Beta` | `N/S` | Android 支持 **170s 自动循环分段 + `ffmpeg` 无损拼接**突破 180s 限制；iOS 真机返回 `not_supported` |
| 日志捕获与序列断言 (`logcat_*`) | `GA` | `Beta` (`hilog`) | `Beta` (`simctl`) | `Exp` (`pmd3`) | iOS 不支持 `clear_log`（请用 `logcat_mark` + `logcat_since` 时间点切片） |
| 双端口本地 `MockServer`（含序列响应） | `GA` | `Beta` | `Beta` | `Exp` | 控制面与数据面隔离；支持 `responses` 顺序响应、`seq_group` 共享计数、POST Body 捕获与 `/media/*` HTTP Range |
| Mock 反向代理隧道 (`ReverseManager`) | `GA` (`adb reverse`) | `Beta` (`hdc rport`) | `GA` (共享 localhost) | `Exp` (`IProxyManager`) | 鸿蒙采用 `hdc rport` 建连 + `hdc fport rm` 释放；iOS 模拟器直连 `127.0.0.1` 免转发 |
| 内存分析 (`meminfo` / `hprof` 泄漏检测) | `GA` | `Beta` (`hidumper`) | `N/S` | `N/S` | Android 完整支持 `meminfo` + `.hprof` Activity 泄漏检测；鸿蒙支持 `hidumper --mem` PSS/ArkTS 堆快照 |
| 构建物与进程新鲜度门禁 (`FreshnessChecker`) | `GA` | `Beta` | `Beta` | `Exp` | 核验源码 `mtime` < 包体 `mtime` <= 进程 `pid_mtime` 及 `.dex`/Mach-O 关键符号字节流 |

### 5. 认知图谱、极速审片工作台与受控现场复原

| 能力项 | Android | HarmonyOS | iOS Sim | iOS 真机 | 实现机制与前置边界 |
| :--- | :---: | :---: | :---: | :---: | :--- |
| 一键安全漫游建图 (`AppExplorer`) | `GA` | `Beta` | `Beta` | `Exp` | 内置破坏性动作黑名单（退出/注销/支付/删除）、灰盒路由内省、骨架同构归并，导出 Mermaid/脑图 |
| 最短路径自愈导航 (`UINavigator` + `PopupGuard`) | `GA` | `Beta` | `Beta` | `Exp` | Dijkstra 寻路 + 历史经验库/多端弹窗守卫（含否定词防误触）/LLM 三级自愈 |
| v4.0 极速审片台与单调时间轴联动 | `GA` | `GA` | `GA` | `GA` | `AcceptanceProfile` 可插拔胶囊灯带、`timeline_sync` 因果时间轴对齐、靶心标注图、`←/→` 键盘流 |
| 受控设备语义现场复原 (`scene.v1`) | `GA` | `N/S` (`BLOCKED`) | `Beta` | `Exp` | 支持离线严苛模式与 `PHONE_PILOT_SCENE_ALLOW_ONLINE=1` Mock 重放模式；内置像素黑/白屏、Crash/ANR 与 `verify_contract` 终态裁判；鸿蒙当前显式 `BLOCKED` |
| 端侧执行四维质量打分 (`TelemetryScoreEngine`) | `GA` | `GA` | `GA` | `GA` | 正确性(40) + 有效性(30) + 时延(20) + 自愈代价(10)，输出 `pp_dashboard.html` |
| 多模态视觉裁判 (`VisualOracle`) | `Beta` (独立模块) | `Beta` (独立模块) | `Beta` (独立模块) | `Beta` (独立模块) | `extensions/vision/visual_oracle.py` 已实现像素预检 + VLM 语义缺陷判定，**待原生内嵌至 `BusinessReport` / MCP 工具层** |

---

## 三、验证覆盖与验收判据

1. **Android**：全量单元测试 + 真机（POCO / Redmi / Samsung 等）17+ 复杂场景发版门禁常态化运行。
2. **HarmonyOS**：253 项驱动与系统单元测试通过 + 华为 Pura 70 Pro+（HarmonyOS 4.x/Next）真机 7 大核心领域实测通过。
3. **iOS**：驱动层 43 入口协议单测（`tests/ios/`）与 iOS Simulator 端到端链路已验证；iOS 真机 E2E 需宿主机具备 Xcode 已签名 `WebDriverAgentRunner` 与 `pymobiledevice3 remote tunneld`（iOS 17+）。
4. **L1 零业务耦合门禁**：由 `tests/test_zero_business_coupling.py` 静态扫描全仓 `*.py` 与 `*.j2`，确保底座零业务包名/域名残留。
