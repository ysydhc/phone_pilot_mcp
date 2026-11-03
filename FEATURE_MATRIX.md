# Phone Pilot 跨平台功能对齐矩阵 (Feature Matrix)

本文档记录 Phone Pilot 在 **Android**、**HarmonyOS (鸿蒙)** 与 **iOS** 三大主流移动操作系统上的能力支持现状与对齐架构。

---

## 一、跨平台架构概览

Phone Pilot 采用分层解耦的跨平台驱动体系：
- **Android**: 基于 ADB 协议与 Android UIAutomator / Accessibility 树驱动，支持真机反向代理 (`adb reverse`)、多并发会话与高性能快照。
- **HarmonyOS**: 基于 HDC 协议与 ArkUI `uitest dumpLayout` 布局树，提供文件互斥会话锁 (`HarmonySessionLock`)、息屏自动点亮与 `synthetic_id` 虚拟层级标识。
- **iOS**: 基于 `idb` / `WebDriverAgent (WDA)`，具备 XCUIElement 递归局部转储、SpringBoard 系统权限弹窗精准防御、`iproxy` 端口转发与 MockServer 联动平替。
- **通用层 (Cross-Platform)**: 统一 MCP 工具协议、PopupGuard 自动弹窗守卫、VLM 多模态视觉定位兜底 (`find_vision`)。

---

## 二、平台能力详细对齐矩阵

| 功能模块 | 子功能 / 契约 | Android | HarmonyOS | iOS | 架构实现与对齐方案 |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **设备与会话管理** | 设备连接与状态探测 | ✅ | ✅ | ✅ | ADB / HDC / idb companion |
| | 多进程并发与会话锁 | ✅ | ✅ | ✅ | 鸿蒙采用基于进程与句柄的 `HarmonySessionLock`；iOS 采用 WDA Session 复用 |
| | **多 Agent 设备租约锁** | ✅ | ✅ | ✅ | 跨进程内核级文件锁 (`fcntl.flock`) + TTL 租约防死锁 + PID 存活检测；支持排队加锁、续期与强占防护 |
| | 环境变量设备优先绑定 | ✅ | ✅ | ✅ | 优先通过 `PHONE_PILOT_DEVICE_SERIAL` / `PHONE_PILOT_PLATFORM` 绑定专属设备，避免多项目串扰 |
| | 息屏自动唤醒与点亮 | ✅ | ✅ | ✅ | 鸿蒙 `power_shell` + `input keyevent 26` 自愈；iOS `idb press-button POWER` |
| | 屏幕截图与尺寸探测 | ✅ | ✅ | ✅ | 统一返回 PNG 二进制与 `(width, height)` |
| | MockServer 反向代理 | ✅ | ⚠️ 方案支持 | ✅ | Android 原生 `adb reverse`；iOS 采用 `iproxy` + 动态端口映射平替 |
| **UI 检查与解析** | UI 层次结构转储 (Dump) | ✅ | ✅ | ✅ | Android `uiautomator dump`；鸿蒙 `uitest dumpLayout`；iOS WDA PageSource 递归剪枝 |
| | 局部转储 / 大树剪枝 (ROI) | ✅ | ✅ | ✅ | iOS 支持针对非活动/弹窗容器的局部快照；鸿蒙支持深度截断 |
| | 虚拟合成标识 (synthetic_id) | ✅ (原生ID) | ✅ (合成增强) | ✅ (XCUI identifier) | 鸿蒙为无 ID 容器节点基于控件类型、深度与兄弟索引生成稳定 `synthetic_id` |
| | 元素可见性与可点击过滤 | ✅ | ✅ | ✅ | iOS 结合 `visible` 与 `XCUIElementType` 规则精准富化 `clickable` 属性 |
| | 页面状态获取 (page_state) | ✅ | ✅ | ✅ | 统一返回 `activity/page`、编号控件树、可交互中心点 |
| **MCP 工具交互路由** | 基础点击 (`phone_tap`) | ✅ | ✅ | ✅ | 统一像素坐标映射与各 Driver 交互层下发 |
| | 文本输入 (`phone_input_text`)| ✅ | ✅ | ✅ | 支持剪贴板广播、原生输入法与软键盘模拟 |
| | 滑动与手势 (`phone_swipe`) | ✅ | ✅ | ✅ | 支持指定起始坐标与耗时 |
| | 编号快速点击 (`tap_element`) | ✅ | ✅ | ✅ | 结合 `phone_get_page_state` 的编号直接命中并点击 |
| | 应用启动与强停 (`launch/stop`) | ✅ | ✅ | ✅ | Android Intent / 鸿蒙 AA 命令行 / iOS Bundle ID Launch |
| | 导航按键 (`go_home` / `go_back` / `go_recents`) | ✅ | ✅ | ✅ | Android Keycode 187 / 鸿蒙底端上滑手势降级 / iOS SpringBoard 上滑悬停唤出 App Switcher |
| **页面认知图谱与漫游** | 拓扑寻路与三级自愈 (`navigate_to`) | ✅ | ✅ | ✅ | 基于 UIKnowledgeGraph Dijkstra 最短路径规划与弹窗/滚动/重排自愈 |
| | 一键应用探索脑图 (`explore_app`) | ✅ | ✅ | ✅ | 启发式自动漫游、敏感破坏性动作过滤、BFS 页面入图与回溯，生成 Mermaid/Mindmap |
| | 图谱持久化与状态转移 (`record/save/load`) | ✅ | ✅ | ✅ | 记录动作触发标准与成功率边权重，无缝序列化为标准化 JSON |
| **打分系统与可视化看板** | 端侧执行质量打分 (`score_engine`) | ✅ | ✅ | ✅ | 四维度（正确性 40%、有效性 30%、时延 20%、自愈代价 10%）100 分制质量量化打分 |
| | 交互式数据看板 (`dashboard`) | ✅ | ✅ | ✅ | 自包含 HTML 看板，呈现 KPI 卡片、三端对比、健康度矩阵、慢调用与应用脑图联动 |
| **智能查找与视觉定位** | 文本智能查找 (`smart_find`) | ✅ | ✅ | ✅ | UIA 树查找 -> OCR 降级 -> 多模态视觉兜底三层漏斗 |
| | 多模态视觉定位 (`vision_find`)| ✅ | ✅ | ✅ | VLM 零样本视觉定位；未配置 API 时静默安全跳过 |
| | 滚动查找 (`scroll_to_find`) | ✅ | ✅ | ✅ | 基于方向滑动与步进重试 |
| **弹窗与自愈守卫** | 自动弹窗守卫 (PopupGuard) | ✅ | ✅ | ✅ | 支持权限申请、系统通知、更新弹窗自动识别与关闭 |
| | 否定词防护 (Negative Filter) | ✅ | ✅ | ✅ | 严格区分“允许”与“不允许”、“Don't Allow”与“Allow”，防止误点拒绝 |
| | iOS SpringBoard 专有防御 | N/A | N/A | ✅ | 针对 iOS 弹窗按钮缺少文本与属性反转专项优化 |

