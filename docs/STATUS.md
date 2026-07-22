# Current Status

## 当前版本

`v0.0.0`（阶段 0、1、2、3、4 已合并到 `main`；阶段 5 第一个 Issue「文档和 Chunk 数据模型」已合并 PR #15；阶段 5 第二个 Issue「文档存储与状态管理服务层」已合并 PR #17；阶段 5 第三个 Issue「文档管理 FastAPI 路由」已合并 PR #19；阶段 5 第四个 Issue「问答 API 路由」代码完成，待提交 PR）

## 已完成

### 阶段 0：仓库与工程基础（Issue #1、#2，PR #3、#4 已合并）

- 仓库基础：`.gitignore`、`LICENSE`、`PROJECT_PLAN.md`、`README.md`、`docs/STATUS.md`
- Python 工程：`pyproject.toml`（uv + Hatchling + Python 3.11 + Ruff + mypy + pytest）
- `src/research_rag/__init__.py`：最小包，暴露 `__version__`
- `tests/test_smoke.py`：冒烟测试 2 条
- `.env.example`、`.pre-commit-config.yaml`、`.python-version`、`uv.lock`
- CI：`.github/workflows/ci.yml`（Lint / Type Check / Test 三作业）
- `.gitattributes`：统一 LF 换行符

### 阶段 1：PDF 解析器（Issue #5，PR #6 已合并到 `main`）

- `src/research_rag/pdf_parser.py`：按页解析 PDF
  - `PageInfo`（page_number / char_count / text / preview）、`PdfParseResult`（pages / page_count）
  - `parse_pdf(path: Path) -> PdfParseResult`
  - 异常：`InvalidPdfError`、`EmptyPdfError`（PROJECT_PLAN 第 13.6 节）
  - 文件不存在抛内置 `FileNotFoundError`
- `scripts/parse_pdf.py`：CLI 入口，退出码区分 4 种结果
- `tests/unit/test_pdf_parser.py`：5 条测试
- 新增依赖 `pymupdf>=1.28.0`

### 阶段 2：文本切分器（Issue #8，PR #9 已合并到 `main`）

- `src/research_rag/chunker.py`：页内文本清洗与带重叠的 Chunk 切分
  - `Chunk`（page_number / chunk_index / content / char_count）
  - `ChunkerConfig`（chunk_size=500 / chunk_overlap=80 / min_chunk_chars=20）
  - `clean_page_text(text)`、`chunk_pages(pages, config) -> list[Chunk]`
  - 使用 LangChain `RecursiveCharacterTextSplitter` 按页切分，不跨页
- `tests/unit/test_chunker.py`：14 条测试
- 新增依赖 `langchain-text-splitters>=1.1.2`

### 阶段 3：Embedding 与向量检索（Issue #10，PR #11 已合并到 `main`）

- `src/research_rag/embedding.py`：Embedding 适配器与向量检索
  - `EmbeddingConfig`（model_name 默认 `BAAI/bge-small-zh-v1.5`）
  - `RetrievalResult`（page_number / chunk_index / content / score）
  - `create_embeddings(config) -> Embeddings`：惰性导入 `HuggingFaceEmbeddings`，依赖缺失时抛 `EmbeddingServiceError`
  - `index_chunks(chunks, embeddings) -> InMemoryVectorStore`：Chunk → Document，保留 page_number/chunk_index 元数据
  - `retrieve(store, query, top_k) -> list[RetrievalResult]`：Top-K 检索，按余弦相似度降序
  - 异常：`EmbeddingServiceError`、`VectorStoreError`（PROJECT_PLAN 第 13.6 节）
- `scripts/evaluate_retrieval.py`：最小评测脚本，支持 `--demo`（内置示例）和 `--pdf` 两种模式
- `tests/unit/test_embedding.py`：17 条测试，用确定性 `FakeEmbeddings` Mock 外部模型
- 新增依赖：`langchain>=1.3.14`、`langchain-core>=1.5.0`、`langchain-huggingface>=1.2.2`、`numpy>=2.4.6`
- 新增可选 extra `embedding`（`sentence-transformers>=2.7`）：本地推理后端，CI 不安装

### 阶段 4：大模型回答与可靠引用（Issue #12，PR #13 已合并到 `main`）

