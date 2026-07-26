# 项目状态

## 当前状态

`v2.1` — 阶段 0-10.3 + 11.1 + 11.2 + 11.3 + 11.4 + 11.5 全部完成，阶段 11 安全与部署收官；阶段 10.2 前端补充（PR #87）完成反馈按钮接入，805 条测试通过，CI 三项全绿。

## 已完成功能

### PDF 解析与切分

- 按页提取文本并保留页码（PyMuPDF）
- 跨页切分：合并连续页文本后统一切分（阶段 8.2），`Chunk` 用 `start_page` + `end_page` 溯源
- 页内文本清洗与带重叠的 Chunk 切分（LangChain `RecursiveCharacterTextSplitter`）
- 异常处理：文件不存在、损坏 PDF、空 PDF

### Embedding 与向量检索

- 基于 LangChain 的 Embedding 适配器（默认 `BAAI/bge-small-zh-v1.5`，中文优化；英文或中英文混合场景可通过 `EMBEDDING_MODEL` 环境变量切换为 `BAAI/bge-small-en-v1.5` 或 `BAAI/bge-m3` 多语言模型）
- 三模式 provider：`local` / `dashscope` / `jina`，通过 `EMBEDDING_PROVIDER` 环境变量切换（阶段 8.4）
- Top-K 向量检索，按余弦相似度降序
- Qdrant 向量数据库（未配置时回退到 `InMemoryVectorStore`）
- BGE Reranker 重排序（Cross-Encoder 两阶段检索，默认关闭，`RERANKER_ENABLED=true` 启用）
- BM25 混合检索（`rank_bm25` + 向量 + 加权 RRF 融合，`vector_weight=2.0`，阶段 8.3）

### 大模型问答与可靠引用

- OpenAI 兼容协议客户端，超时与重试由 httpx 实现指数退避
- 服务端引用映射：模型输出 `[C1]` 编号，服务端映射真实文档名、页码与片段
- 证据不足时拒绝猜测（`[INSUFFICIENT_EVIDENCE]`）
- 流式输出（SSE）：FastAPI `StreamingResponse` + LangChain `astream` + SSE 协议（token/done/error 三类事件），Streamlit `st.write_stream` 接收（阶段 9.1）
- 多轮对话：DB 持久化会话历史 + 查询改写 + 历史截断双重保护 + 会话级文档范围锁定，流式/非流式双路径都支持（阶段 9.2）

### FastAPI 与数据库

- 文档管理 API：上传 / 列表 / 详情 / 删除
- 问答 API：`POST /api/v1/queries`（支持 `stream=true` 流式、`conversation_id` 多轮）
- 会话管理 API：`POST/GET/DELETE /api/v1/conversations`、`GET /api/v1/conversations/{id}/messages`（阶段 9.2）
- 用户反馈 API：`POST /api/v1/feedback`（Upsert，201 新建 / 200 更新）、`GET /api/v1/feedback/{request_id}`、`GET /api/v1/feedback?rating=&conversation_id=&limit=`（列表筛选）、`DELETE /api/v1/feedback/{request_id}`（阶段 10.2）
- SQLAlchemy 2.0 数据模型（Document / Chunk / Conversation / Message / Feedback）+ Alembic 迁移
- sha256 去重、文件落盘、状态机（`pending → processing → ready / failed`）
- 全局异常处理器统一映射业务异常到 HTTP 状态码

### 演示界面

- Streamlit 界面（ChatGPT 风格布局，Issue #72）：左右分栏（左 25% 会话+文档管理，右 75% 聊天区），`st.chat_message` user/assistant 交替气泡 + `st.chat_input` 回车发送自动清空，流式输出用 `st.write_stream` 渲染到 assistant 气泡内
- 文档范围锁定：左侧文档多选 + 「新建会话」按钮，新建时调 `client.create_conversation(document_ids=selected_ids)` 锁定范围（修复多文档会话只检索到一篇的 Bug）；单轮问答也支持文档范围限定
- 引用卡片标注来源文档名（`[C1] paper1.pdf · 第3页`），多文档场景可直观区分
- 通过 HTTP 调用 FastAPI，不直接 import 业务层

