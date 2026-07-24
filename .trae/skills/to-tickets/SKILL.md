---
name: "to-tickets"
description: "把计划/spec/对话拆成 tracer-bullet ticket（垂直切片+阻塞依赖），发布到 GitHub Issues。当用户想把 spec 拆成可执行 ticket、或需要结构化拆解大任务时调用。"
---

# To Tickets

> 适配自 mattpocock/skills 的 to-tickets skill，去掉 setup 依赖，适配本项目 GitHub Issues 工作流。

## 核心理念

把一个计划、spec、或当前对话拆解成一组** ticket**——tracer-bullet 垂直切片，每个声明**阻塞**它的其他 ticket。

## 流程

### 1. 收集上下文

从对话已有上下文工作。如果用户传了一个引用（spec 路径、issue 号或 URL）作为参数，抓取并读取完整正文和评论。

用 GitHub MCP 工具（`run_mcp` 调 `issue_read`）或 `gh issue view <number>` 获取 issue 内容。

### 2. 探索代码库（可选）

如果你还没探索过代码库，做一下以理解代码当前状态。Ticket 标题和描述应该使用项目的领域术语表词汇（见 `CONTEXT.md`），并尊重你触碰区域的 ADR。

寻找 prefactor 代码以让实现更容易的机会。"让变更变容易，再做容易的变更。"

### 3. 起草垂直切片

把工作拆成** tracer bullet** ticket。

<vertical-slice-rules>
- 每个切片切一条窄但**完整**的路径，穿过每一层（schema、API、UI、tests）——垂直，**不是**单层的水平切片
- 完成的切片自身可演示或可验证
- 每个切片大小适合单个全新 context window
- 任何 prefactoring 应该先做
</vertical-slice-rules>

给每个 ticket 它的**阻塞边**——必须完成才能开始的其他 ticket。没有阻塞的 ticket 可以立即开始。

**宽重构是垂直切片的例外。** 一个**宽重构**是一个机械变更——重命名一列、重新类型化一个共享符号——其**爆炸半径**扇开到整个代码库，单一编辑一次破坏数千个调用点，没有垂直切片能落地绿。不要强塞进 tracer bullet；按**expand–contract**排序。先 expand：在旧的旁边加新形式，什么都不破坏。然后按爆炸半径分批迁移调用点（按包、按目录），每批是自己的 ticket，被 expand 阻塞，保持 CI 逐批绿因为旧形式还在。最后 contract：没有调用者剩了就删旧形式，在一个被每个迁移批阻塞的 ticket 里。当连批次都不能单独保持绿时，保持顺序但让它们共享一个集成分支，全部阻塞一个最终的 integrate-and-verify ticket——绿只在那里承诺。

### 4. 测验用户

把提议的拆解作为编号列表呈现。每个 ticket 显示：
- **Title**：简短描述性名字
- **Blocked by**：哪些其他 ticket（如有）必须先完成
- **What it delivers**：这个 ticket 让工作的端到端行为

问用户：
- 粒度感觉对吗？（太粗 / 太细）
- 阻塞边对吗——每个 ticket 只依赖真正门控它的 ticket 吗？
- 应该合并或进一步拆分任何 ticket 吗？

迭代直到用户批准拆解。

### 5. 发布 ticket 到 GitHub Issues

本项目用 GitHub Issues 作为 tracker。用 GitHub MCP 工具（`run_mcp` 调 `issue_write`）或 `gh issue create` 发布。

按依赖顺序发布（阻塞者先），这样每个 ticket 的阻塞边能引用真实标识符。用 GitHub 原生的 blocking / sub-issue 关系如果有的话；否则在每个 ticket 的 "Blocked by" 部分设置阻塞 issue 引用。

Issue 模板：

<issue-template>
## Parent
对 tracker 上 parent issue 的引用（如果源是已有 issue，否则省略此节）。

## What to build
这个 ticket 让工作的端到端行为，从用户视角——不是逐层实现。

## Acceptance criteria
- [ ] 标准 1
- [ ] 标准 2

## Blocked by
- 对每个阻塞 ticket 的引用，或"None — can start immediately"。
</issue-template>

避免特定文件路径或代码片段——它们很快过时。例外：如果一个 prototype 产出了比散文更精确编码决策的片段（状态机、reducer、schema、类型形状），内联它并简要注明来自 prototype。修剪到决策密集部分——不是工作 demo，只是重要 bits。

## 与本项目的适配说明

- issue tracker：GitHub Issues（仓库 fufufu11/research-rag-assistant）
- issue 创建：用 `run_mcp` 调 `issue_write` MCP 工具，或 `gh issue create` 命令
- issue 引用：commit 消息和 PR 描述用 `Closes #<issue-number>` 自动关闭
- Issue 模板：遵循 Issue #12 结构（目标/依据/验收标准/不在范围/技术选型/设计取舍/关联）
- 分支命名：`feat/` 前缀新功能，`fix/` 前缀 bug 修复，`chore/` 前缀维护
- 领域术语：见 `CONTEXT.md`（如存在）或 `docs/ROADMAP.md`
- 已有工作流：先建 Issue → 开分支 → 实现 → 本地质量检查 → 推送 → 开 PR → 轮询 CI 至全绿 → 合并
