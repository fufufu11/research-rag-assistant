# ADR 0001: 用 request_id 作为 feedback 主关联键（不持久化校验）（Superseded by 0003）

## 状态

Superseded（2026-07-26，由 ADR 0003 取代）

> 本 ADR 的核心决策「以 `request_id` 作为 `feedback` 表主关联键」仍然有效；
> 被 0003 取代的部分是「不在 `messages` 表新增 `request_id` 列」——历史
> 消息反馈需求要求通过 `request_id` 反查 feedback 状态，需将 `request_id`
> 持久化到 `messages` 表。详见 ADR 0003。

## 背景

阶段 10.2 用户反馈闭环需要把"点赞/点踩"记录关联到"某次问答答案"。候选关联键有三：

1. **`message_id`**（FK → messages.id）：有 FK 完整性，可 join 消息内容。但 `QaService._persist_turn` 调 `add_message` 后丢弃了返回的 Message 对象，assistant 消息的 `id` 从未上抛到 API 响应（`QueryResponse` / SSE `done` 事件只返回 `request_id` + `conversation_id`）。采用它需改动问答 API 返回 `message_id`。更关键的是：**单轮问答**（`conversation_id=None`）**不持久化任何 Message**，单轮答案将无法被反馈，除非为单轮也自动建会话（改动更大）。

2. **`request_id`**（UUID）：已在 `QaService.answer` / `answer_stream` 生成并返回前端，单轮/多轮均可用，**无需改动现有问答 API**。

3. **不关联具体答案**（纯计数）：无法满足验收标准"DB 可查询某答案的反馈"。

代码探索确认：`request_id` 在 service 层 `uuid.uuid4()` 生成，仅返回前端用于日志追踪，**不持久化到任何表**；项目当前无认证/用户系统（阶段 11.1 待实施），反馈是匿名的。

## 决策

以 `request_id`（UUID，唯一索引）作为 `feedback` 表的主关联键，额外保留可空 `message_id` FK（`ondelete=SET NULL`）供多轮场景 join 消息内容。

- `feedback.request_id` 加 `UNIQUE` 约束，Upsert 语义（POST 不存在则创建、存在则更新），兼作匿名防刷。
- `feedback.message_id` 可空：单轮问答 `message_id=None`，仅靠 `request_id` 关联；多轮问答 `message_id` 非 None，可 join。
- 不在 `messages` 表新增 `request_id` 列、不持久化 request_id 到问答链路。

## 后果

- **正面**：无需改动现有问答 API（`QueryResponse` / `StreamDoneEvent`），单轮与多轮答案均可被反馈，实现成本最低。
- **负面 / 已知局限**：**无法校验** feedback 的 `request_id` 对应真实发生过的问答——request_id 除 feedback 表外无持久化，匿名无认证下可伪造 request_id 提交反馈。当前匿名场景风险可接受（反馈仅用于优化信号，非高stakes 数据）。
- **风险**：若未来需要反馈与真实问答严格对应，需额外在问答链路持久化 request_id（如加到 `messages` 表）并加校验，此时本设计需补迁移。该成本被本 ADR 显式记录，避免未来读者困惑"为何不校验 request_id"。
- **未来演进**：阶段 11.1 认证鉴权落地后，可加 `user_id` 列并把唯一约束改为 `(request_id, user_id)` 以支持多用户独立反馈。
