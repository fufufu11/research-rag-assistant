---
name: "setup-matt-pocock-skills"
description: "为 research-rag-assistant 配置 engineering skills 基础设施——GitHub MCP issue tracker、triage 标签、docs/adr/ + CONTEXT.md 位置。首次使用前运行一次。"
---

# Setup Engineering Skills（research-rag-assistant 适配版）

> 适配自 mattpocock/skills 的 setup-matt-pocock-skills，针对本项目（research-rag-assistant）定制。

## 本项目已有配置（无需重新配置）

本项目已通过 handoff 文档与 project_memory.md 完成大部分配置，本 skill 主要做验证与补充：

- **Issue tracker**：GitHub（远端 `fufufu11/research-rag-assistant`），通过 MCP `mcp_plugin_GitHub_github` 调用
- **Domain docs**：`docs/adr/`（已有 ADR-0001/0002）+ `CONTEXT.md`（用户反馈闭环阶段 10.2 已建）
- **Workflow**：见 `docs/ROADMAP.md` + `c:\Users\25831\.trae-cn\memory\projects\-d-CODE-research-rag-assistant\project_memory.md`

## 已有 artifacts（不复制，只引用）

- 路线图：[docs/ROADMAP.md](file:///d:/CODE/research-rag-assistant/docs/ROADMAP.md)
- 状态：[docs/STATUS.md](file:///d:/CODE/research-rag-assistant/docs/STATUS.md)
- ADR：`docs/adr/0001-request-id-as-feedback-key.md`、`docs/adr/0002-retrieval-stage-p95-metric.md`
- 术语表：`CONTEXT.md`
- Project memory：`c:\Users\25831\.trae-cn\memory\projects\-d-CODE-research-rag-assistant\project_memory.md`
- Issue 模板参考：Issue #12 / #74 / #76 / #81（目标/依据/验收/不在范围/技术选型/设计取舍/关联结构）

## Process

### 1. 验证已有配置

读以下文件确认配置完整：

- `git remote -v` — 应为 `fufufu11/research-rag-assistant`
- `docs/ROADMAP.md` — 确认当前阶段
- `docs/adr/` — 列出已有 ADR
- `CONTEXT.md` — 确认术语表存在
- `c:\Users\25831\.trae-cn\memory\projects\-d-CODE-research-rag-assistant\project_memory.md` — 确认 hard constraints 与 engineering conventions

### 2. 写 `docs/agents/issue-tracker.md`

记录 GitHub MCP 调用方式：

```markdown
# Issue Tracker 配置

## Tracker
GitHub Issues（远端 fufufu11/research-rag-assistant）

## 调用方式
通过 TRAE MCP `run_mcp` 调用 `mcp_plugin_GitHub_github`：

- `list_issues` — 列出 issues（查重必用）
- `issue_read` — 读 issue 详情
- `issue_write` — 创建/更新 issue
- `add_issue_comment` — 加 comment
- `create_pull_request` — 创建 PR
- `pull_request_read`（method=`get_check_runs`）— 读 PR + CI 状态
- `merge_pull_request` — 合并 PR（squash）

## 调用前必读
读对应 schema：`c:\Users\25831\.trae-cn\mcps\s_research-rag-assistant-997aced1\solo_agent_lite\mcp_plugin_GitHub_github\tools\<tool>.json`

## 授权
首次调用前用 `RequestAuthorization`，service=`trae-remote-official:github::github`，scopes=`[]`

## Issue 模板
参考 Issue #12 / #74 / #76 / #81 结构：
- 目标
- 依据
- 验收标准
- 不在范围
- 技术选型
- 设计取舍
- 关联

## PR 规范
- 描述含 `Closes #<issue-number>`
- branch 命名：`feat/` / `fix/` / `chore/` 前缀
- 合并方式：squash merge
- CI 必须三项全绿（Lint / Type Check / Test）
```

### 3. 写 `docs/agents/triage-labels.md`（若安装了 triage skill）

本项目当前未启用 triage 状态机，默认 labels：

```markdown
# Triage Label 词汇

## 默认（未启用状态机）
本项目用 GitHub 原生 label：`bug`、`enhancement`、`documentation`、`chore` 等

## 若启用 triage 状态机（可选）
5 个标准角色（label 字符串 = 角色名）：
- `needs-triage`
- `needs-info`
- `ready-for-agent`
- `ready-for-human`
- `wontfix`
```

### 4. 写 `docs/agents/domain.md`

```markdown
# Domain Docs 配置

## 布局
single-context（非 monorepo）

## 文件位置
- 术语表：`CONTEXT.md`（仓库根）
- ADR：`docs/adr/`
- 路线图：`docs/ROADMAP.md`
- 状态：`docs/STATUS.md`

## 阅读规则
- 任何 skill 触及代码库前先读 `CONTEXT.md` 了解 domain 词汇
- 触及某区域前读相关 ADR，不要 re-litigate 已记录决策
- ADR 冲突时只在 friction 足够 real 到 warrant revisit 时 surface
```

### 5. Done

告诉用户 setup 完成。后续 engineering skills（`to-spec` / `to-tickets` / `triage` / `wayfinder` / `implement`）会从 `docs/agents/*.md` 读取配置。

## 不在范围

- 安装新 MCP（GitHub MCP 已就绪）
- 创建 `CLAUDE.md` / `AGENTS.md`（本项目用 `project_memory.md` 替代）
- monorepo multi-context 配置（本项目是单仓库）
