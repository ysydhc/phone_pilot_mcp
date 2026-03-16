# Android / 移动设备 MCP 生态调研

> 调研对象：面向 Android（及部分含 iOS）的设备级 MCP 服务器；效果与使用技术。  
> 更新：基于 2025 年 3 月公开信息与 GitHub 检索。

---

## 1. 概览与对比表

| 项目 | Stars | 技术栈 | 运行位置 | 连接方式 | 工具数 | 典型延迟 | 效果/特点 |
|------|-------|--------|----------|----------|--------|----------|-----------|
| **CursorTouch/Android-MCP** | 457 | Python 3.10+、ADB、Accessibility API | 主机 | USB/局域网 ADB | 11 | 2～4 s | 轻量、Bring Your Own LLM、无 CV 模型 |
| **hao-cyber/phone-mcp** | 216 | Python 3.7+、ADB | 主机 | USB ADB | 多（CLI 丰富） | — | 通话/短信/联系人/媒体/UI 分析/监控、analyze_screen |
| **MobAI-App/mobai-mcp** | 47 | Node.js 18+、MobAI 桌面端 HTTP API | 主机 + MobAI 桌面 | HTTP 127.0.0.1:8686 | 20+ | — | Android+iOS、Web/CSS、DSL 批执行、AI Agent |
| **danielealbano/android-remote-control-mcp** | 12 | Kotlin、Ktor+Netty、Accessibility、CameraX | **手机端** | HTTP/HTTPS（可 ngrok/Cloudflare） | 54 | **10～100 ms** | 机内 MCP、省 token、标注截图、多设备 slug |
| **axonixtools/PocketMCP** | 2 | Kotlin | 手机端 | LAN | — | — | 手机变 MCP 服务器、AI 调手机 |
| **ccoodduu/phone-tasker-mcp** | 0 | Python | 主机 | — | Tasker 动作 | — | 暴露 Tasker 为 MCP 工具 |
| **AuraFriday/android_mcp** | 5 | — | — | — | — | — | 从 Android 控制/运行 MCP |
| **phone_pilot (本仓库)** | — | Python 3.13、ADB、UIAutomator、可选 HarmonyOS | 主机 | USB/ADB、stdio MCP | 50+ (phone_*) | — | get_page_state、tap_element、open_deeplink、验证/录屏/脚本 |

---

## 2. 各项目简介与使用技术

### 2.1 CursorTouch/Android-MCP（⭐ 457）

