# Context

## Glossary

- **Document** — 用户上传的 PDF，含 `original_name` / `sha256` / `page_count` / `status`（pending → processing → ready / failed）
- **Chunk** — Document 切分后的片段，含 `start_page` / `end_page` / `chunk_index` / `content`，跨页切分时 `end_page > start_page`
- **Conversation** — 多轮对话会话，含 `title` / `document_ids`（会话级文档范围锁定，JSON 快照不随文档删除级联）
- **Message** — 会话中的一轮消息，`role` 为 `user` 或 `assistant`；assistant 消息的 `citations` 存引用元数据快照（JSON）；assistant 消息的 `request_id` 持久化到 `messages` 表（ADR 0003），供历史消息反馈反查
- **Citation** — 答案中的 `[C1]` 引用标记，服务端映射到真实 `document_id` / `page` / `snippet`
- **Retrieval** — 检索阶段，从 Qdrant + BM25 混合召回 Top-K chunks（加权 RRF 融合）
- **Reranking** — 重排阶段，用 BGE Cross-Encoder 对检索结果二次排序
- **request_id** — 单次问答的唯一 ID（`uuid.uuid4`），在 `QaService.answer` / `answer_stream` 生成并返回前端（`QueryResponse` / SSE `done` 事件），用于日志追踪与反馈关联；多轮会话中持久化到 `Message.request_id`（仅 assistant 消息，ADR 0003），单轮问答不持久化
- **Feedback** — 用户对某次问答答案的评价记录，含 `rating`（like/dislike）+ 可空 `comment`，以 `request_id` 为主关联键
- **Rating** — 反馈类型，二值枚举 `like`（点赞）/ `dislike`（点踩）
