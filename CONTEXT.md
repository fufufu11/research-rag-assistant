# Context

## Glossary

- **Document** — 用户上传的 PDF，含 `original_name` / `sha256` / `page_count` / `status`（pending → processing → ready / failed）
- **Chunk** — Document 切分后的片段，含 `start_page` / `end_page` / `chunk_index` / `content`，跨页切分时 `end_page > start_page`
- **Conversation** — 问答的必需归属单元，含 `title` / `document_ids`（会话级文档范围锁定，JSON 快照不随文档删除级联）；系统不提供脱离 Conversation 的单轮问答
- **Turn** — Conversation 中的一轮问答，由一条 user Message 与其对应的一条 assistant Message 构成
- **Message** — Conversation 中的一条消息，`role` 为 `user` 或 `assistant`；assistant 消息的 `citations` 存引用元数据快照（JSON）；assistant 消息的 `request_id` 持久化到 `messages` 表（ADR 0003），供历史消息反馈反查
- **Citation** — 单个 Turn 答案中的 `[C1]`、`[C2]` 等来源标记；数字是本轮生成上下文的一基索引，映射到真实 `document_id` / `page` / `snippet`，同一 Document 的不同片段可对应不同编号
- **Retrieval** — 检索阶段，从 Qdrant + BM25 混合召回 Top-K chunks（加权 RRF 融合）
- **Reranking** — 重排阶段，用 BGE Cross-Encoder 对检索结果二次排序
- **request_id** — 单次问答的唯一 ID（`uuid.uuid4`），在 `QaService.answer` / `answer_stream` 生成并返回前端（`QueryResponse` / SSE `done` 事件），用于日志追踪与反馈关联；持久化到 assistant `Message.request_id`（ADR 0003）
- **Feedback** — 用户对某次问答答案的评价记录，含 `rating`（like/dislike）+ 可空 `comment`，以 `request_id` 为主关联键
- **Rating** — 反馈类型，二值枚举 `like`（点赞）/ `dislike`（点踩）
- **Folder** — 用户自建的文献分组容器，用于组织 Document；本次前端优化不实现，作为后续独立 issue 跟进
