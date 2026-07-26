---
name: "handoff"
description: "把当前会话压缩成交接文档，让另一个 agent 接手继续工作。当用户想结束当前会话、切换 context、或把工作移交给新会话时调用。"
---

# Handoff

> 适配自 mattpocock/skills 的 handoff skill，适配本项目的中文工作流与 artifacts 约定。

## 核心理念

写一份交接文档，总结当前会话的关键信息，让一个**全新的 agent**（无上下文）能继续工作。文档是「指针地图」，不是「内容副本」——通过路径/URL 引用已有 artifacts，不复制内容。

## 流程

### 1. 确定下一会话焦点

如果用户传了参数（如 "继续阶段 11.5"、"修 upload bug"），把参数当作下一会话的关注点，相应调整文档重点。

如果没传参数，按当前会话的自然延续方向写。

### 2. 收集关键信息

从对话中提取：

- **项目**：仓库路径、远端、技术栈一行
- **进度**：当前分支、最近 commit、已完成阶段、测试数、CI 状态
- **下一步**：按优先级列出剩余任务，标注依赖与前置完成状态
- **工作流程**：读哪些 docs、用哪些 MCP 工具、分支命名约定、PR 流程
- **硬约束**：包管理器、Python 版本、CI 要求、API key 处理、分支前缀、行结尾
- **已有模块**：本会话涉及或下一会话需对接的模块路径与功能
- **本地环境**：docker 路径、服务启动命令、必设环境变量、外部依赖
- **关键文件**：路线图、API app、路由、安全模块、测试目录、CI 配置
- **可用 Skills**：项目内 `.trae/skills/` 下的 skills
- **MCP**：调用的 MCP server 与工具名
- **经验教训**：PowerShell 语法限制、CI 轮询时序、测试隔离技巧等非显然事项

### 3. 不重复已有 artifacts

不要复制以下 artifacts 的内容，**只引用路径或 URL**：

- specs / plans / PRD
- ADRs（`docs/adr/`）
- GitHub Issues / PRs（用 URL 或 `#<number>` 引用）
- commits / diffs（用 SHA 或 PR 号引用）
- `docs/ROADMAP.md` / `docs/STATUS.md`（用路径引用）
- `CONTEXT.md` 术语表
- 评测报告

只在交接文档里写**当前会话特有的、不在以上 artifacts 中的**信息。

### 4. 脱敏

脱敏任何敏感信息：

- API key / Secret / Token → 写 `<见 .env>` 或 `<已脱敏>`
- 密码 → 写 `<已脱敏>`
- 个人身份信息（PII）→ 写 `<已脱敏>`

### 5. 包含「建议的 Skills」章节

根据下一会话的工作类型，建议应调用的 skills：

| 工作类型 | 建议 Skill |
|---|---|
| 编码前澄清需求 / 设计访谈 | `grill-with-docs` |
| 把 spec 拆成可执行 ticket | `to-tickets` |
| TDD 红绿重构 | `tdd` |
| 审查分支/PR diff | `code-review` |
| 安全最佳实践审查 | `security-best-practices` |
| 创建新 skill | `skill-creator` |

### 6. 保存到 `.trae/handoffs/`

**不污染 git 仓库**：保存到 `.trae/handoffs/handoff-<YYYYMMDD-HHMM>.md`（已在 `.gitignore` 中忽略）。

> 原版 mattpocock handoff skill 设计为保存到 OS 临时目录（`$env:TEMP` / `/tmp`），但 TRAE 的 `Write` 工具限制在 working directory 内。本项目适配为保存到 `.trae/handoffs/`，与 `.trae/skills/` 同级，已在 `.gitignore` 中忽略。

用 `Write` 工具直接写文件，文件名带时间戳（`Get-Date -Format "yyyyMMdd-HHmm"`）避免覆盖。

### 7. 输出文档结构

```markdown
# <项目名> 后续任务交接

## 项目
<仓库路径、远端、技术栈一行>

## 进度
<当前分支、最近 commit、已完成阶段、测试数、CI 状态>

## 下一步（按优先级）
| 任务 | 优先级 | 依赖 |
|---|---|---|
| **<任务名>** | <高/中/低> | <依赖状态> |

**建议从 <任务> 开始**（<原因>）。

## 工作流程
1. <步骤 1>
2. <步骤 2>
...

## 硬约束
- <约束 1>
- <约束 2>

## 已有模块（如对接需要）
- `<路径>`：<功能描述>

## 本地环境
- <工具路径 / 启动命令 / 必设环境变量>

## 关键文件
- <文件>：<用途>

## 可用 Skills
<项目内 skills 列表>

## MCP（如使用）
通过 `run_mcp` 调 `<server>`：<工具列表>

## 经验教训
- <本会话学到的非显然事项>

## 建议 Skills（下一会话）
基于下一会话的焦点，建议优先调用：
- `<skill-name>`：<原因>
```

## 与本项目的适配说明

- **artifacts 引用**：`docs/ROADMAP.md` / `docs/STATUS.md` / `CONTEXT.md` / `docs/adr/` 已存在，用路径引用，不复制内容
- **GitHub 引用**：commit 用 SHA，PR/Issue 用 `#<number>` 或 URL
- **Skills 位置**：`.trae/skills/{code-review,grill-with-docs,tdd,to-tickets,handoff}/`
- **交接文档位置**：`.trae/handoffs/handoff-<YYYYMMDD-HHMM>.md`（已在 `.gitignore` 中忽略）
- **脱敏**：API key 用 `<见 .env>`，不写真实值
- **语言**：交接文档用中文（与项目工作语言一致）

## 何时不调用

- 简单任务（一两个 step 完成）：无需交接文档
- 用户明确说「不用交接」：尊重用户
- 当前会话已无后续工作：不需要为空会话生成文档