- `src/research_rag/qa_service.py`：OpenAI 兼容客户端 + 结构化答案 + 引用映射
  - `LlmConfig`（base_url / api_key / model / timeout=30 / max_retries=2）
  - `ContextPiece`（document_name / page_number / chunk_index / content / score）：与 `RetrievalResult` 解耦
  - `Citation`（document_name / page_number / snippet / score）：服务端映射后的真实引用
  - `AnswerWithCitations`（answer_text / citation_indices / citations）
  - `create_chat_model(config) -> BaseChatModel`：惰性导入 `ChatOpenAI`，依赖缺失抛 `LlmServiceError`；超时与重试参数直接传给 `ChatOpenAI`，由 httpx 实现指数退避
  - `build_prompt(question, contexts)`：SystemMessage 编码第 9.3 节四条约束，HumanMessage 含 `[C1]`/`[C2]` 编号上下文
  - `parse_citation_indices(text)`：正则提取 `[C1]`/`[C3]` 等编号，去重保序，大小写不敏感
  - `map_citations(indices, contexts)`：编号→真实引用，越界编号静默跳过
  - `answer_question(question, contexts, chat_model)`：完整流程；模型输出 `[INSUFFICIENT_EVIDENCE]` 时抛 `InsufficientEvidenceError`
  - `retrieval_to_context(results, document_name)`：从 `RetrievalResult` 拼接 `ContextPiece`（多文档场景需调用方提供文档名）
  - 异常：`LlmServiceError`、`InsufficientEvidenceError`（PROJECT_PLAN 第 13.6 节）
- `tests/unit/test_qa_service.py`：42 条测试，用 langchain_core 内置 `FakeListChatModel` 和自定义 `_RaisingChatModel` Mock LLM，CI 不消耗真实 Token
- 新增依赖：`langchain-openai>=1.2.0`（OpenAI 兼容协议客户端）
- `.env.example` 新增：`LLM_TIMEOUT=30`、`LLM_MAX_RETRIES=2`

### 阶段 5：FastAPI 与数据库（Issue #14 已合并 PR #15；Issue #16 已合并 PR #17；Issue #18 已合并 PR #19；Issue #20 进行中）

#### Issue #14：建立文档和 Chunk 数据模型（已合并 PR #15）

- `src/research_rag/db/`：数据库包
  - `db/models.py`：`Base`（DeclarativeBase）+ `DocumentStatus` enum + `Document` + `Chunk` + 异常
    - `Document`（id / original_name / stored_name / sha256 / page_count / status / error_message / created_at / updated_at），sha256 唯一索引，status 用 `SAEnum` 存小写字符串值
    - `Chunk`（id / document_id / page_number / chunk_index / content / char_count / vector_id / created_at），(document_id, chunk_index) 唯一约束，外键 `ON DELETE CASCADE`
    - `Document` → `Chunk` 一对多关系，`cascade="all, delete-orphan"` 实现删除文档时自动删除分段（US-002）
    - UUID 主键用 SQLAlchemy 2.0 内置 `Uuid` 类型（跨数据库兼容）
    - 时间戳用 Python 端 `default` / `onupdate`（`datetime.now(UTC)`，naive 存储兼容 SQLite）
    - 异常：`DuplicateDocumentError`、`DocumentNotFoundError`（PROJECT_PLAN 第 13.6 节剩余两条）
  - `db/session.py`：`get_database_url()`（从环境变量读，默认 `sqlite:///./data/app.db`）+ `create_session_factory(database_url)`（工厂函数，测试可注入临时 URL，`expire_on_commit=False`）
- Alembic 配置：
  - `alembic.ini`：注释硬编码 URL（从环境变量读）、启用日期前缀文件名、配置 ruff post_write_hooks 自动格式化生成的迁移
  - `alembic/env.py`：`target_metadata = Base.metadata`、`compare_type=True`、`render_as_batch=True`（SQLite 兼容）
  - `alembic/script.py.mako`：用现代类型标注（`str | None` 代替 `Union`）
  - 首个迁移 `2026_07_22_1113-91c0c0df60b0_create_documents_and_chunks_tables.py`：建 documents + chunks 表
- `tests/unit/test_db_models.py`：17 条测试（模型字段默认值、CRUD、级联删除、唯一约束、relationship、异常可实例化、session 模块）
- `tests/unit/test_alembic_migration.py`：7 条测试（upgrade head 建表、列结构、sha256 唯一索引、(document_id, chunk_index) 唯一约束、外键 CASCADE、downgrade base 回滚）
- 新增依赖：`sqlalchemy>=2.0`、`alembic>=1.13`（主依赖，阶段 5+ 业务要用）
- `.env.example` 更新：DATABASE_URL 注释改为"阶段 5 已启用"，补充 PostgreSQL 切换说明
- `README.md` 新增：`uv run alembic upgrade head` 迁移命令

**本 Issue 不实现**：HTTP 接口、文档存储逻辑、向量库写入、QueryLog 模型、repository 服务层（后续 Issue 处理）。

#### Issue #16：实现文档存储与状态管理服务层（已合并 PR #17）

第二个 Issue 范围：在已有 `Document` / `Chunk` 数据模型基础上，实现 repository 层（纯 DB CRUD）与 service 层（业务编排：sha256 去重、文件落盘、调用 `parse_pdf` / `chunk_pages`、status 状态机）。

