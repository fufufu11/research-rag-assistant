# Current Status

## 当前版本

`v0.0.0`（阶段 0、1、2、3、4 已合并到 `main`；阶段 5 第一个 Issue「文档和 Chunk 数据模型」代码完成，待提交 PR）

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

### 阶段 5：FastAPI 与数据库（Issue #14，分支 `feat/db-models`，待提交 PR）

第一个 Issue 范围：建立文档和 Chunk 数据模型（SQLAlchemy 2 + Alembic 迁移）。

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

## 当前Issue与分支

- Issue #1（初始化Python项目与质量工具）：已关闭（PR #3 合并）
- Issue #2（配置GitHub Actions持续集成）：已关闭（PR #4 合并）
- Issue #5（feat: 实现按页PDF解析器）：已关闭（PR #6 合并）
- Issue #8（feat: 实现页内文本切分器）：已关闭（PR #9 合并）
- Issue #10（feat: 基于LangChain实现Embedding适配器）：已关闭（PR #11 合并）
- Issue #12（feat: 大模型回答与可靠引用）：已关闭（PR #13 合并）
- Issue #14（feat: 建立文档和Chunk数据模型）：进行中，分支 `feat/db-models`（本地，未推送）

## 正在处理的问题

无。阶段 5 第一个 Issue 代码与测试已完成，四项检查中 ruff format/check 和 pytest 通过，mypy 因本机 `librt` C 扩展策略限制无法运行（CI 无此问题），等待用户确认后提交、推送并开 PR。

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

# 安装 pre-commit 钩子（一次性）
uv run pre-commit install
```

## 测试状态

- pytest：104 passed（2 冒烟 + 5 PDF 解析 + 14 切分器 + 17 Embedding + 42 qa_service + 17 db_models + 7 alembic_migration）
- ruff format --check：通过
- ruff check：通过
- mypy：本机因 `librt` C 扩展被应用程序控制策略阻止无法运行，CI 环境（Linux）正常

## 下一步最小任务

按 [PROJECT_PLAN.md 阶段 5](../PROJECT_PLAN.md#L709)：

1. **提交并推送 `feat/db-models` 分支**，开 PR 关联 Issue #14（`Closes #14`）
2. **CI 通过后合并 PR**，切回 `main` 并 `git pull`
3. **继续阶段 5 后续 Issue**：
   - 文档存储逻辑（sha256 去重、文件落盘、status 状态机）
   - FastAPI 路由（上传/列表/详情/删除）
   - 问答 API（接入阶段 4 的 qa_service）
   - 集成测试（Mock LLM 与 Embedding，CI 不消耗真实 Token）

## 尚未提交的改动

`feat/db-models` 分支上的改动（均未提交）：

- 修改：`pyproject.toml`（新增 `sqlalchemy>=2.0` + `alembic>=1.13` 依赖 + 必要性说明）
- 修改：`uv.lock`（依赖解析结果，含 sqlalchemy / alembic / greenlet / mako / markupsafe）
- 修改：`.env.example`（DATABASE_URL 注释更新为"阶段 5 已启用"）
- 修改：`README.md`（新增 `alembic upgrade head` 迁移命令）
- 修改：`docs/STATUS.md`（本次更新）
- 新增：`src/research_rag/db/__init__.py`
- 新增：`src/research_rag/db/models.py`
- 新增：`src/research_rag/db/session.py`
- 新增：`alembic.ini`
- 新增：`alembic/env.py`、`alembic/script.py.mako`、`alembic/README`
- 新增：`alembic/versions/2026_07_22_1113-91c0c0df60b0_create_documents_and_chunks_tables.py`
- 新增：`tests/unit/test_db_models.py`
- 新增：`tests/unit/test_alembic_migration.py`

## 已知问题

1. **mypy 增量缓存不可用**：uv 管理的独立 Python 的 `_sqlite3.dll` 在本机被应用程序控制策略阻止加载，已在 `pyproject.toml` 中用 `no_incremental = true` 规避。CI 环境使用 Linux 上的系统 Python，不会有此问题。
2. **mypy 完全无法启动（本机）**：mypy 新版本依赖 `librt` C 扩展，本机 Windows 应用程序控制策略阻止其加载（实际报错为 `base64.pyd` 被阻止）。系统无 Python 3.11，无法用系统级 mypy 替代。CI 环境（Linux）不受影响。
3. **uv 硬链接警告**：缓存与目标目录在不同文件系统，uv 回退为完整复制。不影响功能。可设置 `$env:UV_LINK_MODE="copy"` 静默警告。
4. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。
5. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体不含中文字形，CI 环境（Linux）也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理。
6. **SQLite inspector 反射限制**：`inspector.get_foreign_keys()` 对 SQLite 的 `ondelete` 反射不稳定（不同版本可能不返回该字段），迁移测试用 `PRAGMA foreign_key_list` 直接查询。`unique` 在 SQLite 中返回 0/1 整数而非 Python bool，测试用 `bool()` 归一化。
7. **本地 `main` 比 `origin/main` 领先 4 个 commits**：历史 merge commits，已包含在远程 PR 历史中。需要在合适时机 `git push` 同步（不阻塞当前 Issue）。

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