### 评测与质量优化

- 30 条英文检索评测数据集（3 篇英文 AI 经典论文，多论文合并库检索）
- 30 条中文检索评测数据集（3 篇 ChinaXiv 中文 AI 论文，阶段 8.4）
- Hit@1 / Hit@5 / MRR / 平均检索耗时指标
- 5 组参数对比实验，支持中英文 Embedding 模型切换
- 英文最优 Hit@5=76.7%、MRR=0.607（chunk-500-overlap-0 + bge-small-en + BM25 + reranker），详见 [评测报告](./evaluation_report.md)
- 中文最优 Hit@5=90.0%、MRR=0.783（chunk-500-overlap-160 + bge-small-zh + reranker），详见 [中文评测报告](./evaluation_report_zh.md)
- 答案质量评测（LLM-as-judge 自实现，阶段 9.3）：四项指标 1-5 分（忠实度 / 相关性 / 完整性 / 引用正确性），纯函数与编排分离，judge LLM 支持 `JUDGE_LLM_*` 环境变量覆盖避免同模型自评偏差
- 答案质量结果（DeepSeek-V3.2 评测）：英文忠实度=5.00 相关性=4.96 完整性=4.62 引用正确性=4.54；中文忠实度=5.00 相关性=5.00 完整性=4.97 引用正确性=5.00，详见 [答案质量报告](./answer_quality_report.md) / [中文报告](./answer_quality_report_zh.md)

### 可观测性

- Langfuse 全链路追踪（阶段 10.1）：自部署 + LangChain CallbackHandler 集成
- 环境变量开关 no-op 优先：`LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` / `HOST` 三项非空才启用，未配置时零开销
- `QaService.answer` / `answer_stream` / `_prepare_contexts` 添加 `@observe` 装饰器，`run_config` 透传给 `rewrite_query` / `answer_question` / `answer_with_messages`
- `app.py` lifespan finally 调用 `flush_langfuse` 避免异步队列丢失
- 自部署模板 `docker-compose.langfuse.yml`

### 用户反馈闭环

- 用户对答案点赞/点踩并记录到 DB，用于持续优化（阶段 10.2 后端 + 前端补充）
- `Feedback` ORM 模型：`request_id` 唯一主关联键 + 可空 `message_id` 外键（`ondelete=SET NULL`）+ `rating` 枚举（like/dislike）+ `comment`（最长 2000 字符）；`FeedbackRating` 枚举 + `FeedbackNotFoundError` 异常
- `FeedbackRepository`：`upsert` / `get_by_request_id` / `list`（按 rating / conversation_id 筛选）/ `delete`，只 `flush` 不 `commit`（事务由路由层显式控制）
- Alembic 迁移：创建 `feedback` 表
- API：`POST /api/v1/feedback`（Upsert，201 新建 / 200 更新）、`GET /api/v1/feedback/{request_id}`（200 / 404）、`GET /api/v1/feedback?rating=&conversation_id=&limit=`（列表筛选）、`DELETE /api/v1/feedback/{request_id}`（204 / 404）
- Schemas：`FeedbackCreate` / `FeedbackRead` / `FeedbackList`（Pydantic v2）
- 路由直接调 Repository，不新建 `FeedbackService`（避免空模块）
- `request_id` 作为主关联键的决策与已知局限见 [ADR 0001](./adr/0001-request-id-as-feedback-key.md)
- 后端 30 个单元测试（Repository 12 + API 18，端到端验证）
- **前端补充**（PR #87 / Issue #86）：`ApiClient.submit_feedback` / `delete_feedback` 封装 POST/DELETE；`_render_feedback_buttons` 在 assistant 气泡内渲染点赞/点踩按钮，状态缓存在 `st.session_state`，Upsert 语义切换赞↔踩，再次点击撤销 DELETE，点踩后展开 `st.text_area` 收集文字评论；9 个新增单元测试（`TestSubmitFeedback` 6 + `TestDeleteFeedback` 3）
- 不在范围：历史消息反馈（后端 `Message` 模型无 `request_id` 字段，前端 `MessageInfo` 也缺失，待后续 Issue 跟踪）

