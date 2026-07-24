---
name: "code-review"
description: "对 HEAD 与固定点间的 diff 做双轴并行审查：Standards（编码规范+Fowler 代码异味）和 Spec（需求符合度）。当用户想审查分支/PR/WIP 改动，或说'review since X'时调用。"
---

# Code Review

> 适配自 mattpocock/skills 的 code-review skill，适配本项目的 issue 获取方式（GitHub MCP 工具）。

## 双轴审查

对 `HEAD` 与用户指定的固定点之间的 diff 做两个维度的审查：
- **Standards** — 代码是否符合仓库文档化的编码规范？
- **Spec** — 代码是否忠实实现了 originating issue / PRD / spec？

两个轴作为**并行子代理**运行，互不污染上下文，然后本 skill 聚合它们的发现。

## 流程

### 1. 锁定固定点

用户说的固定点——commit SHA、分支名、tag、`main`、`HEAD~5` 等。如果用户没指定，问。

捕获 diff 命令一次：`git diff <fixed-point>...HEAD`（三点，比较 merge-base）。也记录 commit 列表：`git log <fixed-point>..HEAD --oneline`。

继续之前，确认固定点能解析（`git rev-parse <fixed-point>`）且 diff 非空。坏 ref 或空 diff 应在这里失败——不要在两个并行子代理里失败。

### 2. 识别 spec 来源

按这个顺序找 originating spec：

1. **commit 消息中的 issue 引用**（`#123`、`Closes #45` 等）——用 GitHub MCP 工具（`run_mcp` 调 `issue_read` 或 `pull_request_read`）或 `gh issue view <number>` 获取 issue 正文和评论
2. **用户作为参数传的路径**
3. **`docs/`、`specs/`、或 `.scratch/` 下匹配分支名或功能的 PRD/spec 文件**
4. 如果什么都没找到，问用户 spec 在哪。如果用户说没有，**Spec** 子代理跳过，报告"no spec available"

### 3. 识别 standards 来源

仓库里任何文档化"代码该怎么写"的东西，比如 `CODING_STANDARDS.md`、`CONTRIBUTING.md`、`pyproject.toml` 里的 ruff/mypy 配置、`.pre-commit-config.yaml`。

本项目典型的 standards 来源：
- `pyproject.toml`（ruff lint rules、mypy strict、pytest 配置）
- `.pre-commit-config.yaml`
- `docs/ROADMAP.md` 中的工程约定
- 项目 memory 中的硬约束（uv 包管理、LF 行结尾、分支命名等）

在仓库文档化的任何东西之上，Standards 轴始终携带下面的**异味基线**——一组固定的 Fowler 代码异味（_Refactoring_ 第 3 章），即使仓库什么都没文档化也适用。两条规则约束它：

- **仓库覆盖。** 文档化的仓库标准总是赢；当仓库标准认可基线会标记的东西时，抑制异味。
- **始终是判断。** 每个异味是带标签的启发式（"possible Feature Envy"），从不是硬性违规——而且和这里的任何标准一样，跳过工具已经强制的东西。

每个异味读作*它是什么* → *怎么修*；对照 diff 匹配：

- **Mysterious Name** — 函数/变量/类型的名字没揭示它做什么或持有什么。→ 重命名；如果起不出诚实的名字，设计就模糊了。
- **Duplicated Code** — 同样的逻辑形状在 diff 的多个 hunk 或文件中出现。→ 提取共享形状，两处都调它。
- **Feature Envy** — 一个方法更多地伸手进另一个对象的数据而不是自己的。→ 把方法移到它羡慕的数据上。
- **Data Clumps** — 同样几个字段或参数总是一起旅行（一个想诞生的类型）。→ 捆成一个类型，传那个。
- **Primitive Obsession** — 基本类型或字符串顶替了一个值得有自己类型的领域概念。→ 给概念一个小类型。
- **Repeated Switches** — 同样的 `switch`/`if` 链在同一类型上在 diff 中反复出现。→ 用多态替换，或一个两处共享的 map。
- **Shotgun Surgery** — 一个逻辑变更迫使在 diff 的很多文件里分散编辑。→ 把一起变的东西聚集到一个模块。
- **Divergent Change** — 一个文件或模块因几个无关原因被编辑。→ 拆分，让每个模块因一个原因变。
- **Speculative Generality** — 为 spec 不存在的需求添加的抽象、参数或 hook。→ 删掉它；内联回去直到真实需求出现。
- **Message Chains** — 长长的 `a.b().c().d()` 导航，调用者不该依赖。→ 在第一个对象上用一个方法藏起走查。
- **Middle Man** — 一个类或函数主要只是转发。→ 删了它，直接调真正目标。
- **Refused Bequest** — 子类或实现者忽略或覆盖了继承的大部分。→ 丢弃继承，用组合。

### 4. 并行 spawn 两个子代理

发一条消息带两个 `Agent` 工具调用。两个都用 `general_purpose_task` 子代理。

**Standards 子代理 prompt**——包含：
- 完整 diff 命令和 commit 列表
- 步骤 3 找到的 standards 来源文件列表，**加上步骤 3 的异味基线全文粘贴**——子代理没有其他访问途径
- 简报："报告——按文件/hunk（相关处）——(a) diff 违反文档化标准的每一处：引用标准（文件 + 规则）；以及 (b) 你发现的任何基线异味：命名它并引用 hunk。区分硬违规和判断——文档化标准违规可以是硬的，但基线异味始终是判断，且文档化仓库标准覆盖基线。跳过工具强制的。400 字以内。"

**Spec 子代理 prompt**——包含：
- diff 命令和 commit 列表
- spec 的路径或抓取的内容
- 简报："报告：(a) spec 要求但缺失或部分实现的需求；(b) diff 中未被要求的行为（范围蔓延）；(c) 看起来实现了但实现看起来错的需求。为每个发现引用 spec 行。400 字以内。"

如果 spec 缺失，跳过 Spec 子代理，在最终报告里注明。

### 5. 聚合

把两个报告放在 `## Standards` 和 `## Spec` 标题下，逐字或轻度清理。**不要**合并或重排发现——两个轴故意分开（见_为什么两个轴_）。

结尾一行总结：每个轴的发现总数，以及_每个轴内_最严重的问题（如果有）。不要跨轴选单一赢家——那种重排正是分开存在的意义。

## 为什么两个轴

一个变更可能过一个轴但失败另一个：
- 代码遵循每个标准但实现了错误的东西 → **Standards 通过，Spec 失败。**
- 代码精确做了 issue 要求的但破坏了项目约定 → **Spec 通过，Standards 失败。**
分开报告阻止一个轴掩盖另一个。

## 与本项目的适配说明

- issue 获取：用 `run_mcp` 调 GitHub MCP 工具（`issue_read` / `pull_request_read`）或 `gh issue view <number>` / `gh pr view <number>`
- standards 来源：`pyproject.toml`（ruff/mypy 配置）、`.pre-commit-config.yaml`、项目 memory 硬约束
- diff 命令：`git diff <fixed-point>...HEAD`（三点 merge-base 比较）
- 子代理类型：`general_purpose_task`
- 质量门禁：Ruff format + Ruff check + mypy strict + pytest + CI 三项全绿
