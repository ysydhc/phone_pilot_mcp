# CLI-Anything 技术调研报告

## 1. CLI-Anything 是什么？

### 1.1 全称与定位

- **全称**：CLI-Anything（副标题：Making ALL Software Agent-Native）
- **一句话**：将任意有代码库的软件，自动生成可供 AI Agent 调用的、生产级 CLI 接口，使软件「Agent 原生」化。

### 1.2 来源

| 项目 | 说明 |
|------|------|
| **仓库** | [HKUDS/CLI-Anything](https://github.com/HKUDS/CLI-Anything) |
| **机构** | 香港大学数据科学实验室（HKUDS, The University of Hong Kong） |
| **官网** | [clianything.org](https://clianything.org/)（含 [中文页](https://clianything.org/zh)） |
| **形态** | 开源项目（GitHub 约 9k+ stars），无单独论文/出版物可查，以工程与文档为主 |

### 1.3 主要功能

- **一键生成 CLI**：对目标软件代码库执行一条命令（如 `/cli-anything ./gimp`），跑完 7 阶段流水线，产出可安装的 Python CLI 包。
- **替代脆弱 GUI 自动化**：不依赖截图、像素点击或 RPA；通过分析源码把「GUI 操作」映射到「底层 API」，再暴露为 CLI，直接调用真实应用后端（如 Blender 真实渲染、LibreOffice 真实生成 PDF）。
- **Agent 友好**：每条命令支持 `--help`（自描述）、`--json`（机器可读），便于 Agent 发现能力并解析结果；可组合成多步工作流。
- **质量保障**：流水线内含测试规划、测试实现与文档更新；官方称 1,436+ 测试、9～11 个应用、100% 通过率（含真实后端验证）。

### 1.4 使用场景

- **创意与媒体**：GIMP、Blender、Inkscape、Audacity、Kdenlive、Shotcut、OBS Studio 等。
- **AI/ML 平台**：Stable Diffusion、ComfyUI、InvokeAI 等。
- **数据与办公**：JupyterLab、Superset、Metabase、LibreOffice、GitLab 等。
- **开发与运维**：Jenkins、Gitea、Portainer、pgAdmin、SonarQube 等。
- **图表与可视化**：Draw.io、Mermaid、PlantUML、Excalidraw 等。

**前提**：目标软件需有可分析的**源代码**，且运行 CLI 的机器上需**已安装**该软件（CLI 调用的是真实后端，不是模拟）。

---

## 2. 技术形态与交互方式

### 2.1 技术形态归纳

| 维度 | 说明 |
|------|------|
| **本质** | **CLI 生成器 + 插件/技能**：不是「一个通用 CLI」，而是「根据目标软件代码库自动生成该软件的 CLI」的工具链。 |
| **产出物** | 每个目标软件对应一个独立 Python 包（如 `cli-anything-gimp`、`cli-anything-blender`），基于 Click 实现，可 `pip install -e .` 装到 PATH。 |
| **运行形态** | 作为 **Claude Code 插件** 或 **OpenCode/Codex/Qodercli 等平台的命令/技能** 使用；生成的 CLI 则可在任意能执行 shell 的环境中运行。 |

因此：**CLI-Anything 是「为 Agent 生成 CLI 的自动化流水线 + 多平台插件」**，生成的才是「CLI 工具」；其本身更接近「Agent 工具链/插件」，而非单一 CLI 可执行文件。

### 2.2 与用户/系统的交互方式

- **自然语言**：用户通过 Agent（Claude Code、Cursor 等）用自然语言描述任务，由 Agent 决定调用哪些生成的 CLI 命令及参数；CLI-Anything 本身不直接解析自然语言，而是提供「可被 Agent 发现和调用的 CLI」。
- **脚本/Shell**：生成的 CLI 支持子命令、选项、`--json`、REPL 模式，可直接在脚本或终端中调用，例如：
  - `cli-anything-gimp project new --width 1920 --height 1080 -o poster.json`
  - `cli-anything-gimp --json layer add -n "Background" --type solid --color "#1a1a2e"`
- **API**：无独立 HTTP/RPC API；与其它系统的集成依赖「在宿主环境执行生成的 CLI」或 Agent 框架的 shell 执行能力（如 Cursor、OpenClaw、nanobot 等）。

**小结**：交互路径为 **人/系统 → Agent → 生成的 CLI（shell）→ 目标软件后端**；核心接口是 **结构化命令行 + JSON 输出**，而非直接 API 或图形界面。

---

## 3. 是否支持或可用于移动端

### 3.1 官方对移动端的支持

- **结论**：**官方未声明支持 Android、iOS 或任何移动端/移动自动化**。
- 文档与官网均以桌面/服务器软件为主（GIMP、Blender、LibreOffice、Jenkins、JupyterLab 等），运行与测试环境为桌面/服务器；未提及 Appium、UIAutomator、ADB、XCUITest 或移动端 MCP/Agent。

### 3.2 社区/第三方与移动端结合的案例

- **结论**：**未检索到将 CLI-Anything 与 Appium、UIAutomator、ADB 或移动端 MCP/Agent 直接结合的成熟案例或讨论。**
- CLI-Anything 的输入是「软件源代码」与「已安装的桌面/服务器应用」，与移动端常见的「设备 + UI 树 + 自动化驱动」不同，因此现成集成较少。

### 3.3 核心思路向移动端迁移的可行性

- **核心思路**：  
  - 从**源码/接口**分析出「可执行能力」；  
  - 设计**结构化、可发现、可组合**的接口（CLI 子命令 + `--help` + `--json`）；  
  - 用**确定性、可测试**的调用替代不稳定的 GUI 点击。  

- **迁移到移动端的对应关系**：

| 桌面/CLI-Anything | 移动端可类比 |
|-------------------|--------------|
| 源码中的 GUI 动作 → API 映射 | 应用 UI 树/可访问性树 → 可执行操作列表（点击、输入、滑动等） |
| 生成的 CLI 命令 + 参数 | 「页面/屏幕 ID + 操作 ID + 参数」或「意图 + 槽位」 |
| `--help` / `--json` | 页面/操作的 schema 或自描述清单（供 Agent 发现） |
| 调用真实应用后端 | 通过 Appium/UIAutomator/ADB 或系统 API 执行真实操作 |

- **迁移时的主要适配点**：
  1. **输入**：移动端通常没有「目标 App 的完整源码」可用，需改为基于 **UI 层级/可访问性树** 或 **已有契约（如 MCP 工具描述、App 的 deeplink/schema）** 来生成「结构化操作列表」。
  2. **执行层**：需对接 **ADB/Appium/UIAutomator（Android）** 或 **XCUITest/Appium（iOS）**，而不是本地进程调用桌面应用。
  3. **状态与会话**：移动端多「页面/屏幕」、多任务，需要类似「当前页面 ID」「可执行操作列表」的抽象，并与导航（含 deeplink）结合。
  4. **流水线**：若要做「自动生成」，需把「Analyze」从「扫源码」改为「扫 UI 树或契约」；「Implement」从「生成 Click CLI」改为「生成 Agent 可调用的动作 schema + 与移动自动化驱动的绑定」。

**结论**：CLI-Anything 本身**不面向移动端**，但其「结构化描述 + 可执行操作 + 自描述接口」的思路**可以迁移**到移动端；迁移需要替换「源码分析 + 本地进程」为「UI/契约分析 + 移动自动化驱动」，并引入「页面/屏幕」与「操作列表」的抽象。

---

## 4. 与「页面结构化描述」「固定操作路径」「deeplink/schema」的关联

### 4.1 CLI-Anything 中的「描述」与「操作」

- **有**：  
  - **可执行操作**：通过生成的 CLI 子命令和参数表达；每个命令对应一类「可执行动作」。  
  - **结构化描述**：`--help` 提供人类可读说明，`--json` 提供机器可读输出；命令结构（子命令树、参数）即一种「结构化能力描述」。  

- **没有**：  
  - **显式的「页面/界面」概念**：CLI-Anything 不建模「当前在哪一屏」「这一屏有哪些可点击元素」；它建模的是「软件能力 → 命令」，偏「功能维度」而非「界面维度」。  
  - **固定操作路径**：不预设「步骤 1→2→3」的固定流程；由 Agent 或用户按需组合命令。  
  - **deeplink / URL schema**：不涉及 deeplink 或 app link；面向桌面/服务器应用，无「通过 URI 跳转到某屏」的语义。

### 4.2 与移动端概念的对照

| 移动端常见概念 | CLI-Anything 中的对应 | 说明 |
|----------------|------------------------|------|
| **页面 ID + 可执行操作列表** | 命令组 + 子命令 + 参数 | 移动端「某页上的按钮/输入框」可类比为「某命令下的子命令/参数」；CLI-Anything 未按「页面」组织，而是按「功能/命令」组织。 |
| **固定操作路径（流程）** | 无直接对应 | 多步流程由 Agent 或脚本多次调用 CLI 组合而成，而非内置「流程定义」。 |
| **deeplink / schema 跳转** | 无 | 桌面场景无此概念；若迁移到移动端，可**新增**「deeplink/schema」作为一类可执行操作（导航），与「点击」「输入」等并列。 |

### 4.3 对移动端方案设计的启示

- 若要做「移动端版 CLI-Anything」或「Agent 可用的移动操作层」：
  - **页面结构化描述**：可引入「屏幕/页面 ID + 当前可见可操作元素列表」的 schema，供 Agent 发现「当前能做什么」。
  - **可执行操作列表**：可借鉴 CLI-Anything 的「自描述 + 结构化输出」：每个操作有名称、参数、说明，并支持类似 `--json` 的机器可读格式。
  - **deeplink/schema**：可作为「导航类操作」纳入同一套描述与执行框架（例如「打开某 deeplink」视为一种原子操作），与 UI 点击、输入等一起组成「可执行动作」集合。
  - **自动化脚本生成**：CLI-Anything 的「分析 → 设计 → 实现」流水线，在移动端可对应为「从 UI 树或契约生成「页面+操作」schema → 生成与 Appium/ADB 等绑定的调用接口」，用于生成或驱动自动化脚本/Agent 工具。

---

## 5. 总结与要点

- **CLI-Anything**：香港大学 HKUDS 的开源项目，用一条命令为任意有源码的软件生成「Agent 可用的 CLI」，替代脆弱 GUI 自动化；官网 [clianything.org](https://clianything.org/)，仓库 [github.com/HKUDS/CLI-Anything](https://github.com/HKUDS/CLI-Anything)。
- **技术形态**：CLI **生成**流水线 + 多平台插件（Claude Code / OpenCode / Codex 等）；产出物为按软件划分的 Click CLI 包，通过 **shell + --help + --json** 与 Agent/脚本交互。
- **移动端**：**官方不支持**；**未见与 Appium/UIAutomator/ADB/移动 MCP 的结合案例**；其「结构化、可发现、可执行」的思路**可迁移**到移动端，但需改为基于 UI/契约 + 移动自动化驱动，并引入「页面 + 操作列表」及可选的 deeplink。
- **与页面/操作/deeplink 的关系**：CLI-Anything 提供的是「命令级」结构化描述与可执行操作，**无页面 ID、无固定流程、无 deeplink**；移动端方案可在此基础上**增加**页面抽象与 deeplink 作为导航类操作，并与「页面 ID + 可执行操作列表」统一建模。

---

*报告基于公开文档与检索结果整理，未对 CLI-Anything 源码做逐行分析；若需与产品方案深度对照，建议结合仓库中的 HARNESS.md、流水线阶段说明及官方文档进一步细读。*