*图例说明：✅ 全面支持且已自动化测试覆盖；⚠️ 方案已制定或受限于系统底层机制；N/A 不适用。*

---

## 三、关键测试集与验证

上述能力的对齐状态均由自动化回归测试集严格把关：
1. **HarmonyOS**:
   - `tests/test_harmony_session_lock.py` (会话锁与息屏自愈)
   - `tests/harmony/test_harmony_ui.py` (ArkUI 层次结构与 `synthetic_id` 稳定性)
   - `tests/test_harmony_popup_guard.py` (鸿蒙弹窗规则与自动关闭)
2. **iOS**:
   - `tests/ios/test_ios_ui.py` (iOS PageSource 转储、元素剪枝与可见性过滤)
   - `tests/test_mock_reverse.py` (iOS 反向代理平替与联动验证)
   - `tests/test_mcp_helpers_ios.py` (iOS MCP 工具全量路由与设备绑定)
   - `tests/ios/test_ios_popup_guard.py` (iOS SpringBoard 弹窗规则与否定词防御)
3. **Cross-Platform / Vision / Concurrency**:
   - `tests/test_device_lease.py` (跨进程设备租约、TTL自动过期、重入更新与上下文管理器)
   - `tests/test_device_mcp_lock.py` (MCP 设备锁工具链路、环境变量绑定优先、并发写操作软拦截)
   - `tests/test_mcp_vision_tools.py` (跨平台多模态视觉 MCP 工具路由)
   - `tests/test_vision_find.py` (跨平台视觉查找与 Tier 3 兜底链条)
