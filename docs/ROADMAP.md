# 上线路线图

> 本文档记录"科研文献可溯源智能问答系统"从 Demo 到可上线系统的演进计划。
> 每个阶段对应若干 GitHub Issue + PR，完成后更新状态。

## 当前状态

- **已覆盖阶段**：阶段 0-7（基础功能）+ 阶段 8.1（Reranker 重排序）+ 阶段 8.2（跨页切分）+ 阶段 8.3（BM25 混合检索）+ 阶段 8.4（EMBEDDING_MODEL 环境变量修复 + bge-m3 可选集成 + 中文论文评测）+ 阶段 9.1（流式输出 SSE）+ 阶段 9.2（多轮对话）+ 阶段 9.3（答案质量评测）+ 阶段 10.1（可观测性 Langfuse）+ 阶段 10.2（用户反馈闭环）+ 阶段 10.3（性能优化）+ 阶段 11.1（API Key 认证鉴权）+ 阶段 11.2（输入过滤与文件校验）+ 阶段 11.3（API 限流）+ 阶段 11.4（Docker Compose 一键部署）+ 阶段 11.5（CI/CD 自动化部署）+ UI 体验优化阶段一（Issue #72：ChatGPT 风格布局 + 多文档会话范围锁定 Bug 修复）+ 历史消息反馈 prefactor（Issue #89：`Message.request_id` 列 + ADR 0003）+ 历史消息反馈写入读出（Issue #90：`_persist_turn` 透传 `request_id` + `MessageRead` 暴露 `request_id`）+ 历史消息反馈前端 model+client（Issue #91：`MessageInfo.request_id` + `ApiClient.get_feedback` 404 转 None）+ 历史消息反馈前端 UI 渲染（Issue #92：`_render_feedback_buttons` 扩展到历史消息 + 旧消息隐藏按钮 + `_init_feedback_state_for_history` 批量初始化反馈状态）+ 阶段 11.6 生产安全加固（切片 A #97：`secrets.py` helper + 切片 B #98：非 root 容器 Dockerfile 改造 + 切片 C #99：8 个密钥读取点替换为 `get_secret` + postgres `POSTGRES_PASSWORD_FILE` 支持 + 切片 D #101：docker-compose.prod.yml 配置 8 个 docker secrets 挂载 + `.env.docker.secrets.example` 示例文件 + 切片 E #100：nginx + certbot 容器化 + TLS 反代 Let's Encrypt webroot 模式 + 切片 F #102：文档同步收官）+ **UI 体验优化阶段二**（基于 ChatGPT 界面截图的二轮迭代：#109 左侧导航重构-图标分组+可折叠会话/文档列表 + #112 输入栏「+」上传按钮+底部免责声明 + #111 对话区居中+宽度收窄布局 + #113 AI 回复复制按钮 + #110 顶部模型选择下拉占位，5 个 ticket 全部 squash 合并到 main，905 → 938 测试零回归）
- **测试**：938 个单元测试通过（含 #97 新增 7 个 + #99 新增 24 个 + #98 新增 7 个 + #101 新增 10 个 + #100 新增 33 个：observability/llm_config/answer_evaluation/auth/embedding/embedding_config/deployment_config 七个文件的 `_FILE` 优先与 fallback env 路径覆盖 + Dockerfile/entrypoint.sh 非 root 改造的文件结构验证 + docker-compose.prod.yml docker secrets 配置结构与 .env.docker.secrets.example 模板验证 + nginx.conf / docker-compose.prod.yml nginx+certbot 服务 / nginx & certbot entrypoint 结构断言 + UI 体验优化阶段二 5 个 ticket 新增 33 个：#109 nav_state +6 / #112 upload +8 / #111 chat_layout +5 / #113 copy_button +8 / #110 model_dropdown +6）
- **CI**：ruff format + ruff check + mypy + pytest 三项全绿
- **评测**：英文 BM25 混合检索后 Hit@5=76.7%、MRR=0.607（chunk-500-overlap-0 + bge-small-en + BM25 + reranker），详见 [评测报告](./evaluation_report.md)；中文论文评测 bge-small-zh 最优 Hit@5=90.0%、MRR=0.783（chunk-500-overlap-160 + reranker），显著优于 jina-embeddings-v3 API，详见 [中文评测报告](./evaluation_report_zh.md)

---

## 阶段 8：检索质量优化（短期，进行中）

| 序号 | 任务 | 状态 | 预期收益 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| 8.1 | BGE Reranker 重排序 | ✅ 已完成（PR #37，Issue #36） | Hit@1 +23%，MRR +33% | 无 | 高 |
| 8.2 | 跨页切分 | ✅ 已完成（Issue #38） | Hit@5 +3.3%（70%→73.3%），Hit@1 有下降 | 无 | 高 |
| 8.3 | 混合检索（BM25 + 向量） | ✅ 已完成（PR #41，Issue #40） | Hit@5 +3.3%（73.3%→76.7%），MRR +10%（0.551→0.607） | 无 | 高 |
| 8.4 | 多语言 Embedding（bge-m3） | ✅ 已完成（调整方案，Issue #42） | 修复 EMBEDDING_MODEL 环境变量读取 gap + bge-m3 可选集成 + 中文论文评测验证配置驱动策略 | 8.2 | 中 |

**验收标准**：Hit@5 提升到 75%+，MRR 提升到 0.65+。

### 8.2 跨页切分

