# 系统架构

本文档说明 Research RAG Assistant 的整体架构、技术选型理由、数据模型、API 设计与 RAG 处理流程，供维护者理解系统设计取舍。

## 1. 整体架构

```text
┌──────────────────┐
│ Streamlit Demo UI│
└────────┬─────────┘
         │ HTTP
┌────────▼─────────┐
│     FastAPI      │
│ validation/error │
└──────┬───────┬───┘
       │       │
       │       └──────────────────────────┐
┌──────▼──────┐                    ┌──────▼──────┐
│Document Flow│                    │ Query Flow  │
│parse/chunk  │                    │retrieve/LLM │
└───┬─────┬───┘                    └───┬─────┬───┘
    │     │                            │     │
┌───▼──┐ ┌▼──────────┐          ┌─────▼─┐ ┌─▼────────┐
│Files │ │PostgreSQL │          │Qdrant │ │LLM API   │
└──────┘ │metadata   │          │vector │ │compatible│
         └───────────┘          └───────┘ └──────────┘
```

系统分为四层：

- **API 层**（`src/research_rag/api/`）：FastAPI 路由、Pydantic schema、依赖注入、全局异常处理器。只做参数解析与响应组装，不含业务逻辑。
- **服务层**（`src/research_rag/services/`）：业务编排。`DocumentService` 负责 sha256 去重、文件落盘、解析切分与状态机；`QaService` 负责文档查询、向量检索、LLM 问答与引用映射。
- **数据访问层**（`src/research_rag/db/`）：SQLAlchemy 2.0 ORM 模型与 Repository。`DocumentRepository` 封装表级 CRUD，只 `flush` 不 `commit`，事务边界由服务层控制。
- **工具层**（`src/research_rag/` 顶层模块）：`pdf_parser`、`chunker`、`embedding`、`vector_store`、`qa_service`（底层 LLM 调用与引用映射）、`evaluation`。

## 2. 技术选型

| 类别 | 选择 | 说明 |
|---|---|---|
| 编程语言 | Python 3.11 | 兼容性好，适合 AI 应用开发 |
| 依赖管理 | `uv` + `pyproject.toml` | 速度快，便于锁定依赖 |
| Web 框架 | FastAPI + Pydantic | 类型校验、自动 OpenAPI 文档、异步接口 |
| PDF 解析 | PyMuPDF | 按页提取文本并保留页码 |
| 向量数据库 | Qdrant | 支持 Payload 过滤，Docker 部署方便；未配置时回退到 `InMemoryVectorStore` |
| Embedding | `BAAI/bge-small-zh-v1.5` | 本地中文模型；模型名可通过环境变量配置 |
| 大模型 | OpenAI 兼容协议模型服务 | 通过环境变量切换 `base_url` / `api_key` / `model` |
| 元数据数据库 | SQLite（默认）/ PostgreSQL | SQLite 降低本地部署难度，生产可切 PostgreSQL |
| ORM 与迁移 | SQLAlchemy 2 + Alembic | 类型安全的数据模型与数据库迁移 |
| 演示界面 | Streamlit | 快速形成可演示产品 |
| 测试 | pytest + httpx | 单元测试与 API 集成测试 |
| 代码质量 | Ruff + mypy + pre-commit | 格式、静态检查与提交前检查 |
| 持续集成 | GitHub Actions | PR 自动执行检查与测试 |
| RAG 框架 | LangChain | 切分、Embedding、向量检索抽象 |

### 为什么直接使用 LangChain

本项目目标是构建可溯源的 RAG 应用，而非重新实现切分器、Embedding 适配器或向量检索引擎。直接使用 LangChain 可以：

- 复用经过社区验证的 `RecursiveCharacterTextSplitter`、Embedding 接口和向量存储抽象，把精力集中在引用溯源、评测与业务逻辑上。
- 通过阅读 LangChain 源码与文档理解切分大小、重叠、归一化、Top-K 等参数的含义，而非重复造轮子。
- 在需要时（如引用映射、Prompt 约束、错误重试）保留对核心业务逻辑的完全控制。

项目中由 LangChain 处理的能力：文本切分、Embedding 调用、向量存储与检索。由项目自身控制的能力：引用编号映射、Prompt 约束、状态机、去重、评测指标。

## 3. RAG 处理流程

### 文档处理流程

```text
上传 PDF
  → 文件类型、大小和哈希校验
  → sha256 去重（已存在则抛 DuplicateDocumentError）
  → 按页提取文本（PyMuPDF）
  → 清洗空白字符
  → 按页切分并保留重叠区（不跨页）
  → 生成 Embedding
  → 保存文档和分段元数据（SQLAlchemy）
  → 写入向量数据库（Qdrant 或 InMemoryVectorStore）
  → 更新文档处理状态（pending → processing → ready / failed）
```

