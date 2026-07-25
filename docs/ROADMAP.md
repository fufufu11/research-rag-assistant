# 上线路线图

> 本文档记录"科研文献可溯源智能问答系统"从 Demo 到可上线系统的演进计划。
> 每个阶段对应若干 GitHub Issue + PR，完成后更新状态。

## 当前状态

- **已覆盖阶段**：阶段 0-7（基础功能）+ 阶段 8.1（Reranker 重排序）+ 阶段 8.2（跨页切分）+ 阶段 8.3（BM25 混合检索）+ 阶段 8.4（EMBEDDING_MODEL 环境变量修复 + bge-m3 可选集成 + 中文论文评测）+ 阶段 9.1（流式输出 SSE）+ 阶段 9.2（多轮对话）+ 阶段 9.3（答案质量评测）+ 阶段 10.1（可观测性 Langfuse）+ 阶段 10.2（用户反馈闭环）+ 阶段 10.3（性能优化）+ 阶段 11.4（Docker Compose 一键部署）+ 阶段 11.1（API Key 认证鉴权）+ UI 体验优化（Issue #72：ChatGPT 风格布局 + 多文档会话范围锁定 Bug 修复）
- **测试**：650+ 个单元测试通过（API 测试需 Qdrant，CI 上全绿；UI 层 app.py 无单元测试，与既有风格一致）
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

- **状态**：✅ 已完成（PR #60，Issue #59）
- **目标**：用户对答案点赞/点踩，记录到 DB 用于持续优化
- **技术方案**：
  - `Feedback` ORM 模型：`request_id` 唯一主关联键 + 可空 `message_id` 外键（`ondelete=SET NULL`）+ `rating` 枚举（like/dislike）+ `comment`（最长 2000 字符）；`FeedbackRating` 枚举 + `FeedbackNotFoundError` 异常
  - `FeedbackRepository`：`upsert` / `get_by_request_id` / `list`（按 rating / conversation_id 筛选）/ `delete`，只 `flush` 不 `commit`（事务由路由层显式控制）
  - Alembic 迁移：创建 `feedback` 表
  - 路由直接调 Repository，不新建 `FeedbackService`（避免空模块）
  - `request_id` 作为主关联键的决策与已知局限见 [ADR 0001](./adr/0001-request-id-as-feedback-key.md)
- **交付**：`src/research_rag/db/models.py`（Feedback / FeedbackRating / FeedbackNotFoundError）+ `src/research_rag/db/repositories.py`（FeedbackRepository）+ `alembic/versions/2026_07_24_1413-aa7a10e898fd_add_feedback_table_for_user_feedback_.py` + `src/research_rag/api/routes/feedback.py`（4 个端点）+ `CONTEXT.md` 术语表 + `docs/adr/0001-request-id-as-feedback-key.md` + 30 个单元测试（Repository 12 + API 18）
- **验收**：DB 可查询某答案的反馈，支持按赞/踩筛选；`POST /api/v1/feedback` Upsert（201 新建 / 200 更新）、`GET /api/v1/feedback/{request_id}`（200 / 404）、`GET /api/v1/feedback?rating=&conversation_id=&limit=`、`DELETE /api/v1/feedback/{request_id}`（204 / 404）端到端测试通过
- **不在范围**：Streamlit 前端点赞/点踩按钮（后端已就绪，前端接入待后续）

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
| 11.3 | API 限流 | 待实施 | 防止滥用 | 11.1 | 中 |
| 11.4 | Docker Compose 一键部署 | ✅ 已完成（PR #70，Issue #69） | 容器化部署 api/qdrant/postgres 三服务 | 无 | 高 |
| 11.5 | CI/CD 自动化部署 | 待实施 | push 到 main 自动部署 | 11.4 | 中 |

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

- **目标**：防止滥用，保护服务稳定性
- **技术方案**：`slowapi` 库或 Nginx 限流，按用户/IP 限制请求频率
- **验收**：超频请求返回 429

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

- **目标**：push 到 main 自动构建镜像并部署
- **技术方案**：GitHub Actions 构建镜像 → 推送 Registry → SSH 部署到服务器
- **验收**：合并 PR 后 5 分钟内新版本上线

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