- `src/research_rag/db/repositories.py`：`DocumentRepository` 数据访问层
  - 封装 `Document` / `Chunk` 表的 CRUD：`create` / `get_by_id` / `get_by_sha256` / `list_all` / `delete` / `update_status` / `update_page_count` / `add_chunks`
  - 只 `flush` 不 `commit`，事务边界由 service 层控制（Repository 模式常见做法）
  - `update_status` 同时更新 `error_message`（状态和错误信息总是一起变化）
  - `add_chunks` 用 `relationship.extend` 关联 chunks 到 document
- `src/research_rag/services/document_service.py`：`DocumentService` 业务编排层
  - `upload_document(file_bytes, original_name)`：sha256 去重 → 创建 PENDING 记录 → 落盘 → PROCESSING → `parse_pdf` + `chunk_pages` → 持久化 chunks → READY（失败转 FAILED + error_message）
  - `get_document(doc_id)`：不存在抛 `DocumentNotFoundError`
  - `list_documents()`：按创建时间降序
  - `delete_document(doc_id)`：先删 DB 记录（事务，级联删 chunks），再删文件（best-effort，`suppress(OSError)`）
  - `stored_name = sha256[:16] + 小写扩展名`，避免路径遍历攻击（PROJECT_PLAN 第 11 节）
  - 状态机：`pending → processing → ready / failed`，每次转换都 `commit`，进程崩溃也保留最后状态
  - 失败处理：`rollback` 清除 pending 改动 → 重新查询 → 标记 FAILED + error_message → commit
  - `vector_id` 保留 `None`（阶段 6 才写向量库）
  - `UPLOAD_DIR` 从环境变量读，默认 `./data/uploads`
- `tests/unit/test_repositories.py`：14 条测试（CRUD、级联删除、状态更新、add_chunks）
- `tests/unit/test_document_service.py`：27 条测试
  - Mock `parse_pdf` / `chunk_pages`（`monkeypatch`），用内存 SQLite + `tmp_path` 隔离
  - 覆盖：成功上传、重复上传（DuplicateDocumentError）、损坏/空 PDF（FAILED 状态）、空 chunks、查询、列表、删除（含文件和 chunks 级联）、文件已缺失、stored_name 安全性

**本 Issue 不实现**：FastAPI 路由、向量库写入、QueryLog、文件大小限制/MIME 校验（下一个 Issue 处理）。

#### Issue #18：实现文档管理 FastAPI 路由（已合并 PR #19）

第三个 Issue 范围：在已有 `DocumentService` 基础上，实现 FastAPI 应用工厂、文档管理 HTTP 路由（上传/列表/详情/删除）、Pydantic schema、异常处理器、依赖注入链。

- `src/research_rag/api/`：FastAPI API 包
  - `api/app.py`：`create_app(session_factory=None, cors_origins=None)` 应用工厂
    - `lifespan` async context manager：启动建 engine + session_factory，关闭 `engine.dispose()`；调用方已注入 factory 时跳过（测试场景）
    - CORS 中间件（`DEFAULT_CORS_ORIGINS` 包含 localhost:3000/5173/8000/8501 等开发端口）
    - 全局异常处理器：`DuplicateDocumentError → 409`、`DocumentNotFoundError → 404`，统一返回 `ErrorResponse`（`{"detail": "..."}`）
  - `api/schemas.py`：Pydantic v2 schema
    - `DocumentRead`（id / original_name / stored_name / sha256 / page_count / status / error_message / created_at / updated_at），`ConfigDict(from_attributes=True)` 从 ORM 属性读值
    - `DocumentList`（items: list[DocumentRead]），包裹数组便于后续加分页
    - `ErrorResponse`（detail: str），与 FastAPI 默认 `HTTPException` 格式一致
  - `api/dependencies.py`：三层依赖链
    - `get_session_factory(request)`：从 `app.state` 取应用启动时创建的工厂
    - `get_db(session_factory)`：每请求一个 Session，`yield` 后 `close`（FastAPI 推荐的"每请求一会话"模式）
    - `get_document_service(session)`：用当前请求 Session 构造 `DocumentService`
  - `api/routes/documents.py`：文档管理路由（`prefix=/api/v1/documents`）
    - `POST ""` → 201 + `DocumentRead`：接收 `UploadFile`，调 `service.upload_document(file_bytes, filename or "unknown")`
    - `GET ""` → 200 + `DocumentList`：调 `service.list_documents()`，逐项 `model_validate` 转 `DocumentRead`
    - `GET "/{doc_id}"` → 200 + `DocumentRead`：路径参数 `uuid.UUID` 类型注解，FastAPI 自动校验非法 UUID → 422
    - `DELETE "/{doc_id}"` → 204 无响应体：调 `service.delete_document(doc_id)`
