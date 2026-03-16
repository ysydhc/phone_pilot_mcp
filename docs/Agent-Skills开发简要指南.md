# Agent Skills 开发简要指南

> 面向「想写一个让 Agent 更好使用 phone_pilot 的 Skill」的开发者。以 Cursor 的 Agent Skills 为主，与 Claude skill-creator 思路兼容。

---

## 1. Skill 是什么、何时用

- **Skill**：一个**独立的能力包**，用 Markdown（+ 可选资源）教 Agent 在特定场景下怎么做——例如「用 phone_pilot 做移动自动化时，先 get_page_state 再 tap_element」。
- **何时用**：用户/任务涉及「控制手机」「phone_pilot」「phone_*」「自动化脚本」等时，Agent 会通过 **description** 匹配到你的 Skill，再加载其中的步骤与约定。

---

## 2. 你需要掌握的知识点

| 知识点 | 说明 |
|--------|------|
| **存放位置** | 项目内：`.cursor/skills/<skill-name>/`；个人全局：`~/.cursor/skills/<skill-name>/`。不要放到 `~/.cursor/skills-cursor/`（系统保留）。 |
| **必须文件** | 每个 Skill 一个目录，里面至少有一个 **SKILL.md**。 |
| **SKILL.md 结构** | 顶部 **YAML frontmatter**（`name`、`description`）+ 正文 Markdown（步骤、示例、注意点）。 |
| **name** | 小写、连字符、≤64 字符，如 `phone-pilot-automation`。 |
| **description** | ≤1024 字符；**第三人称**；同时写清 **做什么（WHAT）** 和 **什么时候用（WHEN）**，便于 Agent 自动选用。 |
| **正文** | 简洁、步骤化；SKILL.md 建议 <500 行；细节可放到同目录下的 reference.md、examples.md，在 SKILL 里用链接引用（渐进披露）。 |
| **可选资源** | `reference.md`（详细说明）、`examples.md`（示例）、`scripts/`（可执行脚本），按需添加。 |

---

## 3. description 怎么写（决定能否被选中）

Agent 主要靠 **description** 决定是否加载该 Skill，所以要写好「做什么 + 何时用」并带上**触发词**。

- **第三人称**：写「Guides the agent to…」/「帮助 Agent…」，不要「I can…」/「You can…」。
- **WHAT**：能力一句话，例如「通过 phone_pilot MCP 控制 Android，先 get_page_state 再 tap_element」。
- **WHEN**：列出典型触发场景，例如「用户要自动化手机、控制手机、使用 phone_* 工具、获取页面状态、按元素点击、打开 deeplink、在 phone_touch 项目里工作」等。

示例（与本项目 Skill 一致）：

```yaml
description: Guides the agent to control Android (and optionally HarmonyOS) devices via phone_pilot MCP tools. Use when the user asks to automate a mobile app, control a phone, run UI automation, use phone_* tools, get page state, tap elements, open deeplinks, run scripts on device, or when working in the phone_touch/phone_pilot project.
```

---

## 4. 本仓库已提供的 Skill

项目中已有一个供 Agent 使用的 Skill：

- **路径**：`.cursor/skills/phone-pilot-automation/`
- **SKILL.md**：约定「先 get_page_state（可选 annotate_elements）→ 用 tap_element(index) 点击、用 open_deeplink 跳转」等核心工作流，以及 device_serial、script_api、录屏/验证等注意点。
- **reference.md**：按类别列出的 `phone_*` 工具速查，需要时再查。

这样 Agent 在「控制手机」「用 phone_pilot」「写自动化脚本」等场景下会优先按该 Skill 的流程使用 MCP 工具。

---

## 5. 如何继续学习 / 扩展

- **Cursor 规范**：阅读 `~/.cursor/skills-cursor/create-skill/SKILL.md`（若存在），里面有 SKILL 结构、description 写法、渐进披露、反模式等。
- **本仓库**：直接看 `.cursor/skills/phone-pilot-automation/SKILL.md` 和 `reference.md` 作为范例；要扩展「更好使用 phone_pilot」时，在同一 Skill 里补步骤或加 `reference.md`/`examples.md` 即可。
- **验证**：在 Cursor 里提与「用 phone_pilot 点一下设置」「根据当前页面生成一段自动化脚本」相关的问题，看 Agent 是否按 Skill 先 get_page_state、再 tap_element 或生成 script_api 脚本。

---

## 6. 小结

- **要掌握**：Skill = 目录 + SKILL.md（name + description + 正文）；description 写清 WHAT+WHEN、第三人称、触发词；正文简洁、可链到 reference/examples。
- **要创建「让 Agent 更好用 phone_pilot」的 Skill**：已在本项目中加好 `phone-pilot-automation`，可直接用并在此基础上按需增改步骤或工具说明。

如需把 Skill 复制到个人目录以便在所有项目里用，可复制 `.cursor/skills/phone-pilot-automation/` 到 `~/.cursor/skills/phone-pilot-automation/`。
