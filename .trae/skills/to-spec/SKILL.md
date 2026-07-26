---
name: "to-spec"
description: "把当前对话综合成 spec 并通过 MCP GitHub 发布为 Issue。不做访谈，只综合已讨论的内容。"
---

# To Spec（research-rag-assistant 适配版）

> 适配自 mattpocock/skills 的 to-spec，针对本项目 MCP GitHub + docs/adr/ + Issue 模板定制。

把当前对话上下文与代码库理解综合成 spec，通过 MCP GitHub 发布为 Issue。**不做访谈** — 只综合已有信息。

## Process

1. **探索代码库** — 用 `Grep`/`Glob`/`Read` 工具理解当前状态。使用 `CONTEXT.md` 的 domain 词汇贯穿 spec，尊重 `docs/adr/` 中触及区域的 ADR。

2. **草拟测试接缝** — 已有接缝优于新接缝。本项目测试在 `tests/unit/`，参考已有测试风格（纯函数 + 路由集成分离）。

   与用户确认接缝符合预期。

3. **用项目模板写 spec**，通过 MCP GitHub 发布为 Issue。**不应用 triage label**（本项目未启用 triage 状态机，直接用 `enhancement` label）。

## Spec 模板（适配本项目 Issue #12/#74/#76/#81 结构）

```markdown
## 目标
<用户面临的问题，从用户视角>

## 依据
<为什么现在做这个；与 docs/ROADMAP.md 哪个阶段对应>

## 验收标准
- <可验证的条件 1>
- <可验证的条件 2>
- CI 三项全绿（Lint / Type Check / Test）

## 不在范围
- <明确排除的事项>

## 技术选型
- <方案选择>
- <依赖库/工具>

## 设计取舍
- <决策 1>：为什么
- <决策 2>：为什么不

## 关联
- 路线图：docs/ROADMAP.md 阶段 X.Y
- 依赖 Issue：#<number>（如适用）
- 相关 ADR：docs/adr/000X-xxx.md（如适用）
```

## MCP GitHub 调用

调用前先 `Read` schema：`c:\Users\25831\.trae-cn\mcps\s_research-rag-assistant-997aced1\solo_agent_lite\mcp_plugin_GitHub_github\tools\issue_write.json`

调用 `issue_write` 创建 Issue：

```python
run_mcp(
    server_name="mcp_plugin_GitHub_github",
    tool_name="issue_write",
    args={
        "owner": "fufufu11",
        "repo": "research-rag-assistant",
        "title": "<spec 标题>",
        "body": "<spec markdown 内容>",
        "labels": ["enhancement"]
    }
)
```

## 不在范围

- 不创建 PR（由 `implement` skill 后续做）
- 不写代码（只写 spec）
- 不访谈用户（只综合已讨论内容）