- `src/research_rag/db/session.py`：新增 `create_engine_for_url(database_url)` 帮助函数
  - 自动为 SQLite URL 添加 `connect_args={"check_same_thread": False}`（FastAPI 同步路由运行在线程池，必需）
  - 非 SQLite URL 不注入 SQLite 专属参数，`create_session_factory` 内部复用
- `tests/unit/test_api_documents.py`：10 条 API 测试
  - 用 `fastapi.testclient.TestClient`（基于 httpx，不起真实 uvicorn）
  - `app.dependency_overrides[get_document_service]` 把 service 换成 `MagicMock(spec=DocumentService)`，完全跳过真实 DB/文件 IO/PDF 解析
  - 覆盖：上传成功（201）、重复上传（409）、空列表（200）、多条列表（200）、详情成功（200）、详情不存在（404）、详情非法 UUID（422）、删除成功（204）、删除不存在（404）、删除非法 UUID（422）
  - `make_document(**overrides)` 辅助函数显式提供 `id`/`created_at`/`updated_at`（未持久化实例的 ORM 默认值只在 flush 时触发）
- `tests/unit/test_db_models.py`：新增 2 条 `create_engine_for_url` 测试
  - SQLite URL 跨线程访问不报错（验证 `check_same_thread=False` 生效）
  - 非 SQLite URL 不添加 `check_same_thread`（用 `monkeypatch` mock `create_engine`，避免实际导入 psycopg）
- `pyproject.toml`：
  - 新增主依赖 `fastapi>=0.115`、`python-multipart>=0.0.18`、`uvicorn>=0.32`
  - dev 依赖 `httpx>=0.27` 替换为 `httpx2>=0.27`（starlette 1.x TestClient 弃用 httpx）
  - 新增 `per-file-ignores`：`"src/research_rag/api/**" = ["TC001", "TC002", "TC003", "B008"]`（FastAPI/Pydantic 需运行时访问类型注解）

**依赖必要性说明**：
- `fastapi`：本 Issue 核心，实现 HTTP 路由、依赖注入、异常处理、Pydantic schema 集成。无替代方案能更好满足"快速构建 RESTful API + 自动 OpenAPI 文档 + 类型安全"的需求。
- `uvicorn`：ASGI 服务器，用于生产运行 FastAPI 应用。开发/测试用 `TestClient` 不需要它，但部署必需。
- `python-multipart`：FastAPI `UploadFile` 的硬性依赖，处理 `multipart/form-data` 文件上传。未安装时 FastAPI 在导入 `UploadFile` 时报错。
- `httpx2`（dev 依赖）：starlette 1.x 的 `TestClient` 弃用 `httpx`，改用 `httpx2`（API 兼容 httpx）。仅测试用。

**本 Issue 不实现**：问答 API（`/qa` 路由，下个 Issue）、向量库写入、QueryLog、认证/鉴权、文件大小限制/MIME 强制校验（验收标准"可选简单校验，不强制"）。

#### Issue #20：实现问答 API 路由（分支 `feat/qa-api`，待提交 PR）

第四个 Issue 范围：实现 `POST /api/v1/queries` 问答 API，编排 DB 查询 → 向量检索 → LLM 问答 → 引用映射 → 组装响应。不新增第三方依赖，复用已有 fastapi/sqlalchemy/langchain。

- `src/research_rag/api/schemas.py`：追加问答相关 Pydantic schema
  - `QueryRequest`（question 必填 min_length=1 / document_ids 可选默认空列表表示全库 / top_k 默认从环境变量 `RETRIEVAL_TOP_K` 读或 `DEFAULT_TOP_K`=8）
  - `CitationRead`（document_id / document_name / page_number / chunk_index / snippet / score），对齐 PROJECT_PLAN 第 8.4 节响应结构
  - `QueryResponse`（answer / citations: list[CitationRead] / request_id: uuid.UUID / elapsed_ms: int），对齐第 8.4 节
  - `_get_default_top_k()` 辅助函数从环境变量读 top_k，格式错误回退默认值
