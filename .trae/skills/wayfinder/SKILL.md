---
name: "wayfinder"
description: "为本项目超出单会话容量的大块工作做规划——在 GitHub Issues 上建立决策 ticket 的地图，逐一解决直到路径清晰。"
---

# Wayfinder（research-rag-assistant 适配版）

> 适配自 mattpocock/skills 的 wayfinder，针对本项目 MCP GitHub + docs/ROADMAP.md 定制。

## 适用场景

当 `docs/ROADMAP.md` 中的某个阶段（如阶段 12 表格感知切分、用户注册登录系统 + JWT）太大、不确定因素太多，无法一个会话完成时使用。先用 wayfinder 把路径 chart 出来，再逐 ticket 推进。

## 本项目适配

- **Map 与 tickets**：GitHub Issues（远端 `fufufu11/research-rag-assistant`）
- **调用方式**：MCP `run_mcp` 调 `mcp_plugin_GitHub_github` 的 `issue_write` / `issue_read` / `list_issues`
- **label**：`wayfinder-map`（map issue）/ `wayfinder-research` / `wayfinder-prototype` / `wayfinder-grilling` / `wayfinder-task`（GitHub label 不能含冒号，用连字符替代原版 `wayfinder:map`）
- **blocking**：GitHub 不支持 native issue dependency，用 body 中 `Blocks: #<n>` / `Blocked by: #<n>` 文本约定
- **child issues**：GitHub 不支持原生 sub-issue，用 body 中 `Map: #<map-number>` 引用

## 流程

### Chart the map

1. **Name the destination** — 用 `grill-with-docs` skill 钉住此 map 找通往什么（如"用户注册登录系统 + JWT"）
2. **Map the frontier** — breadth-first grill，surface open decisions 与可取第一步
3. **Create the map**（label `wayfinder-map`）：body 用本项目模板
4. **Create tickets** 作为 issues，body 引用 map number；second pass wire blocking
5. **Fire research subagents** — 用 `Task` 工具 `subagent_type=search` 替代原版 `/research` subagent
6. Stop — charting 是一会话工作

### Work through the map

1. Load map（`issue_read`）
2. Choose frontier ticket，**assign 给自己** claim
3. Resolve — zoom 用 `issue_read` / `Read` docs/adr/
4. Record resolution：`add_issue_comment` + close + 更新 map 的 Decisions-so-far
5. Add newly-surfaced tickets，graduate fog

## Map body 模板（适配本项目）

```markdown
## Destination
<到达此 map 终点意味着什么>

## Notes
- 领域：research-rag-assistant（科研文献 RAG 问答系统）
- 必读：`docs/ROADMAP.md`、`docs/STATUS.md`、`CONTEXT.md`、`docs/adr/`
- 工作流：见 `c:\Users\25831\.trae-cn\memory\projects\-d-CODE-research-rag-assistant\project_memory.md`

## Decisions so far
<!-- 每个 closed ticket 一行：gist + link -->
- [ticket title](issue-url) — <一行 gist>

## Not yet specified
<!-- in-scope 但还无法 ticket 的雾 -->

## Out of scope
<!-- 超出 destination 的工作 -->
```

## Ticket body 模板

```markdown
## Question
<此 ticket 解决的决策或调查>

## Map
Map: #<map-number>

## Blocked by
<!-- 若有 blocking -->
Blocked by: #<ticket-number>

## Type
<research / prototype / grilling / task>
```

## MCP 调用示例

创建 map：

```python
run_mcp(
    server_name="mcp_plugin_GitHub_github",
    tool_name="issue_write",
    args={
        "owner": "fufufu11",
        "repo": "research-rag-assistant",
        "title": "[wayfinder] <destination 名称>",
        "body": "<map body>",
        "labels": ["wayfinder-map"]
    }
)
```

调用前先 `Read` schema：`c:\Users\25831\.trae-cn\mcps\s_research-rag-assistant-997aced1\solo_agent_lite\mcp_plugin_GitHub_github\tools\issue_write.json`

## 不在范围

- 不直接做实现工作（map 完成后用 `to-tickets` 或 `implement` 接手）
- 不 re-litigate 已有 ADR
- 不创建 PR（map 只产出决策）