### 性能优化

- 检索阶段 P95 延迟优化（阶段 10.3）：BM25 索引缓存 + 并发检索（Qdrant 路径）
- `BM25IndexCache`：进程级缓存经 FastAPI 依赖注入到 `QaService`，签名 `tuple(sorted(doc_ids)) + total_chunks` 自动失效（文档增删/重新切分时自动重建），不耦合 `DocumentService`
- 并发检索：`_retrieve_hybrid` 内用 `concurrent.futures.ThreadPoolExecutor(max_workers=2)` 并行 BM25 与 Qdrant 检索（numpy 打分 + 网络 I/O 都释放 GIL）；InMemory 测试路径保持串行
- 基准脚本 `scripts/benchmark_retrieval.py`（走真实 `QaService` 路径，P50/P95/P99，冷热两遍）与 `scripts/verify_bm25_cache.py`（对比 `--no-cache` 基线与缓存路径）
- 验收结果：检索阶段 P95 从 1727.9ms 降至 408.7ms，降幅 76.3%（远超 ≥50% 标准）
- P95 验收口径（仅检索阶段，不含 LLM/reranker）与设计决策见 [ADR 0002](./adr/0002-retrieval-stage-p95-metric.md)

### Docker 部署

- Docker Compose 一键部署（阶段 11.4）：`docker compose up -d --build` 启动 API + Qdrant + PostgreSQL 三服务
- `Dockerfile`：`python:3.11-slim` + 多阶段复制 `uv` + `uv sync --frozen --extra embedding --extra chinese`；`libgomp1`（BM25/lightgbm）+ `curl`（healthcheck）
- `docker-compose.yml`：api / qdrant / postgres 三服务 + postgres healthcheck + 三个命名卷（`rrag-postgres-data` / `rrag-qdrant-data` / `rrag-api-uploads`）持久化
- `docker/entrypoint.sh`：先 `alembic upgrade head` 迁移 schema，再 `exec uvicorn` 启动 API（`exec` 接管 PID 1 优雅接收 SIGTERM）
- `pyproject.toml`：补 `psycopg[binary]>=3.1`（PostgreSQL 驱动，psycopg3 是 SQLAlchemy 2.0 推荐）
- 部署文档见 [README.md](../README.md)「Docker 部署」章节；Streamlit UI 不容器化，本地运行指向容器化 API

### 认证鉴权

- API Key 认证（阶段 11.1）：所有 `/api/v1/*` 端点接入认证，防止未授权访问
- 新增 `src/research_rag/api/auth.py`：`verify_api_key` 依赖函数，`HTTPBearer(auto_error=False)` 从 `Authorization: Bearer <key>` 提取 token，与 `API_KEYS` 环境变量配置的有效 key 集合比对
- `app.include_router(..., dependencies=[Depends(verify_api_key)])` 集中挂载，所有路由（documents / queries / conversations / feedback）自动生效，无需改每个路由文件
- 环境变量 `API_KEY_ENABLED` 控制开关（默认禁用，开发友好，向后兼容现有测试）；`API_KEYS` 逗号分隔配置多个有效 key
- `secrets.compare_digest` 恒定时间比对防时序攻击；启用但 `API_KEYS` 为空时安全失败（全部 401，避免认证形同虚设）
- Streamlit `ApiClient` 从 `API_KEY` 环境变量读 key，`_get_headers()` 在所有 HTTP 调用（`_request` + `ask_question_stream`）携带 `Authorization: Bearer <key>`；未设置时不携带（兼容禁用认证场景）
- 31 个新增单元测试（纯函数 + 集成路径 + ApiClient），现有 620+ 测试零改动
- 不在范围：用户注册/登录系统 + JWT（后续独立 Issue）、权限分级、API Key 签发/轮换管理界面

### 输入过滤与文件校验

