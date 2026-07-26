# ADR 0003: Message 表持久化 request_id（历史消息反馈反查）

## 状态

Accepted（2026-07-26）

## 背景

ADR 0001 决策「不在 `messages` 表新增 `request_id` 列、不持久化 request_id 到问答链路」的依据是：

1. 当时无历史消息反馈需求——前端只对新消息渲染反馈按钮，`request_id` 在 `QaService.answer` / `answer_stream` 生成后直接返回前端，用于当次问答的反馈提交，不需要反查。
2. 单轮问答（`conversation_id=None`）不持久化 Message，`request_id` 是唯一关联键，无需也不应绑定到 Message。
3. 持久化 `request_id` 需要改动 `_persist_turn` 与 `MessageRead` schema，当时无对应收益。

**新需求驱动**：阶段 10.2 前端补充（PR #87/#88）后，用户进入历史会话时需要看到 assistant 消息的反馈按钮并正确初始化状态（已点赞/已点踩/未反馈）。这要求：

- 前端能从 `GET /api/v1/conversations/{id}/messages` 响应中拿到每条 assistant 消息的 `request_id`
- 前端用 `request_id` 调 `GET /api/v1/feedback/{request_id}` 反查 feedback 状态
- 旧消息（迁移前已存在）无 `request_id`，前端隐藏反馈按钮（不点击 404）

若继续遵循 ADR 0001「不持久化 request_id」，历史消息反馈功能无法实现——`MessageRead` 无法暴露 `request_id`，前端无法反查 feedback。

## 决策

在 `messages` 表新增可空 `request_id` 列（带唯一索引），将 `request_id` 持久化到 assistant 消息。

- **`Message.request_id: Mapped[uuid.UUID | None]`**（可空 + 唯一索引）：
  - `qa_service._persist_turn` 写入 assistant 消息时透传 `request_id`（#90 实现）
  - user 消息不写 `request_id`（保持 None）
  - 旧消息（迁移前）`request_id` 为 NULL，不回填（旧 `request_id` 未持久化到任何表，无法回填）
- **唯一约束**：一个 `request_id` 唯一映射一条 assistant 消息，反馈状态无歧义。
  - SQL 标准对 NULL 的唯一约束语义：多条 NULL 不冲突（SQLite/PostgreSQL 均遵循），因此旧消息与 user 消息共存不破坏约束。
- **`MessageRead` schema 暴露 `request_id`**（#90 实现）：前端从 `GET /api/v1/conversations/{id}/messages` 响应解析 `request_id`。
- **ADR 0001 标记 Superseded**：本 ADR 取代 0001 中「不在 `messages` 表新增 `request_id` 列」的部分；0001 的核心决策「以 `request_id` 作为 `feedback` 表主关联键」仍然有效。

## 后果

- **正面**：
  - 历史消息反馈功能可实现：前端通过 `MessageRead.request_id` + `ApiClient.get_feedback` 反查 feedback 状态。
  - `request_id` 持久化后，未来可校验 feedback 的 `request_id` 对应真实发生过的问答（弥补 ADR 0001 的「无法校验」局限）。
  - 与 `Feedback.request_id` 唯一约束对称：`Message.request_id` 也唯一，两端可双向 join。
- **负面 / 已知局限**：
  - 旧消息（迁移前）`request_id` 为 NULL，无法反馈——前端隐藏按钮，体验降级但可接受（旧消息反馈需求低）。
  - 新增列与索引，迁移需在生产环境执行（`op.add_column` + `op.create_index`，对 SQLite 用 `batch_alter_table` 保证兼容）。
- **风险**：
  - 若 `_persist_turn` 未覆盖 `answer` 与 `answer_stream` 两条路径（#90 实现），可能漏写 `request_id`——需测试覆盖两条路径。
  - 若未来 JWT 用户系统落地，`Feedback.request_id` 唯一约束可能改为 `(request_id, user_id)`，届时 `Message.request_id` 仍唯一（一条消息对应一个 request_id），无需改动。
- **未来演进**：
  - #90 实现 `_persist_turn` 透传 `request_id` + `MessageRead` schema 暴露。
  - #91 实现前端 `MessageInfo` 解析 `request_id` + `ApiClient.get_feedback`。
  - #92 实现前端 `_render_feedback_buttons` 扩展到历史消息。