- **仓库**：[CursorTouch/Android-MCP](https://github.com/CursorTouch/Android-MCP)
- **定位**：轻量开源，连接 AI Agent 与 Android 设备，支持应用导航、UI 交互、自动化 QA，**不依赖传统 CV 或预编脚本**。
- **技术**：
  - **Python 3.10+**，通过 **ADB** 与 **Android Accessibility API** 与设备通信。
  - 获取 UI：读 **view hierarchy**，提供可交互元素与设备状态。
  - 支持 **uvx** / **uv** 运行，Claude Desktop / Cursor 等 MCP 客户端。
- **工具（11 个）**：State-Tool（设备状态+可交互元素）、Click/Long-Click/Type/Swipe/Drag、Press-Tool（Back/Volume 等）、Wait、Notification-Tool、Shell-Tool。
- **效果**：与任意 LLM/VLM 配合即可用，无需微调 CV；典型操作间隔 **2～4 s**；支持 Android 10+；可选 `SCREENSHOT_QUANTIZED` 降低 token。

---

### 2.2 hao-cyber/phone-mcp（⭐ 216）

- **仓库**：[hao-cyber/phone-mcp](https://github.com/hao-cyber/phone-mcp)
- **定位**：通过 **ADB** 用自然语言控制 Android 手机，支持通话、短信、联系人、媒体、UI 分析与监控。
- **技术**：
  - **Python 3.7+**，**ADB** 命令与设备交互。
  - **analyze_screen**：结构化屏幕信息（UI 元素、文本等），可选带 base64 截图。
  - **screen-interact** 统一接口：tap（坐标/按文本/按 content-desc）、swipe、key、text、find、wait、scroll。
  - **monitor-ui**：监控 UI 变化、等待某文本/ID/class 出现或消失。
- **工具/能力**：通话/挂断、短信、联系人、创建联系人（UI 自动化）、截图/录屏、应用启动/关闭/列表、launch Activity、Intent、open-url、analyze_screen、screen-interact、monitor-ui、POI 搜索等。
- **效果**：功能覆盖面广，适合「自然语言任务」；需 USB 调试与 ADB；支持 Cursor、Claude 等；有中文文档。

---

### 2.3 MobAI-App/mobai-mcp（⭐ 47）

- **仓库**：[MobAI-App/mobai-mcp](https://github.com/MobAI-App/mobai-mcp)
- **定位**：MobAI 的 MCP 服务端，供 Cursor、Windsurf、Cline、Claude Desktop 等控制 **Android 与 iOS** 设备/模拟器。
- **技术**：
  - **Node.js 18+**，**npx mobai-mcp**；与 **MobAI 桌面端** 通信（本地 HTTP API，默认 **8686**）。
  - 桌面端负责与真机/模拟器桥接；MCP 通过 HTTP 调用 MobAI API。
  - **get_ui_tree**：可访问性树，支持 text_regex、bounds 过滤。
  - **Web 自动化**：web_list_pages、web_navigate、web_get_dom、web_click（CSS 选择器）、web_type、web_execute_js。
  - **execute_dsl**：批量执行自动化步骤（JSON 步骤列表）。
  - **run_agent**：在设备上跑自主 Agent 完成复杂任务。
- **工具**：设备管理、get_screenshot、get_ui_tree、tap/type_text/swipe、go_home、launch_app、list_apps、execute_dsl、run_agent、Web 系列、http_request。
- **效果**：双平台、Web/WebView 与原生统一、DSL 与 Agent 适合多步编排；依赖 MobAI 桌面端与桥接；Resources 提供 API/DSL 文档。

---

### 2.4 danielealbano/android-remote-control-mcp（⭐ 12）

- **仓库**：[danielealbano/android-remote-control-mcp](https://github.com/danielealbano/android-remote-control-mcp)
- **定位**：**在 Android 设备上运行** 的 MCP 服务器 App，通过 Accessibility + 截图等实现远程控制，**无需主机 ADB**，可经隧道从公网访问。
- **技术**：
  - **Kotlin**，**Ktor + Netty** 在机内起 HTTP(S) 服务，实现 MCP Streamable HTTP（JSON-RPC，`/mcp`）。
  - **Accessibility Service**：读 UI 节点、执行点击/滑动等；**takeScreenshot()**（Android 11+）截图。
  - **Token 优化**：紧凑的 UI 节点表示、可选低分辨率/质量截图、带编号的标注图，相较 uiautomator dump 可省 10～50× token。
  - **可选**：Cloudflare Quick Tunnels / ngrok 暴露 HTTPS；Bearer 鉴权；每工具/参数可单独开关。
  - 其他：CameraX 拍照/录像、剪贴板、文件（SAF）、Intent/URI、NotificationListenerService 等。
- **工具（54 个，12 类）**：get_screen_state、press_back/home/recents、tap/long_press/swipe/scroll、find_nodes/click_node/tap_node/scroll_to_node、type_*、clipboard、文件、open_app/list_apps/close_app、相机、send_intent/open_uri、通知列表/操作/回复等。
- **效果**：**延迟 10～100 ms**（机内执行）；可完全脱离主机与 USB；适合远程/公网控制与省 token 的 Agent 循环；需在设备上安装 APK 并授予无障碍等权限。

---

### 2.5 其他（PocketMCP、phone-tasker-mcp、AuraFriday、DroidLens 等）

- **axonixtools/PocketMCP**（Kotlin，2 stars）：把 Android 手机变成 MCP 服务器，通过 LAN 让 AI/桌面脚本调手机能力。
- **ccoodduu/phone-tasker-mcp**（Python）：将 **Android Tasker** 动作暴露为 MCP 工具，供 AI 助手调用。
- **AuraFriday/android_mcp**（5 stars）：从 Android（手机/平板等）控制或运行 MCP。
- **coki230/DroidLens**、**ericymtx-ux/android-phone-mcp**：用 MCP 控制 Android，实现细节需看仓库。
- **kevintpeng/termux-puppeteer-mcp**：在 Termux 上跑 Puppeteer MCP，偏**手机端浏览器自动化**，非设备级 UI 控制。

---

## 3. 技术维度归纳

| 维度 | 说明 | 代表 |
|------|------|------|
| **连接架构** | **主机 + ADB**：主机跑 MCP，通过 USB/网络 ADB 控手机 | Android-MCP、phone-mcp、phone_pilot、MobAI（经桌面端） |
| | **机内 MCP**：手机装 App，本机起 HTTP MCP 服务，主机或远程通过 HTTP/隧道访问 | android-remote-control-mcp、PocketMCP |
| **UI 获取** | **ADB + UIAutomator dump**：dump hierarchy XML，解析节点 | Android-MCP、phone-mcp、phone_pilot |
| | **Accessibility API（机内）**：App 内读 Accessibility 节点 + takeScreenshot() | android-remote-control-mcp |
| **操作执行** | **ADB input**：tap、swipe、keyevent、input text | 主机侧方案普遍 |
| | **Accessibility 节点操作**：find/click node by id/text/bounds | android-remote-control-mcp、部分 phone-mcp（screen-interact） |
| **Token 与截图** | 原始 dump 体积大；省 token 手段：过滤节点、压缩截图、标注图、可选分辨率/质量 | android-remote-control-mcp（强调）、Android-MCP（SCREENSHOT_QUANTIZED）、phone_pilot（annotate_elements） |
| **双平台** | 仅 Android vs Android + iOS | MobAI 支持双平台；其余多为仅 Android |

---

## 4. 效果与适用场景（简要）

- **CursorTouch/Android-MCP**：生态关注度高（457 stars），上手快，适合「主机 + ADB + 任意 LLM」的自动化与 QA；延迟 2～4 s 可接受。
- **hao-cyber/phone-mcp**：功能多（通话/短信/联系人/分析/监控），适合需要「手机日常能力 + 屏幕分析」的 Agent；依赖 ADB。
- **MobAI MCP**：适合既要 Android 又要 iOS、且需要 Web/WebView 与 DSL/Agent 的场景；依赖 MobAI 桌面与生态。
- **danielealbano/android-remote-control-mcp**：延迟低、省 token、可远程/公网，适合「手机独立作为 MCP 端点」；需装 APK 并授权。
- **phone_pilot（本仓库）**：面向脚本化与验证（get_page_state、tap_element、open_deeplink、验证/录屏/脚本 API），工具数量多，可与现有 pipeline 深度集成；主机 + ADB，支持 HarmonyOS 扩展。

---

## 5. 参考链接

- [CursorTouch/Android-MCP](https://github.com/CursorTouch/Android-MCP)
- [hao-cyber/phone-mcp](https://github.com/hao-cyber/phone-mcp)
- [MobAI-App/mobai-mcp](https://github.com/MobAI-App/mobai-mcp)
- [danielealbano/android-remote-control-mcp](https://github.com/danielealbano/android-remote-control-mcp)
- [axonixtools/PocketMCP](https://github.com/axonixtools/PocketMCP)
- [ccoodduu/phone-tasker-mcp](https://github.com/ccoodduu/phone-tasker-mcp)
- [Model Context Protocol](https://modelcontextprotocol.io/)

---

*报告基于公开仓库与文档整理，具体效果以实际使用为准。*