状态机在每个状态转换时 `commit`，进程崩溃也保留最后状态。失败时先 `rollback` 清除 pending 改动，再标记 `FAILED` 并写入 `error_message`。

### 问答流程

```text
用户问题
  → 参数校验（Pydantic）
  → 查询 READY 文档（全库或按 document_ids 过滤）
  → 问题 Embedding
  → 向量检索 Top-K
  → 构造带编号的上下文（[C1] / [C2] ...）
  → 大模型生成结构化答案与上下文编号
  → 服务端映射真实文档名、页码与片段
  → 返回答案、引用、request_id 与耗时
```

引用映射在服务端完成：模型输出 `[C1]` 等编号，服务端根据编号索引上下文列表，获取真实的 `document_id`、`page_number` 与 `snippet`，避免模型编造页码。证据不足时模型输出 `[INSUFFICIENT_EVIDENCE]`，服务端抛 `InsufficientEvidenceError`。

## 4. 数据模型

### Document

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键，业务层生成 |
| `original_name` | String(255) | 用户上传时的文件名 |
| `stored_name` | String(255) | 服务端生成的安全文件名（sha256 前缀 + 小写扩展名，避免路径遍历） |
| `sha256` | String(64) | 文件哈希，唯一索引，用于去重 |
| `page_count` | Integer | PDF 页数 |
| `status` | Enum | `pending` / `processing` / `ready` / `failed` |
| `error_message` | Text (nullable) | 处理失败原因 |
| `created_at` | DateTime | 创建时间（UTC） |
| `updated_at` | DateTime | 更新时间（UTC） |

### Chunk

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 主键 |
| `document_id` | UUID | 所属文档（外键，`ON DELETE CASCADE`） |
| `page_number` | Integer | 来源页码，从 1 开始 |
| `chunk_index` | Integer | 文档内分段序号，从 0 开始 |
| `content` | Text | 分段文本 |
| `char_count` | Integer | 字符数 |
| `vector_id` | String(255) (nullable) | 向量库中的 ID |
| `created_at` | DateTime | 创建时间（UTC） |

`Document` → `Chunk` 为一对多关系，通过 ORM `cascade="all, delete-orphan"` 与数据库层 `ondelete="CASCADE"` 双重保障级联删除。`(document_id, chunk_index)` 有唯一约束。

## 5. API 设计

所有路由前缀 `/api/v1`，为后续版本演进留余地。

### 文档管理

| 方法 | 路径 | 状态码 | 说明 |
|---|---|---|---|
| POST | `/api/v1/documents` | 201 | 上传 PDF（`multipart/form-data`） |
| GET | `/api/v1/documents` | 200 | 文档列表（按创建时间降序） |
| GET | `/api/v1/documents/{doc_id}` | 200 | 文档详情 |
| DELETE | `/api/v1/documents/{doc_id}` | 204 | 删除文档（DB 记录 + 磁盘文件 + 向量） |

### 问答

| 方法 | 路径 | 状态码 | 说明 |
|---|---|---|---|
| POST | `/api/v1/queries` | 200 | 提交问答请求 |

请求体示例：

```json
{
  "question": "这篇论文使用了什么数据集？",
  "document_ids": ["document-uuid"],
  "top_k": 5
}
```

响应体示例：

```json
{
  "answer": "论文使用了……[C1]",
  "citations": [
    {
      "document_id": "document-uuid",
      "document_name": "example.pdf",
      "page_number": 4,
      "chunk_index": 2,
      "snippet": "原文片段……",
      "score": 0.82
    }
  ],
  "request_id": "request-uuid",
  "elapsed_ms": 1380
}
```

### 错误响应

所有异常由全局处理器统一映射为 `{"detail": "..."}` 格式：

| 异常 | HTTP 状态码 | 语义 |
|---|---|---|
| `DuplicateDocumentError` | 409 | 重复上传 |
| `DocumentNotFoundError` | 404 | 文档不存在 |
| `NoAvailableDocumentsError` | 404 | 无可用 READY 文档 |
| `InsufficientEvidenceError` | 422 | 证据不足以回答 |
| `LlmServiceError` | 503 | LLM 服务不可用 |
| `EmbeddingServiceError` | 503 | Embedding 服务不可用 |
| `VectorStoreError` | 500 | 向量存储内部错误 |

## 6. 配置与密钥管理

通过 `.env` 保存本地配置，提交不含真实密钥的 `.env.example`。完整配置项见 [.env.example](../.env.example)。

安全要求：

- `.env` 在 `.gitignore` 中，禁止提交真实密钥
- 日志不输出 API 密钥、Authorization 头或完整文档内容
- 上传文件名不直接作为磁盘路径，`stored_name` 用 sha256 前缀生成
- 文件路径限制在项目配置的上传目录内