- 输入校验安全模块（阶段 11.2）：PDF 白名单 + 文件大小限制 + Prompt 注入过滤，防止恶意文件上传与注入攻击
- 新增 `src/research_rag/api/security.py`：与 `api/auth.py` 对称，集中管理输入校验逻辑
- **文件类型校验**：扩展名 + `content_type` 双重白名单（仅允许 PDF），非 PDF 返回 415；`content_type` 缺失时只校验扩展名（兼容 Streamlit 等不设置 content_type 的客户端）
- **文件大小校验**：`MAX_UPLOAD_MB` 环境变量（默认 20MB，复用已有变量），超过返回 413（`HTTP_413_CONTENT_TOO_LARGE`）
- **Prompt 注入过滤**：10 类常见注入模式正则匹配（`ignore previous` / `disregard` / `you are` / `act as` / `system:` / `<|im_start|>` / `[/inst]` / `reveal instructions` / `jailbreak` / `DAN`），命中即返回 400（不净化后放行，避免语义漂移）
- 路由层调用：`documents.py` 调 `validate_upload_file`、`queries.py` 调 `validate_question`（在 stream 分支前完成，覆盖流式/非流式两条路径）
- 环境变量 `INPUT_VALIDATION_ENABLED` 控制开关（**默认启用**，与 11.1 认证默认禁用相反——安全功能默认开是最佳实践，且校验只影响非法请求不影响合法用户）
- 77 个新增单元测试（纯函数 + 路由集成），现有 650+ 测试零改动
- 不在范围：文件内容深度校验（PDF 内嵌 JS/病毒扫描）、复杂 Prompt 注入防御（LLM 检测）、XSS 过滤（Streamlit 已转义）

### API 限流

- API 请求限流（阶段 11.3）：`slowapi` 库按用户/IP 限制请求频率，超频返回 429，防止滥用消耗 LLM/检索资源
- 新增 `src/research_rag/api/rate_limit.py`：`Limiter` 模块级单例 + `default_limits` callable lambda（请求时动态读环境变量，支持 monkeypatch 测试）
- `rate_limit_key` 函数：认证启用按 `key:<token>`（公司出口 IP 共享，按 key 更精确），认证禁用按 `ip:<ip>`（X-Forwarded-For 首段或 client.host）
- `app.py` 集中挂载：`configure_limiter()` + `app.state.limiter` + `SlowAPIMiddleware` + `RateLimitExceeded` 异常处理器（返回 `ErrorResponse` JSON 体 + `Retry-After` / `X-RateLimit-*` 头）
- 上传端点 `POST /api/v1/documents` 单独更严限流（`@limiter.limit` 装饰器，默认 10/min vs 全局 60/min），避免上传刷接口拖垮服务
- 环境变量：`RATE_LIMIT_ENABLED`（开关，默认 false 与 11.1 一致保护现有测试）、`RATE_LIMIT_PER_MINUTE`（默认 60）、`RATE_LIMIT_UPLOAD_PER_MINUTE`（默认 10）
- **FastAPI 0.139+ 兼容 patch**：`_patch_find_route_handler` 替换 `slowapi.middleware._find_route_handler`，深入 `_IncludedRouter.original_router.routes` 找 endpoint（未打 patch 时 `default_limits` 对所有 `/api/v1/*` 端点失效）
- 45 个新增单元测试（纯函数 + 路由集成 + FastAPI 0.139+ 兼容性），现有 720+ 测试零改动
- 不在范围：Redis 后端（多副本时切换 `storage_uri`）、滑动窗口（slowapi 默认固定窗口，当前规模可接受）

### CI/CD 自动化部署

