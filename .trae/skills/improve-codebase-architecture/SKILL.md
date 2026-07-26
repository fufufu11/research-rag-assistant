---
name: "improve-codebase-architecture"
description: "扫描 research-rag-assistant 代码库找深化机会，作为 HTML 报告呈现，然后 grill 你选的那个。每隔几天跑一次防泥球。"
---

# Improve Codebase Architecture（research-rag-assistant 适配版）

> 适配自 mattpocock/skills 的 improve-codebase-architecture，针对本项目 docs/adr/ + CONTEXT.md + 测试结构定制。

## 本项目适配

- **HTML 报告位置**：`.trae/reports/architecture-review-<timestamp>.html`（已加 `.gitignore`）
- **subagent**：`Task` 工具 `subagent_type=search` 替代原版 `Explore`
- **open 命令**：`start <path>`（Windows）
- **`/grilling`**：本项目对应 `grill-with-docs` skill
- **`/domain-modeling` / `/codebase-design`**：本项目未安装，inline 手动维护 `CONTEXT.md` / `docs/adr/`

## Process

### 1. Explore

**Scope before you scan — YAGNI.** 给最近变更过的代码库部分额外权重：

- 用 `RunCommand` 跑 `git log --oneline -30` 找 hot spots
- 用 `Read` 读 `CONTEXT.md` 了解 domain 词汇
- 用 `Glob` 列 `docs/adr/*.md` 读已有 ADR
- 用 `Task` 工具 `subagent_type=search` 走代码库，note friction：

  - 哪里理解一个 concept 需要在许多小 modules 之间 bounce？
  - 哪里 modules shallow — interface 几乎与 implementation 一样复杂？
  - 哪里纯函数只为 testability extract，但 real bugs hide 在如何 called？
  - 哪里 tightly-coupled modules leak across seams？
  - 哪些部分 untested 或 hard 通过当前 interface test？

### 2. Present candidates as HTML report

`Write` 到 `.trae/reports/architecture-review-<timestamp>.html`：

- Tailwind via CDN
- Mermaid via CDN（graph-shaped relationships）
- hand-built divs/SVG（editorial visuals）
- 每个 candidate 一张 **before/after visualisation**

每个 candidate 渲染一张 card：

- **Files** — 哪些 files/modules 涉及（用项目实际路径如 `src/research_rag/api/`）
- **Problem** — 当前架构为什么 cause friction
- **Solution** — 会 change 什么的描述
- **Benefits** — 用 locality 和 leverage 解释，以及 tests 会如何 improve
- **Before / After diagram** — side-by-side
- **Recommendation strength** — `Strong` / `Worth exploring` / `Speculative`

以 **Top recommendation** 章节结尾。

用 `CONTEXT.md` vocabulary for domain（如 "Document"、"Chunk"、"Conversation"、"Feedback"），不用 "FooBarHandler" 这种实现细节名。

**ADR conflicts**：若 candidate 与 `docs/adr/0001` 或 `0002` 矛盾，只在 friction 足够 real 时 surface，在 card 中 mark warning。

**不要** propose interfaces 还。文件写入后，用 `RunCommand` 跑 `start <path>` 打开，问用户："Which of these would you like to explore?"

### 3. Grilling loop

用户挑 candidate 后，调 `grill-with-docs` skill walk 决策树：

- constraints、dependencies、deepened module 的形状
- seam 后面是什么、什么 tests survive
- inline 更新 `CONTEXT.md` 与 `docs/adr/`：

  - **Naming a deepened module after a concept not in `CONTEXT.md`?** Add term
  - **Sharpening a fuzzy term?** Update `CONTEXT.md` right there
  - **User rejects candidate with load-bearing reason?** Offer 写新 ADR：`docs/adr/0003-<slug>.md`，只在 reason 实际 needed by future explorer 时 offer
  - **Want to explore alternative interfaces?** 用 `Task` 工具 `subagent_type=general_purpose_task` 做 design-it-twice parallel exploration

## TRAE 适配说明

- 用 `subagent_type=search` 的 Task 工具（TRAE 命名）
- HTML 报告写到 `.trae/reports/`（项目内，已 .gitignore 忽略）
- `start` 命令打开（Windows）
- 用 `Grep`/`Glob`/`Read` 探索代码（不用 `grep`/`find`/`cat`）

## 不在范围

- 不直接做 refactor（grilling 完成后用 `to-tickets` 拆 ticket 再 `implement`）
- 不 re-litigate 已有 ADR 除非 friction 足够 real
- 不创建 PR（只产出 candidate 分析）