- `src/research_rag/services/qa_service.py`：`QaService` 业务编排层（与底层 `qa_service.py` 区分：本模块在 `services/` 子包，编排 DB+Embedding+LLM；底层在顶层 `research_rag/`，只做 LLM 调用+引用映射）
  - `NoAvailableDocumentsError`：无可用 READY 文档时抛出，API 层映射为 404
  - `QaService(session, llm_config, embedding_config?, embeddings?, chat_model?)`：构造函数，Embedding 和 ChatModel 惰性创建（首次调 `answer` 时），测试时直接注入 `FakeEmbeddings` / `FakeListChatModel`
  - `answer(question, document_ids?, top_k) -> QueryResponse`：完整问答流程
    1. `_get_ready_documents`：查 READY 文档（全库或按 document_ids 过滤，不存在的 UUID 抛 `DocumentNotFoundError`，非 READY 的跳过）
    2. 惰性创建 Embedding 和 ChatModel（测试时已注入则跳过）
    3. `_retrieve_contexts`：多文档单独 `index_chunks` + `retrieve`，合并后按 score 降序取全局 top_k；返回 `(contexts, context_doc_ids)` 平行列表（`RetrievalResult` 不含 `document_id`，单文档索引能明确归属）
    4. 调 `answer_question` 让 LLM 基于上下文作答
    5. `_map_citations`：用 `citation_indices` 直接索引 contexts，配合 `context_doc_ids` 获取 `document_id`，越界编号静默跳过
    6. 组装 `QueryResponse`（含 `request_id` 和 `elapsed_ms`）
  - `_orm_chunks_to_chunker`：ORM `Chunk` → chunker `Chunk` dataclass 转换（`index_chunks` 接受 chunker.Chunk）
- `src/research_rag/api/routes/queries.py`：问答路由（`prefix=/api/v1/queries`）
  - `POST ""` → 200 + `QueryResponse`：接收 `QueryRequest`（`Body(...)` 显式标注请求体），调 `QaService.answer`，返回 200（问答是查询而非创建资源，不用 201）
- `src/research_rag/api/dependencies.py`：追加问答依赖
  - `get_llm_config()`：从环境变量构造 `LlmConfig`（`_parse_float`/`_parse_int` 安全转换 timeout/max_retries，格式错误回退默认值）
  - `get_qa_service(session=Depends(get_db), llm_config=Depends(get_llm_config))`：用当前 Session + LlmConfig 构造 `QaService`（参数必须用 `Depends()` 包裹，否则 FastAPI 会当成 query/body 参数）
- `src/research_rag/api/app.py`：追加 5 个异常处理器
  - `NoAvailableDocumentsError → 404`（无可用文档）
  - `InsufficientEvidenceError → 422`（模型无法回答，语义化：请求实体无法处理）
  - `LlmServiceError → 503`（LLM 服务不可用）
  - `EmbeddingServiceError → 503`（Embedding 服务不可用）
  - `VectorStoreError → 500`（向量存储内部错误）
  - 注册 `queries_router`
- `tests/unit/test_api_queries.py`：12 条 API 测试
  - 用 `TestClient` + `app.dependency_overrides[get_qa_service]` 替换为 `MagicMock(spec=QaService)`
  - 覆盖：问答成功（200）、带 document_ids（200）、证据不足（422）、LLM 异常（503）、Embedding 异常（503）、向量存储异常（500）、无可用文档（404）、文档不存在（404）、空 question（422）、缺少 question（422）、无效 UUID（422）、空 body（422）
- `tests/unit/test_qa_orchestration.py`：9 条编排测试
  - 用内存 SQLite + 真实 `DocumentRepository`（测试 DB 查询逻辑），注入 `_FakeEmbeddings`（字符袋向量）+ `FakeListChatModel`
  - 用 `monkeypatch` 替换 `research_rag.services.qa_service.answer_question`，控制 LLM 返回值
  - 覆盖：citation 映射正确、document_ids 过滤、默认 top_k、空库 NoAvailableDocumentsError、文档非 READY、文档不存在、证据不足透传、LLM 异常透传、citation_indices 越界跳过

**设计取舍**：
- 多文档检索用"每文档单独索引+检索，合并按 score 降序取全局 top_k"：`embedding.retrieve` 返回的 `RetrievalResult` 不含 `document_id`，单文档索引能明确归属，避免修改已合并的阶段 3 代码。阶段 6 接 Qdrant 后改为单库检索。
- `InsufficientEvidenceError → 422` 而非 404：422 语义为"请求实体无法处理"（模型无法基于上下文回答），比 404（资源不存在）更准确。
- `QaService` 惰性创建 Embedding/ChatModel：生产环境由 `get_qa_service` 依赖注入（不传，让 QaService 自己创建）；测试时直接注入 Fake 实例，跳过真实模型加载。
- POST /queries 返回 200 而非 201：问答是查询操作（读 LLM + 读 DB），不创建持久化资源。

**本 Issue 不实现**：Qdrant 接入（阶段 6）、QueryLog 持久化（阶段 5 后期）、流式响应、认证/鉴权、多轮对话。

## 当前Issue与分支

