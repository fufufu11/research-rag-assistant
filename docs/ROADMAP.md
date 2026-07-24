# 上线路线图

> 本文档记录"科研文献可溯源智能问答系统"从 Demo 到可上线系统的演进计划。
> 每个阶段对应若干 GitHub Issue + PR，完成后更新状态。

## 当前状态

- **已覆盖阶段**：阶段 0-7（基础功能）+ 阶段 8.1（Reranker 重排序）+ 阶段 8.2（跨页切分）+ 阶段 8.3（BM25 混合检索）+ 阶段 8.4（EMBEDDING_MODEL 环境变量修复 + bge-m3 可选集成 + 中文论文评测）+ 阶段 9.1（流式输出 SSE）+ 阶段 9.2（多轮对话）+ 阶段 9.3（答案质量评测）+ 阶段 10.1（可观测性 Langfuse）
- **测试**：530+ 个单元测试通过（API 测试需 Qdrant，CI 上全绿）
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
| 10.2 | 用户反馈闭环 | 待实施 | 点赞/点踩记录到 DB | 9.3 | 中 |
| 10.3 | 性能优化 | 待实施 | Embedding 缓存、并发检索、Qdrant 索引调优 | 无 | 中 |
| 10.4 | 多语言支持（bge-m3） | ✅ 已提前至 8.4 完成（Issue #42） | 中英文混合场景统一 | 8.2 | 低 |

### 10.1 可观测性

- **状态**：✅ 已完成（PR #56，Issue #55）
- **目标**：追踪每次问答的完整调用链（检索 → 重排 → LLM 生成），记录延迟、Token 消耗、失败率
- **技术方案**：Langfuse（开源自部署）+ LangChain CallbackHandler 集成；环境变量开关 no-op 优先（`LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` / `HOST` 三项非空才启用，未配置时零开销）
- **交付**：`src/research_rag/observability.py`（`LangfuseConfig` / `observe` / `get_current_langchain_handler` / `_build_run_config` / `flush`）+ `tests/unit/test_observability.py`（25 个单元测试覆盖 no-op 与启用路径）+ `docker-compose.langfuse.yml` 自部署模板；`QaService.answer` / `answer_stream` / `_prepare_contexts` 添加 `@observe` 装饰器，`run_config` 透传，`app.py` lifespan finally 调用 `flush_langfuse` 避免异步队列丢失
- **验收**：dashboard 可查看每次请求的检索结果、重排前后对比、LLM 输入输出；未配置环境变量时功能正常无副作用

### 10.2 用户反馈闭环

- **目标**：用户对答案点赞/点踩，记录到 DB 用于持续优化
- **技术方案**：新增 `feedback` 表，API 端点 `POST /api/v1/feedback`，前端按钮
- **验收**：DB 可查询某答案的反馈，支持按赞/踩筛选

### 10.3 性能优化

- **目标**：降低 P95 延迟，提升并发能力
- **技术方案**：
  - Embedding 缓存：相同文本不重复计算（Redis 或磁盘缓存）
  - 并发检索：多文档索引用 `asyncio.gather` 并行
  - Qdrant 索引调优：HNSW 参数（`m`、`ef_construct`、`ef_search`）
- **验收**：P95 延迟降低 50%，并发 10 请求无阻塞

### 10.4 多语言支持

- **目标**：换用 `bge-m3` 统一中英文场景
- **技术方案**：替换默认 Embedding 模型，重新索引已有文档
- **风险**：bge-m3 模型较大（约 2.2GB），首次下载耗时
- **验收**：中英文混合查询场景下 Hit@5 不低于当前英文场景

---

## 阶段 11：安全与部署（中期）

| 序号 | 任务 | 状态 | 预期收益 | 依赖 | 优先级 |
|---|---|---|---|---|---|
| 11.1 | 认证鉴权（API Key / JWT） | 待实施 | 防止未授权访问 | 无 | 高 |
| 11.2 | 输入过滤与文件校验 | 待实施 | 防注入、文件类型/大小限制 | 无 | 高 |
| 11.3 | API 限流 | 待实施 | 防止滥用 | 11.1 | 中 |
| 11.4 | Docker Compose 一键部署 | 待实施 | 容器化部署 | 无 | 高 |
| 11.5 | CI/CD 自动化部署 | 待实施 | push 到 main 自动部署 | 11.4 | 中 |

### 11.1 认证鉴权

- **目标**：API 需鉴权才能访问
- **技术方案**：API Key（简单场景）或 JWT（多用户场景），FastAPI `Depends` + `HTTPBearer`
- **验收**：未认证请求返回 401，认证后正常访问

### 11.2 输入过滤与文件校验

- **目标**：防止恶意文件上传和注入攻击
- **技术方案**：
  - 文件类型校验：白名单（仅允许 PDF）
  - 文件大小限制：如 50MB
  - SQL 注入防护：SQLAlchemy 参数化查询（已实现）
  - Prompt 注入：过滤用户输入中的特殊指令
- **验收**：上传非 PDF 文件返回 415，超大文件返回 413

### 11.3 API 限流

- **目标**：防止滥用，保护服务稳定性
- **技术方案**：`slowapi` 库或 Nginx 限流，按用户/IP 限制请求频率
- **验收**：超频请求返回 429

### 11.4 Docker Compose 一键部署

- **目标**：`docker compose up` 一键启动 API + Qdrant + DB
- **技术方案**：
  - `Dockerfile`：Python 3.11 + uv + 应用代码
  - `docker-compose.yml`：api / qdrant / postgres 三服务
  - 环境变量通过 `.env` 注入
- **验收**：全新机器上 `docker compose up` 后系统可用

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