- **状态**：✅ 已完成（Issue #38）
- **目标**：当前不跨页切分导致跨页信息被切断，改进切分逻辑让段落/句子跨页连续
- **技术方案**：先按页提取，再合并连续页文本，最后用 RecursiveCharacterTextSplitter 切分
- **页码溯源**：`Chunk` dataclass 用 `start_page` + `end_page` 记录页码范围，通过字符偏移追踪
- **评测结果**：Hit@5 从 70% 提升到 73.3%（chunk-500-overlap-0 + reranker），Hit@1 有下降（跨页 chunk 语义分散），详见 [评测报告 5.5 节](./evaluation_report.md#55-跨页切分-ab-对比阶段-82)
- **设计取舍**：保留 `cross_page=True` 作为默认，因为召回率提升比精度下降更重要，后续 8.3 混合检索可弥补 Hit@1 下降

### 8.3 混合检索（BM25 + 向量）

- **状态**：✅ 已完成（Issue #40，PR #41）
- **目标**：改善关键词列表、数值、公式类问题的检索效果
- **技术方案**：引入 `rank_bm25` 库，BM25 召回 + 向量召回取并集，再融合排序（加权 RRF，vector_weight=2.0）
- **风险**：BM25 对中文需 jieba 分词，英文场景下直接用空格分词即可
- **评测结果**：Hit@5 在所有 5 组参数中均提升或持平（+3.3% ~ +10.0%），最优组 Hit@1 +6.7%（40%→46.7%），MRR +10%（0.551→0.607）

### 8.4 多语言 Embedding（bge-m3）+ 中文论文评测

- **状态**：✅ 已完成（调整方案，Issue #42；中文评测补充验证完成）
- **目标**：统一中英文混合检索场景，改善跨论文语义干扰（4 条 Hit@5 未命中）；补充中文论文评测数据集验证配置驱动策略
- **最终交付**：
  - 修复预存在 gap：`EMBEDDING_MODEL` 环境变量此前声明在 `.env.example` 但从未被代码读取，新增 `get_embedding_config()` 接入 `get_qa_service` 与 `_create_vector_store`，让用户能按场景切换模型
  - 集成 `BAAI/bge-m3` 多语言模型作为可选项（vector_store 维度动态推断已支持 1024 维），通过 `EMBEDDING_MODEL=BAAI/bge-m3` 启用
  - 默认仍保留 `BAAI/bge-small-zh-v1.5`（中文小模型，512 维，体积小、推理快，生产面向中文用户）
  - 新增中文论文评测数据集 `eval/dataset_zh.json`（3 篇 ChinaXiv 中文 AI 论文，30 条问题）与 `eval/pdfs/zh/`，并完成 bge-small-zh 与 jina-embeddings-v3（API）的 8 组对比评测
- **方案调整原因**：实测 bge-m3 在纯英文论文评测下不及 bge-small-en（Hit@5 70.0% vs 76.7%，未命中 9 条 vs 7 条），未达 Issue #42 原验收标准。多语言模型在语言专用小模型的优势场景下不占优是预期内的模型特性，强行切换默认会引入英文场景退步。改为配置驱动策略：中文用 bge-small-zh、英文用 bge-small-en、混合用 bge-m3，把语言选择权通过 `EMBEDDING_MODEL` 交给用户
- **技术方案**：新增 `get_embedding_config()` 读 `EMBEDDING_MODEL` 环境变量；仅用 bge-m3 的 dense 向量，不引入 FlagEmbedding（sparse 与现有 BM25 重复）
- **风险**：bge-m3 模型体积大（约 2.2GB），推理慢于 bge-small；维度变化（512→1024）需重新索引已上传文档（删除文档后重新上传，或清空 Qdrant 集合）
- **验收**：环境变量修复 + bge-m3 可选集成交付完成；中文论文评测补充验证完成，结论支持配置驱动策略——`bge-small-zh-v1.5`（本地中文专用）在中文论文场景显著优于 `jina-embeddings-v3`（多语言 API）：最优 Hit@5 90.0% vs 60.0%、MRR 0.783 vs 0.583，且本地推理延迟低 40-50 倍。详见 [中文评测报告](./evaluation_report_zh.md)

---

## 阶段 9：生成质量优化（短期）

| 序号 | 任务 | 状态 | 预期收益 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| 9.1 | 流式输出（SSE） | ✅ 已完成（PR #47，Issue #46） | 首字延迟降低，用户体验提升 | 无 | 高 |
| 9.2 | 多轮对话 | ✅ 已完成（PR #49，Issue #48） | 支持上下文追问 | 无 | 中 |
| 9.3 | 答案质量评测 | ✅ 已完成（PR #52 + PR #53，Issue #51） | 忠实度/相关性指标，量化生成质量 | 无 | 中 |

### 9.1 流式输出

- **状态**：✅ 已完成（PR #47，Issue #46）
- **目标**：LLM 答案流式返回，用户看到逐字生成
- **技术方案**：FastAPI `StreamingResponse` + LangChain `astream` + SSE 协议（token/done/error 三类事件）
- **前端**：Streamlit `st.write_stream` 接收流式输出
- **交付**：`QueryRequest.stream` 字段、`QaService.answer_stream` 异步生成 token + `[INSUFFICIENT_EVIDENCE]` 缓冲检测、`StreamingResponse` SSE、`ApiClient.ask_question_stream` SSE 解析、16 个新增单元测试

### 9.2 多轮对话

- **状态**：✅ 已完成（PR #49，Issue #48）
- **目标**：支持"刚才那篇论文的方法再详细说说"等上下文追问
- **技术方案**：
  - DB 持久化会话历史（`Conversation` / `Message` ORM 模型 + Alembic 迁移 + `ConversationRepository`）
  - 查询改写（`rewrite_query`，LLM 把"那篇"等指代解析为独立问题再检索，失败回退原问题）
  - 历史截断双重保护（轮数 `DEFAULT_MAX_HISTORY_TURNS=5` + token `DEFAULT_MAX_HISTORY_TOKENS=4000`）
  - 会话级 `document_ids` 锁定（保证多轮检索范围一致）
  - 流式 + 非流式双路径都支持 `conversation_id`
  - 每轮引用编号独立（`[C1]` 只指代当前轮 contexts）
- **风险**：会话历史过长需截断 → 已用轮数 + token 双重保护解决
- **验收**：连续 3 轮对话内能正确理解"那篇""刚才"等指代
- **测试**：111 个新增测试（Repository CRUD / 底层函数 / QaService 编排 / API 路由 / ApiClient），总测试数 500+

### 9.3 答案质量评测

- **状态**：✅ 已完成（PR #52 + PR #53，Issue #51）
- **目标**：扩展评测到生成阶段，量化 LLM 答案质量
- **指标**：忠实度（答案是否基于引用）、相关性（答案是否回答了问题）、完整性、引用正确性（项目特色指标）
- **技术方案**：LLM-as-judge 自实现（未用 RAGAS，避免依赖冲突）；纯函数与编排分离（`build_judge_prompt` / `parse_judge_response` / `check_citations` / `aggregate_judgements` / `judge_answer`）；judge LLM 支持 `JUDGE_LLM_*` 环境变量覆盖避免同模型自评偏差
- **交付**：`src/research_rag/answer_evaluation.py` + `scripts/evaluate_answer.py` + `tests/unit/test_answer_evaluation.py`（65 个单元测试，FakeListChatModel Mock，CI 不消耗真实 Token）+ 中英文各 30 条问题评测报告
- **验收**：DeepSeek-V3.2 评测报告已生成——英文忠实度=5.00 相关性=4.96 完整性=4.62 引用正确性=4.54；中文忠实度=5.00 相关性=5.00 完整性=4.97 引用正确性=5.00，详见 [答案质量报告](./answer_quality_report.md) / [中文报告](./answer_quality_report_zh.md)

---

## 阶段 10：生产化基础设施（中期）

| 序号 | 任务 | 状态 | 预期收益 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| 10.1 | 可观测性（Langfuse/LangSmith） | ✅ 已完成（PR #56，Issue #55） | 全链路追踪，定位瓶颈 | 无 | 高 |
| 10.2 | 用户反馈闭环 | ✅ 已完成（PR #60，Issue #59） | 点赞/点踩记录到 DB | 9.3 | 中 |
| 10.3 | 性能优化 | ✅ 已完成（PR #64，Issue #63） | BM25 索引缓存 + 并发检索（Qdrant 路径）+ 检索阶段 P95 基准；P95 降 76.3% | 无 | 中 |
| 10.4 | 多语言支持（bge-m3） | ✅ 已提前至 8.4 完成（Issue #42） | 中英文混合场景统一 | 8.2 | 低 |

### 10.1 可观测性

- **状态**：✅ 已完成（PR #56，Issue #55）
- **目标**：追踪每次问答的完整调用链（检索 → 重排 → LLM 生成），记录延迟、Token 消耗、失败率
- **技术方案**：Langfuse（开源自部署）+ LangChain CallbackHandler 集成；环境变量开关 no-op 优先（`LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` / `HOST` 三项非空才启用，未配置时零开销）
- **交付**：`src/research_rag/observability.py`（`LangfuseConfig` / `observe` / `get_current_langchain_handler` / `_build_run_config` / `flush`）+ `tests/unit/test_observability.py`（25 个单元测试覆盖 no-op 与启用路径）+ `docker-compose.langfuse.yml` 自部署模板；`QaService.answer` / `answer_stream` / `_prepare_contexts` 添加 `@observe` 装饰器，`run_config` 透传，`app.py` lifespan finally 调用 `flush_langfuse` 避免异步队列丢失
- **验收**：dashboard 可查看每次请求的检索结果、重排前后对比、LLM 输入输出；未配置环境变量时功能正常无副作用

### 10.2 用户反馈闭环

- **状态**：✅ 已完成（后端 PR #60 / Issue #59；前端补充 PR #87 / Issue #86）
- **目标**：用户对答案点赞/点踩，记录到 DB 用于持续优化
- **技术方案**：
  - `Feedback` ORM 模型：`request_id` 唯一主关联键 + 可空 `message_id` 外键（`ondelete=SET NULL`）+ `rating` 枚举（like/dislike）+ `comment`（最长 2000 字符）；`FeedbackRating` 枚举 + `FeedbackNotFoundError` 异常
  - `FeedbackRepository`：`upsert` / `get_by_request_id` / `list`（按 rating / conversation_id 筛选）/ `delete`，只 `flush` 不 `commit`（事务由路由层显式控制）
  - Alembic 迁移：创建 `feedback` 表
  - 路由直接调 Repository，不新建 `FeedbackService`（避免空模块）
  - `request_id` 作为主关联键的决策与已知局限见 [ADR 0001](./adr/0001-request-id-as-feedback-key.md)
  - **前端补充**（PR #87）：`ApiClient.submit_feedback` / `delete_feedback` 封装 POST/DELETE；`_render_feedback_buttons` 在 assistant 气泡内渲染点赞/点踩按钮，状态缓存在 `st.session_state`，Upsert 语义切换赞↔踩，点踩后展开 `st.text_area` 收集评论
- **交付**：
  - 后端：`src/research_rag/db/models.py`（Feedback / FeedbackRating / FeedbackNotFoundError）+ `src/research_rag/db/repositories.py`（FeedbackRepository）+ `alembic/versions/2026_07_24_1413-aa7a10e898fd_add_feedback_table_for_user_feedback_.py` + `src/research_rag/api/routes/feedback.py`（4 个端点）+ `CONTEXT.md` 术语表 + `docs/adr/0001-request-id-as-feedback-key.md` + 30 个单元测试（Repository 12 + API 18）
  - 前端：`src/research_rag/ui/api_client.py`（`FeedbackInfo` dataclass + `submit_feedback` / `delete_feedback` 方法 + `_parse_feedback`）+ `src/research_rag/ui/app.py`（`_render_feedback_buttons` 函数）+ 9 个单元测试（`TestSubmitFeedback` 6 + `TestDeleteFeedback` 3）
- **验收**：DB 可查询某答案的反馈，支持按赞/踩筛选；`POST /api/v1/feedback` Upsert（201 新建 / 200 更新）、`GET /api/v1/feedback/{request_id}`（200 / 404）、`GET /api/v1/feedback?rating=&conversation_id=&limit=`、`DELETE /api/v1/feedback/{request_id}`（204 / 404）端到端测试通过；前端流式渲染后显示点赞/点踩按钮，点击切换 Upsert，再次点击撤销 DELETE，点踩后可补充文字评论
- **不在范围**：历史消息反馈（后端 `Message` 模型无 `request_id` 字段，前端 `MessageInfo` 也缺失）—— **prefactor 已完成**（PR #93 / Issue #89）：`Message` 表新增 `request_id` 列 + ADR 0001 标记 Superseded + ADR 0003 新写；**写入读出已完成**（PR #94 / Issue #90）：`_persist_turn` 透传 `request_id` 到 assistant `Message` + `MessageRead` schema 暴露 `request_id` + `add_message` 签名扩展；**前端 model+client 已完成**（PR #95 / Issue #91）：`MessageInfo.request_id` 字段 + `_parse_message` 解析 + `ApiClient.get_feedback` 404 转 None；**前端 UI 渲染已完成**（PR #96 / Issue #92）：`_render_feedback_buttons` 扩展到 `conversation_messages` 历史消息循环 + `_init_feedback_state_for_history` 批量初始化反馈状态 + 旧消息（`request_id=None`）隐藏按钮 + `_should_render_feedback_for_message` 纯函数判断渲染条件；历史消息反馈端到端体验闭环完成

### 10.3 性能优化

- **状态**：✅ 已完成（PR #64，Issue #63）
- **目标**：降低检索阶段 P95 延迟，提升并发检索能力
- **范围调整**（基于代码事实重新定义，详见 [Issue #63](https://github.com/fufufu11/research-rag-assistant/issues/63)）：
  - **砍掉 Embedding 缓存**：Qdrant 生产路径下向量在上传时已持久化，查询时只 embed 1 条 query（极廉价），生产路径收益≈0
  - **跳过 Qdrant HNSW 调优**：当前规模仅数百 chunk，HNSW 搜索 <10ms，调优收益≈0 且重建索引成本高；数据量到万级 chunk 再评估
  - **保留并发检索**：`_retrieve_hybrid` 内 BM25 与 Qdrant 检索串行执行，用 `ThreadPoolExecutor` 并行可重叠 ~50ms
  - **新增 BM25 索引缓存**（最大瓶颈）：每次问答都从 DB 读全部 chunk 重建 BM25 索引（~50ms），缓存后命中复用
- **技术方案**：
  - `BM25IndexCache`：进程级实例经 FastAPI 依赖注入到 `QaService`，签名 `tuple(sorted(doc_ids)) + total_chunks` 自动失效（文档增删/重新切分时自动重建），不耦合 `DocumentService`
  - 并发检索：`_retrieve_hybrid` 内用 `concurrent.futures.ThreadPoolExecutor(max_workers=2)` 并行 BM25 检索与 Qdrant 检索（numpy 打分 + 网络 I/O 都释放 GIL）；InMemory 测试路径保持串行
  - 基准脚本 `scripts/benchmark_retrieval.py`：走真实 `QaService` 路径，计时仅 `_retrieve_hybrid`（不含 reranker/LLM），算 P50/P95/P99，冷热两遍
- **P95 验收口径**：检索阶段（BM25 建索引 + BM25 检索 + Qdrant 检索 + RRF 融合），**不含 LLM 生成与 reranker**。理由见 [ADR 0002](./adr/0002-retrieval-stage-p95-metric.md)——端到端延迟中 LLM 生成占 80%+，检索优化无法移动端到端 P95 50%
- **验收**：检索阶段 P95 降低 ≥ 50%（优化分支相对 main 基线），并发 10 请求无阻塞
- **验收结果**：检索阶段 P95 从 1727.9ms 降至 408.7ms，降幅 76.3%（远超 ≥50% 标准），由 `scripts/verify_bm25_cache.py` 对比 `--no-cache` 基线与缓存路径测得
- **不在范围**：Embedding 缓存、Qdrant HNSW 调优、reranker 延迟优化、LLM 生成延迟优化、InMemory 测试路径优化

### 10.4 多语言支持

- **目标**：换用 `bge-m3` 统一中英文场景
- **技术方案**：替换默认 Embedding 模型，重新索引已有文档
- **风险**：bge-m3 模型较大（约 2.2GB），首次下载耗时
- **验收**：中英文混合查询场景下 Hit@5 不低于当前英文场景

---

## 阶段 11：安全与部署（中期）

| 序号 | 任务 | 状态 | 预期收益 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| 11.1 | 认证鉴权（API Key） | ✅ 已完成（Issue #74） | 防止未授权访问 | 无 | 高 |
| 11.2 | 输入过滤与文件校验 | ✅ 已完成（Issue #76） | 防注入、文件类型/大小限制 | 无 | 高 |
| 11.3 | API 限流 | ✅ 已完成（Issue #78） | 防止滥用 | 11.1 | 中 |
| 11.4 | Docker Compose 一键部署 | ✅ 已完成（PR #70，Issue #69） | 容器化部署 api/qdrant/postgres 三服务 | 无 | 高 |
| 11.5 | CI/CD 自动化部署 | ✅ 已完成（Issue #81，PR #82） | push 到 main 自动部署 | 11.4 | 中 |
| 11.6 | 生产安全加固（非 root 容器 + docker secrets + TLS 反代） | ✅ 已完成（PR #103 #105 #104 #106 #107 #108，切片 A-F 全部完成） | 生产级密钥与传输安全 | 11.4 | 高 |

### 11.1 认证鉴权

- **状态**：✅ 已完成（Issue #74）
- **目标**：API 需鉴权才能访问，防止未授权调用
- **技术方案**：API Key 认证，FastAPI `Depends` + `HTTPBearer`
  - 新增 `src/research_rag/api/auth.py`：`verify_api_key` 依赖函数，用 `HTTPBearer(auto_error=False)` 从 `Authorization: Bearer <key>` 提取 token，与 `API_KEYS` 环境变量配置的有效 key 集合比对
  - `app.include_router(..., dependencies=[Depends(verify_api_key)])` 集中挂载，所有 `/api/v1/*` 端点（documents / queries / conversations / feedback）自动生效，无需改每个路由文件
  - 环境变量 `API_KEY_ENABLED` 控制开关（默认禁用，开发友好，向后兼容现有测试）；`API_KEYS` 配置有效 key（逗号分隔，支持多个）
  - `secrets.compare_digest` 恒定时间比对，防时序攻击
  - Streamlit `ApiClient` 从 `API_KEY` 环境变量读 key，`_get_headers()` 在所有 HTTP 调用（`_request` + `ask_question_stream`）携带 `Authorization: Bearer <key>`
- **设计取舍**：
  - **API Key 而非 JWT**：当前无 User 表、无注册登录系统。JWT 需要用户认证流程支撑，独立于 11.1 验收范围。后续建用户系统时再切 JWT，`HTTPBearer` 格式兼容，切换成本低
  - **开关默认禁用**：参考项目 Langfuse no-op 优先模式，保证现有 620+ 测试零改动、本地开发零配置；生产部署显式 `API_KEY_ENABLED=true` 启用
  - **启用但 `API_KEYS` 为空时安全失败**：所有请求 401，避免「以为启用了认证但实际无人能通过」的配置静默错误变成「认证形同虚设」
  - **多 key 支持**：`API_KEYS` 逗号分隔，支持按客户端隔离（UI / 脚本 / 外部服务各自一个 key），泄露时只轮换受影响的那一个
  - **不引入 User 表**：API Key 是服务级凭证，环境变量管理即可，落库需配套签发/撤销逻辑，YAGNI
- **验收**：未认证请求返回 401（含 `WWW-Authenticate: Bearer` 头），认证后正常访问；禁用认证时所有端点可匿名访问（向后兼容）；31 个新增单元测试覆盖纯函数 + 集成路径
- **不在范围**：用户注册/登录系统 + JWT（后续独立 Issue）、权限分级、API Key 签发/轮换/撤销管理界面、阶段 11.2 输入过滤、阶段 11.3 限流

### 11.2 输入过滤与文件校验

- **状态**：✅ 已完成（Issue #76）
- **目标**：防止恶意文件上传和注入攻击
- **技术方案**：
  - 文件类型校验：扩展名 + `content_type` 双重白名单（仅允许 PDF），返回 415
  - 文件大小限制：`MAX_UPLOAD_MB` 环境变量（默认 20MB，复用已有变量），返回 413
  - SQL 注入防护：SQLAlchemy 参数化查询（阶段 5 已实现，本阶段不涉及）
  - Prompt 注入：正则匹配 10 类常见注入模式（`ignore previous` / `system:` / `<|im_start|>` / `[/inst]` / `act as` / `reveal instructions` / `jailbreak` / `DAN` 等），命中即返回 400（不净化后放行，避免语义漂移）
- **实现位置**：新增 [src/research_rag/api/security.py](../src/research_rag/api/security.py) 集中管理，与 `api/auth.py` 对称；路由层 `documents.py` / `queries.py` 调用 `validate_upload_file` / `validate_question`
- **设计取舍**：
  - **默认启用**（`INPUT_VALIDATION_ENABLED=true`）：与 11.1 认证默认禁用相反——安全功能默认开是最佳实践，且校验只影响非法请求不影响合法用户
  - **校验放路由层而非中间件**：能精确控制哪个端点需要哪种校验，返回精确状态码（415/413/400）
  - **双重白名单**：单一校验可绕过（客户端可伪造 `content_type`），双重提高门槛；`content_type` 缺失时只校验扩展名（兼容 Streamlit）
  - **正则而非 LLM 检测**：零成本、确定性强、可单测；完美防御是开放问题，本阶段做"低垂果实"过滤
  - **命中即拒**：净化会改变语义且绕过手法多变，命中即拒让用户明确知道输入有问题
- **验收**：上传非 PDF 文件返回 415，超大文件返回 413，注入问题返回 400；禁用校验时所有请求放行（向后兼容）；77 个新增单元测试覆盖纯函数 + 路由集成路径
- **不在范围**：文件内容深度校验（PDF 内嵌 JS/病毒扫描）、复杂 Prompt 注入防御（LLM 检测）、XSS 过滤（Streamlit 已转义）、阶段 11.3 限流

### 11.3 API 限流

- **状态**：✅ 已完成（Issue #78，PR #79）
- **目标**：防止滥用，保护服务稳定性
- **技术方案**：`slowapi` 库，按用户/IP 限制请求频率
  - 新增 `src/research_rag/api/rate_limit.py`：`Limiter` 模块级单例 + `default_limits` callable lambda（请求时动态读环境变量，支持 monkeypatch 测试）
  - `rate_limit_key` 函数：认证启用按 `key:<token>`（公司出口 IP 共享，按 key 更精确），认证禁用按 `ip:<ip>`（X-Forwarded-For 首段或 client.host）
  - `app.py` 集中挂载：`configure_limiter()` + `app.state.limiter` + `SlowAPIMiddleware` + `RateLimitExceeded` 异常处理器（返回 `ErrorResponse` JSON 体 + `Retry-After` / `X-RateLimit-*` 头）
  - 上传端点 `POST /api/v1/documents` 单独更严限流：`@limiter.limit(lambda: f"{get_rate_limit_upload_per_minute()}/minute")` 装饰器覆盖默认 60/min（PDF 解析+切分+Embedding+Qdrant 写入单请求 5-30 秒，比问答重）
  - 环境变量：`RATE_LIMIT_ENABLED`（开关，默认 false 与 11.1 一致保护现有测试）、`RATE_LIMIT_PER_MINUTE`（默认 60）、`RATE_LIMIT_UPLOAD_PER_MINUTE`（默认 10）
  - **FastAPI 0.139+ 兼容 patch**：`_patch_find_route_handler` 替换 `slowapi.middleware._find_route_handler`，深入 `_IncludedRouter.original_router.routes` 找 endpoint。未打 patch 时 `default_limits` 对所有 `/api/v1/*` 端点失效（slowapi 0.1.9 原实现因 `hasattr(route, "endpoint")` 为 False 找不到路由处理器）
- **设计取舍**：
  - **默认禁用（`RATE_LIMIT_ENABLED=false`）**：与 11.1 认证默认禁用一致，保护现有 720+ 测试不被限流误伤；生产部署显式 `RATE_LIMIT_ENABLED=true` 启用
  - **按 API Key 优先于 IP**：公司出口 IP 共享，按 IP 限流会误伤同公司不同用户。认证启用时按 key 更精确。认证禁用时回退 IP（开发/调试场景）
  - **上传端点单独更严**：单请求 5-30 秒，比问答重，单独 10/min 限制防刷接口
  - **内存级而非 Redis**：单实例部署足够。多副本时 slowapi 支持 Redis 后端，切换成本低（改 `storage_uri`）
  - **固定窗口而非滑动窗口**：slowapi 默认固定窗口，边界处可能短时双倍流量，当前规模可接受
  - **monkey-patch 而非 fork slowapi**：FastAPI 0.139+ 兼容问题已知，社区待修复；patch 不到 30 行，远小于 fork 维护成本
- **验收**：CI 三项全绿（Lint / Type Check / Test），781 个测试通过（含 45 新增限流测试）；超频请求返回 429 + `ErrorResponse` body + `Retry-After` / `X-RateLimit-*` 头

### 11.4 Docker Compose 一键部署

- **状态**：✅ 已完成（PR #70，Issue #69）
- **目标**：`docker compose up` 一键启动 API + Qdrant + PostgreSQL 三服务
- **技术方案**：
  - `Dockerfile`：`python:3.11-slim` + 多阶段复制 `uv` 二进制 + `uv sync --frozen --extra embedding --extra chinese` 装齐本地推理依赖；`libgomp1`（BM25/lightgbm）+ `curl`（healthcheck）；`uv sync --no-install-project` 利用层缓存
  - `docker-compose.yml`：api / qdrant / postgres 三服务，postgres 用 `postgres:15-alpine` + healthcheck，api 依赖 postgres healthy 后启动；三个命名卷 `rrag-postgres-data` / `rrag-qdrant-data` / `rrag-api-uploads` 持久化
  - `docker/entrypoint.sh`：先 `uv run alembic upgrade head` 执行数据库迁移，再 `exec uvicorn` 启动 API（`exec` 让 uvicorn 接管 PID 1 接收 SIGTERM 优雅退出）
  - `.dockerignore`：排除 `.git` / `.venv` / `data/` / `models/` / 测试缓存等，减小构建上下文
  - `.env.docker.example`：容器化部署环境变量示例
  - `pyproject.toml`：补 `psycopg[binary]>=3.1` 依赖（PostgreSQL 驱动，psycopg3 是 SQLAlchemy 2.0 推荐，与同步 Session 路径匹配）
  - `README.md`：新增 Docker 部署章节（前置要求 / 配置 / 启动 / 停止清理 / 数据持久化 / 与 Streamlit UI 配合）
- **设计取舍**：
  - **psycopg3 vs psycopg2 vs asyncpg**：选 `psycopg[binary]`——新官方推荐、协议更现代、与同步 Session 路径匹配；`asyncpg` 仅异步，与当前同步路由不匹配
  - **Streamlit UI 不容器化**：交接文档只要求 api/qdrant/postgres 三服务；UI 容器化后续按需追加，README 已说明本地运行 UI 指向容器化 API 的方式
  - **不引入非 root 用户**：生产级安全加固（非 root 用户、密钥管理、TLS）列入后续 Issue，本任务保持简洁
- **验收**：CI 三项全绿（Lint / Type Check / Test），628 个测试通过；全新机器上 `docker compose up -d --build` 后 API 自动迁移 schema 并启动

### 11.5 CI/CD 自动化部署

- **状态**：✅ 已完成（Issue #81，PR #82）
- **目标**：push 到 main 后（CI 全绿后）自动构建 Docker 镜像并推送到 GitHub Container Registry，可选 SSH 自动部署到生产服务器
- **技术方案**：
  - 新增 `.github/workflows/deploy.yml`：独立 CD workflow（与 `ci.yml` 职责分离）
    - 触发：`workflow_run`（CI 成功后自动）+ `workflow_dispatch`（手动）
    - `build-and-push` job：`docker/build-push-action@v6` 构建并推送镜像到 `ghcr.io/fufufu11/research-rag-assistant`，双标签 `:latest` + `:sha-<short>`，`cache-from/cache-to: type=gha` 复用 buildx 层缓存
    - `deploy` job：`appleboy/ssh-action@v1.2.0` SSH 登录服务器执行 `docker compose pull && up -d` + 健康检查（12 次 × 5 秒）
  - 新增 `docker-compose.prod.yml`：生产覆盖文件，`api` 服务引用 `ghcr.io/fufufu11/research-rag-assistant:${IMAGE_TAG:-latest}` 预构建镜像（不本地 build），支持 `IMAGE_TAG` 切换历史 sha 标签回滚
  - 新增 `tests/unit/test_deployment_config.py`：15 个测试验证 workflow 与 compose 配置的 YAML 语法与关键结构（权限 / 步骤 / 依赖 / 门控 / 镜像引用 / 健康检查脚本）
  - `pyproject.toml`：添加 `pyyaml>=6.0` dev 依赖（解析 YAML 配置文件）
  - `README.md`：新增「CI/CD 自动化部署」章节（流水线 / 镜像拉取 / Secrets 与 Variables 配置 / 手动触发）
- **设计取舍**：
  - **独立 `deploy.yml` 而非合并到 `ci.yml`**：CD 与 CI 职责分离，CI 关心代码质量，CD 关心交付；独立文件便于按需禁用部署
  - **`workflow_run` 触发而非 push 直接触发**：保证 CI 全绿后才构建镜像，避免推送坏代码到 Registry
  - **双标签（latest + sha-short）**：latest 方便部署方拉取，sha-short 支持版本追溯与回滚
  - **SSH 部署用 `ENABLE_SSH_DEPLOY` 变量门控**：未配置服务器时 workflow 仍能构建并推送镜像（价值前置），配置变量后自动启用部署（本地开发友好，不强制配置服务器）
  - **`docker-compose.prod.yml` 覆盖而非独立文件**：复用基础 `docker-compose.yml` 的 postgres/qdrant/volumes 配置，只覆盖 `api.image`，避免配置漂移
  - **GHA 缓存**：首次构建慢，后续复用 buildx 层缓存显著加速
  - **不引入 Helm/Kustomize**：docker compose 足够当前规模
- **验收**：CI 三项全绿（Lint / Type Check / Test），796 个测试通过（含 15 新增部署配置测试）；deploy workflow 结构经单元测试验证（权限 / 步骤 / 依赖 / 门控 / 镜像引用）
- **不在范围**：蓝绿部署 / 金丝雀发布（当前单实例规模不需要）、Kubernetes 部署（YAGNI）、回滚自动化（手动 `IMAGE_TAG=sha-xxx` 即可）、多环境（staging/prod）分离

### 11.6 生产安全加固

- **状态**：✅ 已完成（切片 A-F 全部完成，6 个 PR：#103 #105 #104 #106 #107 #108）
- **目标**：把开发级容器部署升级到生产可用——非 root 用户、密钥通过 docker secrets 文件挂载（不入环境变量）、Nginx TLS 反代终止 HTTPS
- **三个子方向**：
  1. **密钥管理升级**（切片 A #97 + 切片 C #99 + 切片 D #101）：`src/research_rag/secrets.py` 提供 `get_secret(name)` helper 优先读 `{NAME}_FILE` 文件内容（docker secrets 路径）fallback 到 `{NAME}` 环境变量（开发/CI 路径）；5 个文件 8 个密钥读取点从 `os.environ.get(...)` 替换为 `get_secret(...)`；docker-compose.prod.yml 配置 8 个 docker secrets 顶级块 + api/postgres 服务引用 + 7 个 `_FILE` 环境变量；postgres 用官方镜像原生 `POSTGRES_PASSWORD_FILE` 支持
  2. **非 root 容器**（切片 B #98）：Dockerfile 加 `USER 65532` + `groupadd/useradd/chown` 创建 UID 65532 的 app 用户（distroless 标准）+ `chown -R app:app /app` 让非 root 用户可读 `.venv`/源码 + 可写 `data/uploads`；entrypoint.sh 改用 `/app/.venv/bin/uvicorn` 和 `/app/.venv/bin/alembic` 直接调用绕开非 root 用户无权写 `~/.cache/uv` 的 cache 写权限问题
  3. **Nginx TLS 反代**（切片 E #100）：nginx + certbot 容器化 + Let's Encrypt webroot 模式自动签发与续期；nginx.conf 模板含 HTTP→HTTPS 301 重定向 + ACME webroot + `proxy_pass http://api:8000` + `${DOMAIN}` envsubst 占位；entrypoint 含占位自签证书（解决首次启动无证书 nginx 无法启动的鸡生蛋问题）+ crond 周期 reload nginx（每 6 小时读取续期后新证书）；certbot entrypoint 含 certbot certonly --webroot 首次签发 + certbot renew 续期 cron（每 12 小时检查）；docker-compose.prod.yml 加 nginx/certbot 服务 + 共享 webroot/证书卷 + api 用 `ports: !reset []` 清空 8000 端口发布强制走 TLS
- **设计取舍**：
  - **UID 65532 而非命名用户**：与 distroless 镜像惯例一致，避免 user namespace 冲突；`chown -R 65532 /app` 在 Dockerfile build 阶段完成，运行时无 root 权限
  - **webroot 模式而非 standalone**：不停服签发证书（standalone 需停 nginx 占用 80 端口）；nginx 独立 reload cron 而非跨容器信号通信（6 小时 reload 足够及时，避免跨容器信号复杂度）
  - **docker secrets 方案 A（代码层 helper）而非方案 B（pydantic-settings BaseSettings）**：YAGNI，当前 `os.environ.get` 调用点替换为 `get_secret` 足够；pydantic-settings 引入新抽象层，且不能覆盖 postgres 官方镜像原生 `_FILE` 支持。决策见 [ADR 0004](./adr/0004-docker-secrets-helper.md)
  - **postgres 用 `POSTGRES_PASSWORD_FILE` 而非自定义 entrypoint**：官方镜像原生支持，零代码改动；其他服务的密钥通过应用层 `get_secret` 读取
  - **保持向后兼容**：`get_secret` fallback env，开发/CI 不挂载 secrets 时行为不变；`POSTGRES_PASSWORD_FILE` 为空字符串时 Postgres 官方镜像忽略 `_FILE` 后缀，回退到 `POSTGRES_PASSWORD`
  - **docker secrets 用 `file:` 而非 swarm**：docker compose 非 swarm 模式支持 `secrets:` + `file:`，无需 swarm 集群；`${VAR_FILE:?error}` 模式在变量未设置时报错引导运维配置
- **技术方案**：6 个垂直切片（依赖图见 [handoff 20260726-1246](../.trae/handoffs/handoff-20260726-1246.md)）
  - **切片 A #97（已完成）**：`src/research_rag/secrets.py` 提供 `get_secret(name) -> str | None` helper——优先读 `{NAME}_FILE` 环境变量指向的文件内容（docker secrets 路径），无 `_FILE` 或文件不存在时回退到 `{NAME}` 环境变量（开发/CI 路径），两者均无返回 `None`；7 个单元测试覆盖五条行为路径（fallback env / file 优先 / file 缺失回退 / 空文件 / 仅空白文件）
  - **切片 B #98（已完成，PR #105）**：api 容器非 root 改造
    - `Dockerfile` 加 `RUN groupadd -r app && useradd -r -g app -u 65532 app && chown -R app:app /app`（创建 UID 65532 的 app 用户 + chown /app 让非 root 用户可读 `.venv`/源码 + 可写 `data/uploads`，必须在所有 COPY 之后执行确保 entrypoint.sh 也被 chown）
    - `Dockerfile` 加 `USER 65532`（在 chown 之后，否则 chown 以非 root 执行会失败）
    - `docker/entrypoint.sh` 从 `uv run uvicorn` / `uv run alembic` 改为 `/app/.venv/bin/uvicorn` / `/app/.venv/bin/alembic` 直接调用（非 root 用户无权写 `~/.cache/uv`，`uv run` 会因 cache 写入失败而崩溃，直接调用 venv 内可执行文件绕开）
    - 保留 `exec` 让 uvicorn 接管 PID 1，`docker stop` 能优雅关闭（SIGTERM 直接给 uvicorn 而非 sh）
    - 7 个新增单元测试（`tests/unit/test_dockerfile_non_root.py`）：`TestDockerfileNonRoot`（4 个：USER 65532 / groupadd+useradd / chown / 顺序约束）+ `TestEntrypointScript`（3 个：venv uvicorn / venv alembic / exec 保留）
    - 容器行为测试（`id` 命令 / 健康检查 / uploads 可写）需真实 Docker 构建后验证，本地 pytest 只验证文件结构与关键指令存在性
  - **切片 C #99（已完成）**：5 个文件 8 个密钥读取点从 `os.environ.get(...)` 替换为 `get_secret(...)`，覆盖 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LLM_API_KEY` / `DASHSCOPE_API_KEY` / `JINA_API_KEY` / `JUDGE_LLM_API_KEY` / `API_KEYS`；`docker-compose.yml` postgres 服务添加 `POSTGRES_PASSWORD_FILE: ${POSTGRES_PASSWORD_FILE:-}` 字段（Postgres 官方镜像原生支持 `_FILE` 后缀，设置后优先读文件内容作为密码，忽略 `POSTGRES_PASSWORD`）；24 个新增单元测试覆盖 `_FILE` 优先 / fallback env / 缺失返回 None 三条路径
  - **切片 D #101（已完成，PR #106）**：docker-compose.prod.yml 配置 8 个 docker secrets 挂载
    - 顶级 `secrets:` 块声明 8 个 secrets（`postgres_password` / `llm_api_key` / `judge_llm_api_key` / `api_keys` / `langfuse_public_key` / `langfuse_secret_key` / `dashscope_api_key` / `jina_api_key`），每个用 `file: ${VAR_FILE:?must point to host file path}` 指向宿主机路径（未设置时 `:?` 报错引导运维配置）
    - `api` 服务 `secrets:` 引用 7 个 secrets（除 `postgres_password`）+ `environment` 加 7 个 `{NAME}_FILE` 指向 `/run/secrets/<name>`，应用层 `get_secret` helper 优先读 `_FILE` 文件内容
    - `postgres` 服务 `secrets:` 引用 `postgres_password` + `POSTGRES_PASSWORD_FILE` 覆盖为 `/run/secrets/postgres_password`（Postgres 官方镜像读取文件内容作为密码）
    - 新增 `.env.docker.secrets.example` 示例文件（8 个 secrets 文件宿主机路径占位，运维复制为 `.env.docker.secrets` 后填入真实路径）
    - `.gitignore` 加 `.env.docker.secrets`（真实路径文件不入 git，`.example` 可提交）
    - 10 个新增单元测试（`tests/unit/test_deployment_config.py`）：`TestProdComposeDockerSecrets`（8 个：顶级 secrets 块 / 8 secrets 定义 / api 引用 7 / postgres 引用 password / POSTGRES_PASSWORD_FILE 路径 / api environment 7 个 _FILE / 服务引用一致性 / env 路径与 secret 名一致性）+ `TestDockerSecretsEnvExample`（2 个：example 文件存在 + 含 8 个 _FILE 变量 + 路径非空）
    - 已知局限：`api` 服务的 `DATABASE_URL` 仍用 base compose 的 `${POSTGRES_PASSWORD:-rrag}` 模式（生产环境需通过 entrypoint 脚本读 `POSTGRES_PASSWORD_FILE` 内容动态拼装，留待后续 issue 处理）
  - **切片 E #100（已完成，PR #107）**：nginx + certbot 容器化 + TLS 反代（Let's Encrypt webroot 模式）
    - 新增 `nginx/nginx.conf` 模板：HTTP server block（监听 80，`/.well-known/acme-challenge/` webroot + HTTP→HTTPS 301 重定向）+ HTTPS server block（监听 443，SSL 证书路径 + `proxy_pass http://api:8000` + 标准反代头 Host/X-Real-IP/X-Forwarded-For/X-Forwarded-Proto），用 `${DOMAIN}` 占位由 entrypoint `envsubst` 替换
    - 新增 `docker/nginx/entrypoint.sh`：`envsubst` 替换 `${DOMAIN}` 占位 → 若 `fullchain.pem` 不存在则 `openssl req -x509` 生成 1 天占位自签证书（让 nginx SSL server block 能加载配置启动）→ `crond` 周期 reload nginx（每 6 小时读取 certbot 续期后的新证书）→ `nginx -g 'daemon off;'` 前台运行
    - 新增 `docker/certbot/entrypoint.sh`：等待 nginx 启动 → 首次签发 `certbot certonly --webroot --webroot-path=/var/www/certbot -d ${DOMAIN} -m ${LETSENCRYPT_EMAIL} --agree-tos --no-eff-email --non-interactive` → `crond` 周期 `certbot renew`（每 12 小时检查，仅临近过期时实际续期）→ `crond -f` 前台运行
    - `docker-compose.prod.yml` 新增 nginx 服务（`nginx:alpine` + 80/443 端口 + nginx.conf/entrypoint/webroot/证书卷挂载 + `${DOMAIN}` env）+ certbot 服务（`certbot/certbot` + webroot/证书卷挂载 + `${DOMAIN}`/`${LETSENCRYPT_EMAIL}` env）+ 共享卷 `certbot-webroot` / `certbot-certs`
    - api 服务用 `ports: !reset []` 清空 base compose 的 8000 端口发布（compose merge 对 ports 取并集，需显式清空；生产仅 nginx 可访问 api，强制走 TLS）
    - 33 个新增单元测试（`tests/unit/test_deployment_config.py`）：扩展 `_load_yaml` 支持 compose-spec 的 `!reset`/`!override` 自定义 YAML 标签 + `TestNginxConfig`（8 个 nginx.conf 结构断言）+ `TestProdComposeNginxCertbot`（15 个 compose 服务/卷/端口断言）+ `TestNginxEntrypoint`（5 个 nginx entrypoint 结构断言）+ `TestCertbotEntrypoint`（5 个 certbot entrypoint 结构断言）
    - 设计决策：webroot 模式（不停服签发，standalone 需停 nginx）+ nginx 独立 reload cron（避免跨容器信号通信复杂度，6 小时 reload 足够及时）+ 占位自签证书（解决首次启动无证书 nginx 无法启动的鸡生蛋问题）
    - 不在范围：实际证书签发（部署运维文档说明，需真实域名）/ 泛域名 + dns-01 / HTTP/2 或 HTTP/3 / nginx 容器非 root 化
  - **切片 F #102（已完成，PR #108）**：文档同步收官——ROADMAP.md 标记阶段 11.6 完成 + 新增三个子方向（密钥管理升级 / 非 root 容器 / Nginx TLS 反代）总结 + 设计取舍（UID 65532 / webroot 模式 / docker secrets 方案 A）汇总 + 验收结果汇总；STATUS.md 标记阶段 11.6 完成 + 版本 v2.9 → v3.0；本切片为纯文档变更，无新增测试，905 测试基线零回归
- **验收**：
  - #97：CI 三项全绿，837 测试通过（830→837，+7 新增）
  - #99：CI 三项全绿，854 测试通过（837→854，+24 新增），830 基线零回归
  - #98：CI 三项全绿（Lint 10s / Test 29s / Type Check 40s），861 测试通过（854→861，+7 新增），854 基线零回归；Dockerfile 含 `USER 65532` + `groupadd/useradd/chown` + entrypoint.sh 改用 `/app/.venv/bin/uvicorn` 和 `/app/.venv/bin/alembic` + 保留 `exec`；容器行为测试（id/健康检查/uploads 可写）需真实 Docker 构建后验证
  - #101：CI 三项全绿（Lint 12s / Test 28s / Type Check 47s），871 测试通过（861→871，+10 新增），861 基线零回归；docker-compose.prod.yml 含顶级 `secrets:` 块 + 8 secrets 定义 + api 引用 7 + postgres 引用 password + POSTGRES_PASSWORD_FILE 指向 `/run/secrets/postgres_password` + api environment 7 个 _FILE + `.env.docker.secrets.example` 模板；容器行为测试（`ls /run/secrets/` / `env | grep -i key`）需真实 Docker 构建后验证
  - #100：CI 三项全绿，905 测试通过（871→905，+33 新增），871 基线零回归；nginx.conf 含 HTTP/HTTPS server block + ACME webroot + `proxy_pass http://api:8000`；docker-compose.prod.yml 加 nginx/certbot 服务 + 共享 webroot/证书卷 + api 用 `ports: !reset []` 清空 8000 端口；nginx & certbot entrypoint 含 envsubst + 占位自签证书 + crond reload + certbot certonly --webroot + certbot renew cron；容器行为测试（实际证书签发需真实域名）留待部署运维验证
  - #102：CI 三项全绿（纯文档变更，905 测试基线零回归）；ROADMAP.md 阶段 11.6 标记 ✅ 已完成 + 新增三个子方向总结 + 设计取舍汇总 + 验收结果汇总；STATUS.md v2.9 → v3.0 标记阶段 11.6 完成
  - **阶段 11.6 总计**：6 个切片全部完成，830 → 905 测试（+75 新增，全部零回归），CI 三项全绿；6 个 PR（#103 #105 #104 #106 #107 #108）依次 squash 合并到 main
- **不在范围**：Vault / SOPS / Sealed Secrets 等外部密钥管理系统（YAGNI，docker secrets 足够当前规模）、双向 TLS（mTLS）、WAF（Web Application Firewall）

---

## UI 体验优化阶段二（#109-#113）

基于 ChatGPT 界面截图的二轮 UI 迭代。阶段一（Issue #72）建立了 ChatGPT 风格的左右分栏基础布局；本阶段在此基础上做 5 项体验改进，全部 squash 合并到 main。

| 序号 | 任务 | 状态 | 预期收益 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| #109 | 左侧导航重构（图标分组+可折叠会话/文档列表） | ✅ 已完成（commit `5b6cbef`） | 信息密度提升 + 视觉层次清晰 | 无 | 高 |
| #112 | 输入栏「+」上传按钮+底部免责声明 | ✅ 已完成（commit `e589cc0`） | 上传统一入口 + 风险提示 | #109 | 高 |
| #111 | 对话区居中+宽度收窄布局 | ✅ 已完成（commit `012624a`） | 宽屏下阅读体验改善 | 无 | 中 |
| #113 | AI 回复复制按钮 | ✅ 已完成（PR #117，commit `9aba590`） | 一键复制答案 | 无 | 中 |
| #110 | 顶部模型选择下拉（占位） | ✅ 已完成（PR #118，commit `cc27531`） | 视觉占位 + 真切换预留接口 | 无 | 低 |

### #109 左侧导航重构

- **状态**：✅ 已完成（commit `5b6cbef`）
- **目标**：左侧导航视觉密度过高、信息层次不清——重构为图标分组 + 可折叠列表
- **技术方案**：
  - 新增 `_render_nav_section` / `_is_nav_section_expanded` / `_is_sidebar_collapsed` 纯函数
  - 上层分组：新建会话 / 搜索会话 / 历史会话列表（可折叠，默认展开）/ 文档列表（可折叠，默认展开，支持选择/删除但不上传）
  - 下层分组：设置 / 帮助
  - 折叠状态缓存到 `st.session_state`，rerun 时保留
- **设计取舍**：
  - **文档上传移到输入栏**：本 ticket 只做导航重构，上传入口移除留给 #112 输入栏「+」按钮统一接管，避免双入口
  - **可折叠默认展开**：常用列表（历史会话/文档）默认展开降低首次访问成本，次要列表（设置/帮助）放底部下层分组
- **验收**：6 个新增单元测试（`tests/unit/test_ui_nav_state.py`）覆盖 `_render_nav_section` / `_is_nav_section_expanded` / `_is_sidebar_collapsed` 行为路径

### #112 输入栏「+」上传按钮+底部免责声明

- **状态**：✅ 已完成（commit `e589cc0`）
- **目标**：上传入口统一到输入栏 + 底部加风险提示免责声明
- **技术方案**：
  - 新增 `_render_input_toolbar` / `_is_valid_pdf_filename` 纯函数 + `_UPLOAD_DISCLAIMER` 常量
  - 输入栏左侧「+」按钮触发 PDF 上传（与 #109 配合：#109 移除左侧导航上传入口，#112 在输入栏接管）
  - 底部免责声明：「AI 可能出错，请核查重要信息」
  - `_is_valid_pdf_filename` 做前端白名单校验（仅 `.pdf`），与后端 `api/security.py` 双重白名单形成纵深防御
- **设计取舍**：
  - **免责声明放底部而非每次问答后**：避免对用户造成持续打断，与 ChatGPT 一致
  - **前端校验只是 UX 改善**：真正安全靠后端 `validate_upload_file`，前端校验绕过不构成安全风险
- **验收**：8 个新增单元测试（`tests/unit/test_ui_upload.py`）覆盖 `_render_input_toolbar` / `_is_valid_pdf_filename` / `_UPLOAD_DISCLAIMER` 内容

### #111 对话区居中+宽度收窄布局

- **状态**：✅ 已完成（commit `012624a`）
- **目标**：宽屏下对话气泡横向铺满导致阅读疲劳——居中 + 最大宽度收窄
- **技术方案**：
  - 新增 `_get_chat_layout_css` 纯函数返回 CSS 字符串
  - 用 `st.markdown(..., unsafe_allow_html=True)` 注入 `<style>` 块覆盖 Streamlit 默认布局
  - 对话区最大宽度收窄到 ~768px（与 ChatGPT 一致），居中显示
- **设计取舍**：
  - **CSS 注入而非 Streamlit 原生布局参数**：Streamlit 不暴露 column max-width 控制，CSS 注入是社区惯用法
  - **只收窄对话区不收窄侧栏**：侧栏信息密度高，保持默认宽度
- **验收**：5 个新增单元测试（`tests/unit/test_ui_chat_layout.py`）覆盖 `_get_chat_layout_css` 返回内容关键字断言

### #113 AI 回复复制按钮

- **状态**：✅ 已完成（PR #117，commit `9aba590`）
- **目标**：用户需手动选中文本复制，体验差——加一键复制按钮
- **技术方案**：
  - 新增 `_render_copy_button` / `_strip_markdown_to_plain_text` 纯函数
  - 用 `streamlit.components.v1.html` 注入 `<button>` + `navigator.clipboard.writeText` JS
  - 复制成功后 Toast 提示（`streamlit.components.v1.html` 注入 `alert()` 简单实现，避免引入 streamlit-toast 等额外依赖）
  - 复制内容为剥离 Markdown 标记后的纯文本（`_strip_markdown_to_plain_text` 用正则去 `**bold**` / `*italic*` / `[text](url)` / `` `code` `` 等标记）
- **设计取舍**：
  - **复制纯文本而非 Markdown 源文本**：用户粘贴到 Word/微信等场景下纯文本更通用，Markdown 源文本对普通用户无意义
  - **用 `navigator.clipboard.writeText` 而非 `document.execCommand('copy')`**：前者是现代浏览器标准 API，后者已 deprecated
  - **不用 `st.button` 而用 `components.v1.html`**：`st.button` 点击会触发 rerun，破坏流式输出体验
- **验收**：8 个新增单元测试（`tests/unit/test_ui_copy_button.py`）覆盖 `_render_copy_button` HTML 结构 + `_strip_markdown_to_plain_text` 5 类标记剥离

### #110 顶部模型选择下拉（占位）

- **状态**：✅ 已完成（PR #118，commit `cc27531`）
- **目标**：顶部加模型选择下拉占位，让用户知道「未来可切换模型」
- **技术方案**：
  - 新增 `_render_model_dropdown` / `_get_current_model_name` / `_get_model_dropdown_options` 纯函数
  - `st.selectbox` + `disabled=True` 单元素 options 实现「展示但不切换」的占位下拉
  - `_get_current_model_name` 从 `LLM_MODEL` 环境变量读当前模型名（fallback `"deepseek-chat"`）
  - `_get_model_dropdown_options` 返回 `[当前模型名]` 单元素列表（占位模式下 options 只有一个）
- **设计取舍**：
  - **占位而非真切换**：真切换需后端补 `/api/v1/config` 端点返回可用模型列表 + 用户偏好持久化，独立 issue 处理；本 ticket 只做 UI 占位避免阻塞
  - **`disabled=True` 而非「假按钮」**：selectbox 视觉与未来真切换一致，避免占位变成「装饰按钮」被误以为是其他功能
- **验收**：6 个新增单元测试（`tests/unit/test_ui_model_dropdown.py`）覆盖 `_render_model_dropdown` + `_get_current_model_name` 环境变量路径 + `_get_model_dropdown_options` 单元素列表

### 阶段二总计

- **5 个 ticket 全部完成**，905 → 938 测试（+33 新增，零回归），CI 三项全绿
- **5 个 PR 依次 squash 合并到 main**：#109 → #112 → #111 → #113 (PR #117) → #110 (PR #118)
- **不在范围**：模型真切换（需后端补 `/api/v1/config` 端点）、用户登录页（需 JWT 用户系统）、移动端响应式适配、暗色模式

---

## 阶段 12：文档解析升级（长期）

| 序号 | 任务 | 状态 | 预期收益 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| 12.1 | 表格感知切分 | 待实施 | 表格不被切碎 | 无 | 中 |
| 12.2 | 公式识别（LaTeX） | 待实施 | 公式语义保留 | 无 | 低 |
| 12.3 | 版面分析 | 待实施 | 标题/正文/图表结构化切分 | 无 | 低 |

### 12.1 表格感知切分

- **目标**：识别表格结构，避免表格被切分到多个 chunk
- **技术方案**：用 `camelot` 或 `pdfplumber` 提取表格，表格作为独立 chunk
- **验收**：含表格的 PDF 解析后表格完整保留

### 12.2 公式识别

- **目标**：提取 LaTeX 公式并保留在 chunk 中
- **技术方案**：`pymupdf` 的 `page.get_text("dict")` 识别公式块，或用 `pix2tex` OCR
- **验收**：公式类问题的检索效果改善

### 12.3 版面分析

- **目标**：识别标题/正文/图表/页眉页脚，结构化切分
- **技术方案**：`layoutparser` 或 `pymupdf` 的版面分析 API
- **验收**：切分结果按逻辑结构组织，而非纯字符数

---

## 建议执行顺序

```
近期（1-2周）:
  8.2 跨页切分 → 8.3 混合检索
  ↓
短期（2-4周）:
  9.1 流式输出 → 11.4 Docker 部署 → 11.1 认证鉴权 → 10.1 可观测性
  ↓
中期（1-2月）:
  9.3 答案质量评测 → 10.2 反馈闭环 → 11.2/11.3 安全加固 → 11.5 CI/CD
  ↓
长期:
  12.1 表格切分 → 12.2 公式识别 → 12.3 版面分析
```

**关键里程碑**：
- 完成 8.2 + 8.3 + 9.1 + 11.1 + 11.4 后，系统达到"小团队内部可用"的最小上线标准
- 完成阶段 10-11 全部后，达到"对外服务"标准