- Issue #1（初始化Python项目与质量工具）：已关闭（PR #3 合并）
- Issue #2（配置GitHub Actions持续集成）：已关闭（PR #4 合并）
- Issue #5（feat: 实现按页PDF解析器）：已关闭（PR #6 合并）
- Issue #8（feat: 实现页内文本切分器）：已关闭（PR #9 合并）
- Issue #10（feat: 基于LangChain实现Embedding适配器）：已关闭（PR #11 合并）
- Issue #12（feat: 大模型回答与可靠引用）：已关闭（PR #13 合并）
- Issue #14（feat: 建立文档和Chunk数据模型）：已关闭（PR #15 合并）
- Issue #16（feat: 实现文档存储与状态管理服务层）：已关闭（PR #17 合并）
- Issue #18（feat: 实现文档管理 FastAPI 路由）：已关闭（PR #19 合并）
- Issue #20（feat: 实现问答 API 路由）：进行中，分支 `feat/qa-api`（本地，未推送）

## 正在处理的问题

无。阶段 5 第四个 Issue（问答 API）代码与测试已完成，四项检查中 ruff format/check 和 pytest 通过，mypy 因本机 `librt` C 扩展策略限制无法运行（CI 无此问题），等待用户确认后提交、推送并开 PR。

## 本地运行命令

```powershell
# 安装依赖（首次或修改 pyproject.toml 后）
uv sync --extra dev

# 四项检查（PROJECT_PLAN.md 第 13.4 节）
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run python -m pytest

# 运行 PDF 解析 CLI
uv run python scripts/parse_pdf.py <pdf_path>

# 运行检索评测脚本（需先安装推理后端）
uv sync --extra embedding
uv run python scripts/evaluate_retrieval.py --demo

# 数据库迁移（阶段 5，首次运行或拉取新代码后执行）
uv run alembic upgrade head

# 启动 FastAPI 开发服务器（阶段 5 第三个 Issue 起）
uv run uvicorn research_rag.api.app:create_app --factory --reload --port 8000

# 安装 pre-commit 钩子（一次性）
uv run pre-commit install
```

## 测试状态

- pytest：178 passed（2 冒烟 + 5 PDF 解析 + 14 切分器 + 17 Embedding + 42 qa_service + 19 db_models + 7 alembic_migration + 14 repositories + 27 document_service + 10 api_documents + 12 api_queries + 9 qa_orchestration）
- ruff format --check：通过
- ruff check：通过
- mypy：本机因 `librt` C 扩展被应用程序控制策略阻止无法运行，CI 环境（Linux）正常

## 下一步最小任务

