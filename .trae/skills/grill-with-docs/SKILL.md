---
name: "grill-with-docs"
description: "对计划或设计进行 relentless 访谈澄清，同时构建项目领域模型（CONTEXT.md 术语表 + ADR）。当用户想在编码前澄清需求、stress-test 想法、或需要沉淀领域术语时调用。"
---

# Grill With Docs

> 适配自 mattpocock/skills 的 grill-with-docs + grilling + domain-modeling 三个 skill，合并为一个完整的"访谈+领域建模"工作流。

## 核心理念

在开始编码前，通过** relentless 访谈**澄清需求的每一个分支，同时**主动构建项目领域模型**（CONTEXT.md 术语表 + ADR 架构决策记录）。这能：

- 消除"agent 没做对"的沟通鸿沟（最常见失败模式）
- 建立共享语言，让后续对话和代码命名更精确、更省 token
- 把难以解释的决策沉淀为 ADR，避免未来困惑

## 访谈方法论（Grilling）

### 基本规则

- **一次只问一个问题**，等待用户回答后再问下一个。多个问题同时抛出会让人不知所措。
- 对每个问题，**先给出你推荐的答案**，再让用户确认或修正。
- 如果一个**事实**可以通过探索环境（文件系统、代码、工具）查到，就自己去查，不要问用户。
- 但**决策**是用户的——把每个决策点摆出来，等用户回答。
- **在用户确认达成共识之前，不要开始实施**。

### 访谈流程

1. **遍历决策树的每一个分支**，逐个解决决策之间的依赖关系。
2. 对每个分支：
   - 探索环境确认事实
   - 摆出决策点 + 你的推荐答案
   - 等待用户回答
   - 记录到 CONTEXT.md 或 ADR（如果符合条件）
3. 当所有分支解决后，向用户确认"我们已达成共识"，再开始实施。

## 领域模型构建（Domain Modeling）

### 文件结构

本项目采用单 context 结构：

```
/
├── CONTEXT.md          ← 术语表（glossary），只记领域概念，不记实现细节
├── docs/
│   └── adr/            ← 架构决策记录
│       ├── 0001-xxx.md
│       └── 0002-yyy.md
└── src/
```

**懒创建**：只有当有内容要写时才创建文件。
- 项目还没有 `CONTEXT.md` → 当第一个术语被确定时创建
- 项目还没有 `docs/adr/` → 当第一个 ADR 需要被记录时创建

### 访谈中的主动纪律

这不是"读一下 CONTEXT.md 就行"的被动习惯，而是**主动挑战和锐化**领域模型：

#### 1. 挑战术语冲突
当用户使用的术语与 `CONTEXT.md` 现有定义冲突时，立即指出。
> "你的 glossary 把'检索'定义为 X，但你刚才说的好像是 Y——是哪个？"

#### 2. 锐化模糊语言
当用户使用模糊或重载的词时，提出精确的规范术语。
> "你说'文档'——是指 Document（上传的 PDF）还是 Chunk（切分后的片段）？这是两个不同的东西。"

#### 3. 讨论具体场景
当涉及领域关系时，用具体场景做压力测试，逼用户精确说明概念边界。
> "如果用户删除了一个 Document，但它已经被某个 Conversation 的历史引用了，应该怎么处理？"

#### 4. 与代码交叉验证
当用户陈述某事如何工作时，检查代码是否同意。发现矛盾时浮现出来。
> "你的代码在删除 Document 时级联删除了 Message，但你刚才说消息应该保留——哪个对？"

#### 5. 即时更新 CONTEXT.md
当一个术语被确定，**当场**更新 `CONTEXT.md`，不要攒着批量写。

`CONTEXT.md` 应该**完全 devoid of 实现细节**。不要把它当成 spec、草稿本、或实现决策的仓库。它只是**术语表**。

### CONTEXT.md 格式

```markdown
# Context

## Glossary

- **Document** — 用户上传的 PDF，含 original_name / sha256 / page_count / status
- **Chunk** — Document 切分后的片段，含 start_page / end_page / chunk_index / content
- **Conversation** — 多轮对话会话，含 title / document_ids（会话级文档范围锁定）
- **Message** — 会话中的一轮消息，role 为 user 或 assistant
- **Citation** — 答案中的 [C1] 引用标记，映射到真实 document_id / page / snippet
- **Retrieval** — 检索阶段，从 Qdrant + BM25 混合召回 Top-K chunks
- **Reranking** — 重排阶段，用 BGE Cross-Encoder 对检索结果二次排序
```

### ADR 三个条件

**只在三个条件全满足时**才提议创建 ADR：

1. **难以逆转** — 后期改变主意的成本有意义
2. **没有上下文会困惑** — 未来读者会问"为什么这样做？"
3. **真实权衡的结果** — 有真正的备选方案，你因为特定理由选了一个

如果任何一个不满足，跳过 ADR。

### ADR 格式

```markdown
# ADR 0001: 用 RRF 融合 BM25 与向量检索

## 状态
Accepted（2026-07-15）

## 背景
单一向量检索对关键词/数值/公式类问题弱，需要混合检索...

## 决策
用 Reciprocal Rank Fusion (RRF) 融合 BM25 和向量检索，vector_weight=2.0

## 后果
- 正面：Hit@5 从 70% 提升到 76.7%
- 负面：引入 rank_bm25 依赖，中文场景需 jieba 分词
- 风险：BM25 和向量检索的 score 量纲不同，必须用 RRF 而非加权求和
```

## 与本项目的适配说明

- 本项目（research-rag-assistant）已有 `docs/` 目录，ADR 放 `docs/adr/`
- 本项目用 GitHub Issues 跟踪任务，访谈结论如果产生新任务，用 `to-tickets` skill 拆解
- 本项目已有领域术语（Document / Chunk / Conversation / Message / Citation / Retrieval / Reranking），首次访谈时应先读取现有代码和 docs/ROADMAP.md 提取已有术语，再与用户确认