- CI/CD 自动化部署（阶段 11.5）：push 到 main 后（CI 全绿后）自动构建 Docker 镜像并推送到 GitHub Container Registry，可选 SSH 自动部署到生产服务器
- 新增 `.github/workflows/deploy.yml`：独立 CD workflow（与 `ci.yml` 职责分离），`workflow_run`（CI 成功后自动）+ `workflow_dispatch`（手动）触发
- `build-and-push` job：`docker/build-push-action@v6` 构建并推送镜像到 `ghcr.io/fufufu11/research-rag-assistant`，双标签 `:latest` + `:sha-<short>`，GHA buildx 缓存加速
- `deploy` job：`appleboy/ssh-action@v1.2.0` SSH 登录服务器 `docker compose pull && up -d` + 健康检查（12 次 × 5 秒）；门控于 `vars.ENABLE_SSH_DEPLOY == 'true'`（未配置时只构建不部署，本地开发友好）
- 新增 `docker-compose.prod.yml`：生产覆盖文件，`api` 服务引用 GHCR 预构建镜像（`${IMAGE_TAG:-latest}` 支持版本回滚），复用基础 `docker-compose.yml` 的 postgres/qdrant/volumes 配置
- 新增 `tests/unit/test_deployment_config.py`：15 个测试验证 workflow 与 compose 配置的 YAML 语法与关键结构（权限 / 步骤 / 依赖 / 门控 / 镜像引用 / 健康检查脚本）
- `pyproject.toml`：添加 `pyyaml>=6.0` dev 依赖；`README.md`：新增「CI/CD 自动化部署」章节（流水线 / 镜像拉取 / Secrets 与 Variables 配置 / 手动触发）
- 不在范围：蓝绿部署 / 金丝雀发布、Kubernetes 部署、回滚自动化（手动 `IMAGE_TAG=sha-xxx` 即可）、多环境（staging/prod）分离

## 技术栈

Python 3.11 · uv · FastAPI · Pydantic · PyMuPDF · LangChain · Qdrant · SQLAlchemy 2 + Alembic · Streamlit · pytest · Ruff + mypy · GitHub Actions · Langfuse · Docker Compose

## 本地运行

```powershell
# 安装依赖（首次或修改 pyproject.toml 后）
uv sync --extra dev

# 代码质量检查
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest

# PDF 解析 CLI
uv run python scripts/parse_pdf.py <pdf_path>

# 数据库迁移（首次运行或拉取新代码后执行）
uv run alembic upgrade head

# 启动 FastAPI API 服务（端口 8000）
uv run uvicorn research_rag.api.app:create_app --factory --reload --port 8000

# 启动 Streamlit 演示界面（端口 8501，需先启动 API 服务）
uv run streamlit run src/research_rag/ui/app.py

# 运行检索评测
uv sync --extra embedding
uv run python scripts/evaluate.py verify --pdfs-dir <含 PDF 的目录>
uv run python scripts/evaluate.py run --pdfs-dir <含 PDF 的目录>
```

## 测试状态

- pytest：805 passed（含阶段 9.1 新增 16 个、阶段 9.2 新增 111 个、阶段 9.3 新增 65 个、阶段 10.1 新增 25 个、阶段 10.2 后端新增 30 个、阶段 10.2 前端补充新增 9 个、阶段 11.1 新增 31 个、阶段 11.2 新增 77 个、阶段 11.3 新增 45 个、阶段 11.5 新增 15 个）
- ruff format --check：通过
- ruff check：通过
- mypy：CI 环境（Linux）通过；本机 Windows 因应用程序控制策略阻止 C 扩展加载无法运行

## 已知问题

1. **mypy 本机不可用**：uv 管理的独立 Python 的 C 扩展在 Windows 本机被应用程序控制策略阻止加载。CI 环境（Linux）使用系统 Python，不受影响。已创建 conda 环境 `rrag311`（Python 3.11.15）作为本地替代，但 mypy 仍受 `_sqlite3.dll` 阻塞无法运行，留待 CI 验证。
2. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。
3. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体不含中文字形，CI 环境也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理。
4. **本地 pytest 全量运行注意事项**：部分测试可能加载真实 Embedding 模型导致卡住，建议单独跑改动相关测试文件；API 路由测试需设置 `$env:RERANKER_ENABLED="false"; $env:QDRANT_ENABLED="false"` 避免 lifespan 加载真实 reranker/Qdrant。

## 后续可选方向

- 历史消息反馈（阶段 10.2 已识别局限）：后端 `Message` 模型新增 `request_id` 字段 + 前端 `MessageInfo` 同步，让历史消息也能点赞/点踩
- 用户注册登录系统 + JWT（11.1 已预留 HTTPBearer 格式兼容，切换成本低）
- 生产级安全加固：非 root 容器用户、密钥管理（Vault/secrets manager）、TLS 终止（Nginx 反代）
- 表格感知切分与公式识别（阶段 12）
