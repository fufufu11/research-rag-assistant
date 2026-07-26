---
name: "triage"
description: "把 GitHub Issues 通过 triage 角色状态机移动——分类、验证、必要时 grill、写 agent-ready briefs。"
---

# Triage（research-rag-assistant 适配版）

> 适配自 mattpocock/skills 的 triage，针对本项目 MCP GitHub + GitHub 原生 label 定制。

## 本项目适配

- **Issue tracker**：GitHub（远端 `fufufu11/research-rag-assistant`）
- **调用方式**：MCP `run_mcp` 调 `mcp_plugin_GitHub_github`
- **labels**：本项目当前用 GitHub 原生 label（`bug` / `enhancement` / `documentation` / `chore`），未启用 triage 状态机的 5 个标准角色
- **disclaimer**：每条 triage comment 以 `> *AI triage 生成*` 开头
- **`.out-of-scope/` 知识库**：本项目未建立，prior rejection 检查改为查询 closed issues

## 调用前必读

读 schema：`c:\Users\25831\.trae-cn\mcps\s_research-rag-assistant-997aced1\solo_agent_lite\mcp_plugin_GitHub_github\tools\<tool>.json`

涉及工具：`list_issues` / `issue_read` / `issue_write` / `add_issue_comment`

## Roles

两个 **category** 角色：

- `bug` — 东西坏了
- `enhancement` — 新功能或改进

五个 **state** 角色（可选启用，当前未启用）：

- `needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`

## Invocation

用户调 `/triage` 并用自然语言描述：

- "看看有什么需要注意的"
- "看看 #42"（issue）
- "把 #42 移到 ready-for-agent"（若启用状态机）
- "哪些 ready 给 agent 做"

## Show what needs attention

调 `list_issues` 查询，呈现三个 bucket（oldest first）：

1. **Unlabeled** — 从未 triaged
2. **`needs-triage`**（若启用）— evaluation 进行中
3. **`needs-info` with reporter activity** — 需 re-evaluation

显示 counts 与每项一行 summary。

## Triage a specific issue

1. **Gather context** — `issue_read` 读 body/comments/labels。用 `Grep`/`Read` 探索代码库，尊重 `docs/adr/`。两个检查：(a) redundancy — 按域概念搜现有实现；(b) prior rejection — 查 closed issues 找相似 request。

2. **Recommend** — 告诉用户 category 与 state 推荐 + 理由。等 direction。

3. **Verify the claim** — Bug 从 reporter 步骤 reproduce（用 `RunCommand` 或 `pytest`）；PR `pull_request_read` + 读 diff + 跑相关测试。

4. **Grill (if needed)** — 用 `grill-with-docs` skill fleshing out。

5. **Apply outcome**：
   - `ready-for-agent` — `add_issue_comment` post agent brief
   - `ready-for-human` — 同 brief 结构，note 为什么不能 delegate
   - `needs-info` — post triage notes
   - `wontfix` — `issue_write` close，comment 取决于原因（已实现/rejected bug/rejected enhancement）

## Agent brief 模板（适配本项目）

```markdown
> *AI triage 生成*

## Agent Brief

### 任务
<issue 的核心目标>

### 接缝
<测试接缝位置，参考 tests/unit/ 风格>

### 上下文
- 相关文件：<paths>
- 相关 ADR：<docs/adr/000X-xxx.md>
- 依赖：<前置 issue 或阶段>

### 验收
- <可验证条件>
- CI 三项全绿

### 不在范围
- <明确排除>
```

## Needs-info 模板

```markdown
> *AI triage 生成*

## Triage Notes

**已确认：**
- point 1
- point 2

**仍需你提供（@reporter）：**
- question 1
- question 2
```

## 不在范围

- 不直接实现 issue（由 `implement` skill 做）
- 不创建 PR（triage 只分类与 brief）
- 不 re-litigate 已有 ADR