按 [PROJECT_PLAN.md 阶段 5](../PROJECT_PLAN.md#L709)：

1. **提交并推送 `feat/qa-api` 分支**，开 PR 关联 Issue #20（`Closes #20`）
2. **CI 通过后合并 PR**，切回 `main` 并 `git pull`
3. **继续阶段 5 后续 Issue**：
   - 集成测试（Mock LLM 与 Embedding，CI 不消耗真实 Token）
   - QueryLog 持久化（阶段 5 后期）

## 尚未提交的改动

`feat/qa-api` 分支上的改动（均未提交）：

- 修改：`src/research_rag/api/app.py`（追加 5 个异常处理器 + 注册 queries_router）
- 修改：`src/research_rag/api/dependencies.py`（追加 `get_llm_config` / `get_qa_service`，`get_document_service` / `get_qa_service` 参数加 `Depends()`）
- 修改：`src/research_rag/api/schemas.py`（追加 `QueryRequest` / `CitationRead` / `QueryResponse` + `_get_default_top_k`）
- 修改：`docs/STATUS.md`（本次更新）
- 新增：`src/research_rag/services/qa_service.py`（`QaService` 业务编排层 + `NoAvailableDocumentsError`）
- 新增：`src/research_rag/api/routes/queries.py`（问答路由 `POST /api/v1/queries`）
- 新增：`tests/unit/test_api_queries.py`（12 条 API 测试）
- 新增：`tests/unit/test_qa_orchestration.py`（9 条编排测试）

## 已知问题

1. **mypy 增量缓存不可用**：uv 管理的独立 Python 的 `_sqlite3.dll` 在本机被应用程序控制策略阻止加载，已在 `pyproject.toml` 中用 `no_incremental = true` 规避。CI 环境使用 Linux 上的系统 Python，不会有此问题。
2. **mypy 完全无法启动（本机）**：mypy 新版本依赖 `librt` C 扩展，本机 Windows 应用程序控制策略阻止其加载（实际报错为 `base64.pyd` 被阻止）。系统无 Python 3.11，无法用系统级 mypy 替代。CI 环境（Linux）不受影响。
3. **uv 硬链接警告**：缓存与目标目录在不同文件系统，uv 回退为完整复制。不影响功能。可设置 `$env:UV_LINK_MODE="copy"` 静默警告。
4. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。
5. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体不含中文字形，CI 环境（Linux）也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理。
6. **SQLite inspector 反射限制**：`inspector.get_foreign_keys()` 对 SQLite 的 `ondelete` 反射不稳定（不同版本可能不返回该字段），迁移测试用 `PRAGMA foreign_key_list` 直接查询。`unique` 在 SQLite 中返回 0/1 整数而非 Python bool，测试用 `bool()` 归一化。

## 最近学到的内容

- SQLAlchemy 2.0 的 `Mapped[T]` + `mapped_column` 比 1.x 的 `Column` 类型安全：`Mapped[str | None]` 自动推断 `nullable=True`，类型与约束一致
- `Uuid` 类型是 SQLAlchemy 2.0 内置的跨数据库 UUID 类型（SQLite 存 32 字符字符串，PostgreSQL 用原生 UUID），比 `postgresql.UUID` 通用
- `SAEnum` 的 `values_callable=lambda e: [x.value for x in e]` 让数据库存枚举值（"pending"）而非枚举名（"PENDING"），便于 SQL 排查
- `native_enum=False` 让 SAEnum 用 VARCHAR + CHECK 约束存储，跨数据库兼容（默认 `native_enum=True` 在 PostgreSQL 用原生 ENUM 类型，迁移和修改不灵活）
- `relationship(cascade="all, delete-orphan")` 是 ORM 层级联，`ForeignKey(ondelete="CASCADE")` 是数据库层级联，两者搭配最稳妥（ORM 操作走 cascade，裸 SQL 走 ondelete）
- `datetime.now(UTC).replace(tzinfo=None)` 生成 naive UTC 时间，兼容 SQLite 的 DateTime 存储（SQLite 不存时区信息，timezone-aware 会带 +00:00 跨库不一致）
- Alembic 的 `render_as_batch=True` 对 SQLite 必要：SQLite 不支持 ALTER TABLE 部分操作，batch 模式拆成"建新表→复制→删旧表→改名"
- Alembic 的 `compare_type=True` 让 autogenerate 检测列类型变化（默认只检测表存在性，会漏掉 String(64)→String(128) 这类变更）
- Alembic 的 `post_write_hooks` 配置 ruff format + ruff check --fix，让生成的迁移自动符合代码规范，省去手动格式化
- `alembic.ini` 用 `encoding="locale"` 读取，Windows locale 是 GBK，无法解码 UTF-8 中文，故 alembic.ini 注释用英文（env.py 是 Python 文件，默认 UTF-8，可中文注释）
- SQLAlchemy 的 `inspector.get_foreign_keys()` 对 SQLite 的 `ondelete` 反射不稳定，用 `PRAGMA foreign_key_list` 直接查询更可靠
- SQLite 的 `inspector.get_indexes()` 返回的 `unique` 是 0/1 整数而非 Python bool，断言时用 `bool()` 归一化
- `sessionmaker[Session]` 是泛型类型标注，让 mypy 能检查 `factory()` 返回的 Session 类型
- `expire_on_commit=False` 是 FastAPI + SQLAlchemy 常见配置：commit 后对象属性不过期，避免请求处理中访问属性触发额外查询
- Repository 模式：把数据访问集中到一个类，业务层只调方法不写 SQL，便于换数据库和测试。Repository 只 `flush` 不 `commit`，事务边界由 service 层控制
- `session.rollback()` 后 ORM 对象属性会过期，需要 `get_by_id` 重新查询才能安全访问（避免访问过期属性触发隐式查询）
- 状态机 + 多次 commit：每个状态转换都 commit，即使进程崩溃也保留最后状态。失败时先 rollback 清除 pending 改动，再重新查询记录标记 FAILED
- `Path.suffix.lower()` 提取小写扩展名，配合 sha256 前缀生成安全文件名，从源头杜绝路径遍历（用户文件名不作为磁盘路径）
- `contextlib.suppress(OSError)` 比 `try/except/pass` 更简洁，语义明确表达"忽略此异常"
- `unittest.mock.patch` / `monkeypatch.setattr` 替换模块级函数，测试中 Mock `parse_pdf` / `chunk_pages` 避免真实 PDF 解析
- `tmp_path` fixture 隔离文件 IO，每个测试用独立临时目录，不污染真实磁盘
- `from __future__ import annotations` 让类型标注变为字符串，ruff TCH 规则会把仅用于标注的导入移到 `TYPE_CHECKING` 块，减少运行时导入开销
- FastAPI 应用工厂模式（`create_app()` 返回新实例）比模块级 `app = FastAPI()` 单例更灵活：测试可注入不同配置（内存 SQLite factory、Mock service），未来可同进程跑多实例
- FastAPI `lifespan` async context manager 替代弃用的 `@app.on_event("startup"/"shutdown")`：`@asynccontextmanager` + `yield` 前后分别做启动/关闭，资源生命周期清晰
- FastAPI `dependency_overrides` 是测试的核心利器：`app.dependency_overrides[get_document_service] = lambda: mock_service` 完全跳过真实 DB/文件 IO/PDF 解析，路由逻辑被隔离测试
- `MagicMock(spec=DocumentService)` 限定 mock 只能调 DocumentService 的方法，调不存在的方法抛 AttributeError，既断言调用参数又防止误用
- Pydantic v2 `ConfigDict(from_attributes=True)` 让 `DocumentRead.model_validate(orm_doc)` 直接读 ORM 属性，无需手写字段映射；但 ORM 对象的 `default` 字段（如 `id`/`created_at`）只在 `flush` 时触发，未持久化实例这些属性为 `None`，会触发 Pydantic 校验失败
- FastAPI 同步路由（`def` 而非 `async def`）运行在线程池，多请求可能用不同线程访问同一 engine；SQLite 默认 `check_same_thread=True` 禁止跨线程使用连接，必须用 `create_engine(connect_args={"check_same_thread": False})` 关闭
- FastAPI 路径参数类型注解（`doc_id: uuid.UUID`）自动校验格式，非法 UUID 返回 422，合法 UUID 直接传入 service，无需手动解析
- `@app.exception_handler(BusinessError)` 集中映射业务异常到 HTTP 状态码，路由代码保持线性（不写 try/except），新增异常只需加一个 handler
- FastAPI `UploadFile` 硬性依赖 `python-multipart` 处理 `multipart/form-data`，未安装时导入即报错；`file.filename or "unknown"` 兜底空文件名
- starlette 1.x 的 `TestClient` 弃用 `httpx`，改用 `httpx2`（API 兼容 httpx）；用 `with TestClient(app)` 触发 `lifespan`，否则 `app.state` 可能未初始化
- ruff `TC001/TC002/TC003` 规则把仅用于类型标注的导入移到 `TYPE_CHECKING` 块；但 FastAPI 路由的返回类型注解（`-> Document`）和 `Depends` 参数注解（`service: DocumentService`）需运行时可访问，故 `api/**` 用 `per-file-ignores` 关闭这些规则
- FastAPI 依赖函数的参数必须用 `Depends()` 包裹：`def get_qa_service(session=Depends(get_db))`。如果不写 `Depends()`，FastAPI 会把 `session` / `llm_config` 当成 query/body 参数，导致请求返回 422（参数缺失）。这是依赖注入最常见的陷阱。
- FastAPI `Body(...)` 显式标注请求体：当路由函数只有一个 Pydantic model 参数时，FastAPI 默认把它当请求体；但加 `Body(...)` 更明确，避免与路径/查询参数混淆。
- 业务编排层与底层工具层命名区分：`services/qa_service.py`（`QaService` 类，编排 DB+Embedding+LLM）vs 顶层 `qa_service.py`（`answer_question` 函数，只做 LLM 调用+引用映射）。两者命名相同但职责不同，靠子包路径区分。
- 多文档检索策略：当向量检索结果不含 `document_id` 时，用"每文档单独索引+检索 + 外部维护 document_id 映射"绕开，避免修改已合并的底层代码。合并后按 score 降序取全局 top_k。
- `citation_indices` 直接索引 contexts：`answer_question` 返回的编号（从 1 开始）与 contexts 列表顺序一致，用 `contexts[idx - 1]` 直接获取，配合平行 `context_doc_ids` 获取 `document_id`，无需反查 DB。
- 惰性初始化 + 测试注入：`QaService` 构造函数接受可选 `embeddings` / `chat_model`，未传时在 `answer` 中惰性创建。生产环境不传（让 service 自己创建），测试时注入 Fake 实例跳过真实模型加载。用 `assert self._embeddings is not None` 帮助 mypy 收窄类型（本项目不用 `-O` 优化）。
- `monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_fn)` 替换模块级函数：Mock 的是 services/qa_service.py 命名空间中的引用（从 qa_service.py 导入的），不是原始 qa_service.py 模块中的函数。Mock 路径必须是"被测模块的导入路径"。
- HTTP 状态码语义：422（Unprocessable Entity）比 404 更适合"模型无法回答"（请求格式正确但语义无法处理）；503（Service Unavailable）适合外部依赖（LLM/Embedding）失败；500 适合内部错误（向量存储）。
- POST 查询类操作返回 200 而非 201：201 Created 语义是"创建了新资源"，问答是读操作（读 LLM + 读 DB），不创建持久化资源，用 200 更准确。
- SQLAlchemy ORM `default=uuid.uuid4` 在 `flush()` 时才触发：构造对象后 `doc.id` 为 None，必须先 `session.add(doc)` + `session.flush()` 才能获取 `doc.id` 用于设置子表外键（如 Chunk.document_id）。
