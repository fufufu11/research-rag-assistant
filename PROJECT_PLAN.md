# 科研文献可溯源智能问答系统：项目开发章程

> 英文名称：Research RAG Assistant  
> 建议仓库名：`research-rag-assistant`  
> 文档版本：`v1.0`  
> 项目状态：规划中  
> 建议周期：8～10周，可根据身体状态暂停或顺延  
> 日均投入：45～60分钟  
> 目标岗位：Python开发实习生、大模型应用开发实习生、RAG/Agent应用开发实习生

---

## 1. 使用说明

这份文档既是项目需求说明，也是后续AI编码会话的长期上下文。创建GitHub仓库后，将本文件放在仓库根目录并命名为 `PROJECT_PLAN.md`。

开发时遵循以下原则：

1. 每次只完成一个小功能，不要求AI一次生成整个项目。
2. 先建立最小可运行版本，再逐步增加数据库、向量数据库、评测和部署。
3. 前四个阶段手动实现RAG核心流程，理解原理后再引入LangChain。
4. 每个功能都必须有验收标准，重要逻辑必须有自动化测试。
5. 所有性能和效果数字必须来自实际测试，禁止在README和简历中编造。
6. 不提交API密钥、个人文档、医疗资料或其他敏感信息。
7. 身体状态不适合开发时可以暂停。恢复后根据 `docs/STATUS.md` 继续，不需要赶进度。

---

## 2. 项目目标

### 2.1 产品目标

构建一个可以导入科研论文和技术文档的智能问答系统。用户提出问题后，系统只根据已导入的文档作答，并返回可核查的文档名称、页码和原文片段。

### 2.2 学习目标

完成项目后，应能够独立解释并实践：

- Python项目结构、类型标注、异常处理和依赖管理
- PDF解析、文本清洗、文本切分及其取舍
- Embedding、余弦相似度和向量检索
- RAG的检索、重排序、上下文构建和答案生成流程
- 大模型API、结构化输出、流式响应和错误重试
- FastAPI接口设计、参数校验和HTTP状态码
- SQLite/PostgreSQL、数据模型、事务和数据库迁移
- Qdrant向量数据库的集合、向量和Payload设计
- 自动化测试、代码检查、日志、Docker和持续集成
- 如何构建评测集并用数据评价检索效果
- 如何使用GitHub Issue、分支、Pull Request和Release管理项目

### 2.3 求职目标

项目完成后应形成：

- 一个公开且结构清晰的GitHub仓库
- 一份包含架构、运行方式和评测结果的README
- 一张系统架构图和一张RAG处理流程图
- 一个3～5分钟的演示视频
- 一份可重复执行的检索评测报告
- 两条包含真实数据的简历项目描述
- 一套能够应对项目追问的面试笔记

---

## 3. 项目边界

### 3.1 MVP必须实现

- 上传文本型PDF文件
- 提取每一页的文字并保留页码
- 文本清洗、分段和Embedding
- 根据问题检索相关文档片段
- 调用大模型生成仅基于上下文的答案
- 返回文档名、页码和原文片段作为引用
- 查看文档列表并删除文档
- 提供FastAPI接口和简单演示界面
- 提供至少30条人工构建的检索评测数据
- 使用Docker在本机一键启动最终版本
- 使用GitHub Actions执行代码检查和测试

### 3.2 暂不实现

以下功能不属于MVP，不得在核心功能完成前开发：

- 多智能体协作
- 模型训练、全量微调或分布式推理
- OCR和扫描版PDF识别
- Word、PPT、网页、音频等多格式解析
- 复杂前端、移动端和小程序
- 计费、支付和商业化功能
- 互联网规模的高并发架构
- Kubernetes部署

### 3.3 可选扩展

完成MVP和评测后，可以按优先级选择：

1. BM25与向量检索结合的混合检索
2. Cross-Encoder或BGE Reranker重排序
3. LangGraph查询路由与低置信度回退
4. 多用户登录和知识库权限隔离
5. 对话历史和上下文压缩
6. 文档增量更新与版本管理
7. 使用对象存储保存原始文件

---

## 4. 用户故事与验收标准

### US-001：上传文档

作为用户，我可以上传一个PDF。系统应验证文件、计算哈希值、提取文本、生成文档分段，并显示处理结果。

- 仅允许合法PDF，默认最大20 MB，可通过环境变量调整
- 文件扩展名与文件内容都要验证
- 扫描版或无文本PDF应返回明确提示
- 同一文件重复上传时不得产生重复数据
- 处理失败时记录失败状态和可理解的错误信息

### US-002：管理文档

作为用户，我可以查看已上传文档的名称、页数、处理状态和上传时间，也可以删除文档。

- 删除文档时，同时删除元数据、文本分段和对应向量
- 文件不存在时返回规范的404响应
- 不允许通过文件名访问任意本地路径

### US-003：文档问答

作为用户，我可以输入问题并选择查询全部或部分文档。系统返回答案和引用来源。

- 答案不得脱离检索上下文随意补充事实
- 证据不足时明确回答“根据当前文档无法确定”
- 每条引用包含文档名、页码、片段和相关度信息
- 模型引用上下文编号，服务端再映射到真实页码
- 返回请求ID和处理耗时，便于排查问题

### US-004：检索评测

作为开发者，我可以运行固定评测集，比较不同切分和检索参数的效果。

- 评测集至少包含30个问题
- 每个问题标记相关文档和相关页码
- 输出Hit@K、MRR、平均检索耗时等指标
- 评测结果保存为带日期的Markdown或JSON文件
- 参数调整前后有可比较的结果

---

## 5. 技术选型

| 类别 | 选择 | 说明 |
|---|---|---|
| 编程语言 | Python 3.11 | 兼容性好，适合AI应用开发 |
| 依赖管理 | `uv` + `pyproject.toml` | 速度快，便于锁定依赖 |
| Web框架 | FastAPI + Pydantic | 学习API、类型校验和异步接口 |
| PDF解析 | PyMuPDF | 按页提取文本并保留页码 |
| 初始向量检索 | NumPy余弦相似度 | 先理解向量检索原理 |
| 最终向量数据库 | Qdrant | 支持Payload过滤，Docker部署方便 |
| Embedding | `BAAI/bge-small-zh-v1.5`或同级中文模型 | 本地模型；模型名可配置 |
| 大模型 | 支持OpenAI兼容协议的模型服务 | 通过环境变量切换模型 |
| 元数据数据库 | SQLite起步，最终迁移PostgreSQL | 先降低难度，再学习正式数据库 |
| ORM与迁移 | SQLAlchemy 2 + Alembic | 学习数据模型和数据库迁移 |
| 简单界面 | Streamlit | 快速形成可演示产品 |
| 测试 | pytest + pytest-asyncio + httpx | 单元测试和API集成测试 |
| 代码质量 | Ruff + mypy + pre-commit | 格式、静态检查和提交前检查 |
| 容器化 | Docker + Docker Compose | 统一运行环境 |
| 持续集成 | GitHub Actions | PR自动执行检查和测试 |
| RAG框架 | LangChain，MVP中后期引入 | 先手写流程，再比较框架实现 |
| 可选工作流 | LangGraph | 仅在基础RAG稳定后使用 |

### 5.1 为什么不先使用LangChain

如果从第一天就使用框架，很容易只记住API，不理解文档切分、向量归一化、Top-K检索和引用映射。项目先用普通Python手动实现核心流程，再在独立分支中使用LangChain重构。最终需要能够说明：

- 不使用LangChain时，RAG流程如何运行
- LangChain帮项目减少了哪些代码
- 哪些业务逻辑仍然由项目自己控制
- 如果框架版本变化，如何替换组件

---

## 6. 目标架构

```text
┌──────────────────┐
│ Streamlit Demo UI│
└────────┬─────────┘
         │ HTTP
┌────────▼─────────┐
│     FastAPI      │
│ validation/error│
└──────┬───────┬───┘
       │       │
       │       └──────────────────────────┐
┌──────▼──────┐                    ┌──────▼──────┐
│Document Flow│                    │ Query Flow  │
│parse/chunk  │                    │retrieve/LLM │
└───┬─────┬───┘                    └───┬─────┬───┘
    │     │                            │     │
┌───▼──┐ ┌▼──────────┐          ┌─────▼─┐ ┌─▼────────┐
│Files │ │PostgreSQL │          │Qdrant│ │LLM API   │
└──────┘ │metadata   │          │vector│ │compatible│
         └───────────┘          └──────┘ └──────────┘
```

### 6.1 文档处理流程

```text
上传PDF
  → 文件类型、大小和哈希校验
  → 按页提取文本
  → 清洗空白字符
  → 按页切分并保留重叠区
  → 生成Embedding
  → 保存文档和分段元数据
  → 写入向量数据库
  → 更新文档处理状态
```

### 6.2 问答流程

```text
用户问题
  → 参数校验
  → 问题Embedding
  → 向量检索Top-K
  → 可选混合检索与Rerank
  → 构造带编号的上下文
  → 大模型生成结构化答案和上下文编号
  → 服务端映射真实文档名、页码和片段
  → 返回答案、引用、耗时和请求ID
```

---

## 7. 数据模型

### 7.1 Document

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 文档唯一标识 |
| `original_name` | string | 用户上传时的文件名 |
| `stored_name` | string | 服务端生成的安全文件名 |
| `sha256` | string | 文件哈希，用于去重 |
| `page_count` | integer | PDF页数 |
| `status` | enum | `pending/processing/ready/failed` |
| `error_message` | nullable string | 处理失败原因 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

### 7.2 Chunk

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 分段唯一标识 |
| `document_id` | UUID | 所属文档 |
| `page_number` | integer | 原始页码，从1开始 |
| `chunk_index` | integer | 文档内分段序号 |
| `content` | text | 分段文本 |
| `char_count` | integer | 字符数 |
| `vector_id` | string | Qdrant中的向量ID |
| `created_at` | datetime | 创建时间 |

### 7.3 QueryLog（MVP后期）

只记录调试和性能所需信息，不记录密钥或完整私人文档内容。

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | UUID | 请求标识 |
| `question` | text | 用户问题，生产环境可配置为不保存 |
| `document_ids` | JSON | 查询范围 |
| `retrieval_ms` | integer | 检索耗时 |
| `generation_ms` | integer | 生成耗时 |
| `total_ms` | integer | 总耗时 |
| `model_name` | string | 使用的模型 |
| `created_at` | datetime | 创建时间 |

---

## 8. API草案

### 8.1 健康检查

```http
GET /api/v1/health
GET /api/v1/ready
```

### 8.2 上传文档

```http
POST /api/v1/documents
Content-Type: multipart/form-data
```

成功返回：

```json
{
  "id": "document-uuid",
  "original_name": "example.pdf",
  "page_count": 12,
  "status": "ready"
}
```

### 8.3 文档管理

```http
GET    /api/v1/documents
GET    /api/v1/documents/{document_id}
DELETE /api/v1/documents/{document_id}
```

### 8.4 文档问答

```http
POST /api/v1/queries
Content-Type: application/json
```

请求：

```json
{
  "question": "这篇论文使用了什么数据集？",
  "document_ids": ["document-uuid"],
  "top_k": 5
}
```

响应：

```json
{
  "answer": "论文使用了……",
  "citations": [
    {
      "document_id": "document-uuid",
      "document_name": "example.pdf",
      "page_number": 4,
      "snippet": "原文片段……",
      "score": 0.82
    }
  ],
  "request_id": "request-uuid",
  "elapsed_ms": 1380
}
```

### 8.5 错误响应

```json
{
  "error": {
    "code": "INVALID_PDF",
    "message": "文件不是可解析的文本型PDF。",
    "request_id": "request-uuid"
  }
}
```

---

## 9. 初始RAG设计

### 9.1 文本切分

初始参数：

```text
chunk_size = 500个中文字符左右
chunk_overlap = 80个字符左右
top_k = 8
```

这些只是第一版参数，不是固定答案。最终参数必须通过评测集比较后确定。

- 不跨页切分，确保引用页码准确
- 优先在段落或句号附近切分
- 每个Chunk保留文档ID、页码和序号
- 过滤只有页眉、页脚或极少字符的片段
- 不应过度清洗导致公式编号和关键术语丢失

### 9.2 Embedding与检索

第一版使用本地Embedding模型，将向量归一化后通过NumPy计算余弦相似度。确认流程正确后迁移到Qdrant。

需要理解：

- Embedding表达的是语义相似性，不等于事实正确性
- 向量归一化如何影响余弦相似度计算
- `top_k`过大和过小分别有什么问题
- 为什么元数据过滤应在检索阶段处理

### 9.3 答案生成与引用

Prompt必须要求模型：

1. 只能使用提供的上下文作答。
2. 证据不足时明确说明无法确定。
3. 使用上下文编号引用证据，例如 `[C1]`、`[C3]`。
4. 不得自行编造文档名、页码和参考文献。

服务端根据模型返回的上下文编号映射真实引用，减少模型编造页码的问题。

---

## 10. 仓库结构

```text
research-rag-assistant/
├─ .github/
│  ├─ workflows/ci.yml
│  ├─ ISSUE_TEMPLATE/
│  └─ pull_request_template.md
├─ docs/
│  ├─ STATUS.md
│  ├─ architecture.md
│  ├─ decisions/
│  └─ interview-notes.md
├─ eval/
│  ├─ dataset.example.jsonl
│  └─ reports/
├─ scripts/
│  ├─ parse_pdf.py
│  ├─ evaluate_retrieval.py
│  └─ seed_demo_data.py
├─ src/
│  └─ research_rag/
│     ├─ api/
│     │  ├─ routes/
│     │  └─ dependencies.py
│     ├─ core/
│     │  ├─ config.py
│     │  ├─ exceptions.py
│     │  └─ logging.py
│     ├─ db/
│     │  ├─ models.py
│     │  ├─ repositories.py
│     │  └─ session.py
│     ├─ schemas/
│     ├─ services/
│     │  ├─ pdf_parser.py
│     │  ├─ chunker.py
│     │  ├─ embedding.py
│     │  ├─ retriever.py
│     │  └─ qa_service.py
│     └─ main.py
├─ streamlit_app/
│  └─ app.py
├─ tests/
│  ├─ fixtures/
│  ├─ unit/
│  └─ integration/
├─ .env.example
├─ .gitignore
├─ .pre-commit-config.yaml
├─ docker-compose.yml
├─ Dockerfile
├─ LICENSE
├─ PROJECT_PLAN.md
├─ README.md
├─ pyproject.toml
└─ uv.lock
```

不需要第一天创建所有文件。目录应随功能逐步增加，禁止创建大量空模块。

---

## 11. 配置与密钥管理

通过 `.env` 保存本地配置，并提交不含真实密钥的 `.env.example`。

```dotenv
APP_ENV=development
LOG_LEVEL=INFO
DATABASE_URL=sqlite:///./data/app.db
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=research_chunks
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
EMBEDDING_MODEL=BAAI/bge-small-zh-v1.5
UPLOAD_DIR=./data/uploads
MAX_UPLOAD_MB=20
CHUNK_SIZE=500
CHUNK_OVERLAP=80
RETRIEVAL_TOP_K=8
```

安全要求：

- `.env`必须在 `.gitignore` 中
- 日志不得输出API密钥、Authorization头或完整文档内容
- 上传文件名不得直接作为磁盘路径
- 文件路径必须限制在项目配置的上传目录内
- 公布仓库前执行密钥扫描并检查Git历史

---

## 12. 标准开发流程

采用适合个人项目的主干开发方式：`main`保持可运行，每个任务使用短生命周期分支。

### 12.1 初始化GitHub仓库

1. 在GitHub创建公开仓库 `research-rag-assistant`。
2. 添加Python `.gitignore` 和MIT License。
3. 克隆到本地，创建 `PROJECT_PLAN.md`、`README.md` 和 `docs/STATUS.md`。
4. 在GitHub开启Issues和Projects。
5. 为 `main` 开启分支保护；如果个人账号限制无法开启，仍坚持通过PR合并。
6. 创建里程碑：`v0.1 CLI`、`v0.2 RAG MVP`、`v0.3 Quality`、`v1.0 Resume Ready`。

### 12.2 每个功能的工作循环

```text
创建Issue
  → 写清目标和验收标准
  → 从main创建功能分支
  → 先阅读相关代码和测试
  → 小步实现
  → 添加或更新测试
  → 本地执行全部检查
  → 更新文档
  → 提交Pull Request
  → 自己进行代码审查
  → CI通过后合并
  → 更新docs/STATUS.md并关闭Issue
```

### 12.3 分支命名

```text
feat/pdf-parser
feat/vector-retrieval
feat/query-api
fix/duplicate-upload
test/retrieval-evaluation
docs/architecture
chore/github-actions
```

### 12.4 Commit规范

使用Conventional Commits，单次提交只表达一个逻辑变化。

```text
feat: add page-aware PDF parser
fix: prevent duplicate document ingestion
test: cover empty PDF parsing failure
docs: explain retrieval evaluation workflow
refactor: isolate embedding provider interface
chore: add ruff checks to CI
```

避免使用 `update`、`修改代码`、`final version`、`fix bugs` 等模糊信息。

### 12.5 Pull Request模板

```markdown
## 变更内容

## 为什么需要这项变更

## 如何验证

## 测试结果

## 风险与未完成事项

## 关联Issue
Closes #编号
```

### 12.6 Definition of Done

一个Issue只有同时满足以下条件才算完成：

- 验收标准全部满足
- 代码能在本机运行
- 新增逻辑有适当测试
- Ruff、mypy和pytest通过
- 没有提交密钥、缓存、模型文件和个人PDF
- 错误处理和日志合理
- README或相关文档已同步更新
- PR已自查并合并到 `main`
- `docs/STATUS.md` 已记录当前状态和下一步

---

## 13. 测试、日志与错误处理

### 13.1 单元测试

- PDF按页解析以及空文档处理
- 文本切分长度、重叠和页码保持
- 文件哈希和重复判断
- 向量归一化与相似度排序
- 上下文编号与真实引用映射
- 配置读取和输入校验

### 13.2 集成测试

- 上传合法PDF并生成文档记录
- 上传非法文件返回400
- 重复上传返回已有文档或明确冲突响应
- 查询接口返回答案和引用
- 删除文档后元数据和向量同时消失

测试中应Mock模型API和Embedding服务，CI不得依赖外部网络或消耗付费Token。

### 13.3 测试数据

- 使用自己编写或公开许可的短文档
- 可以在测试中动态生成两页PDF
- 不把下载的论文全文、个人材料或医疗资料提交到仓库
- `eval/dataset.example.jsonl` 只包含可公开的示例数据

### 13.4 预期检查命令

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

### 13.5 日志要求

- 记录时间、级别、模块和 `request_id`
- 记录文档ID，不记录完整文档内容
- 记录解析、Embedding、检索和生成耗时
- 记录外部模型调用状态和重试次数
- 不记录API密钥或Authorization头

### 13.6 项目级异常

```text
InvalidPdfError
EmptyDocumentError
DuplicateDocumentError
DocumentNotFoundError
EmbeddingServiceError
VectorStoreError
LlmServiceError
InsufficientEvidenceError
```

API层负责将异常转换为稳定错误码，业务服务不直接拼接HTTP响应。

---

## 14. 八阶段开发路线

每个阶段可以用一周，也可以根据身体状态延长。优先保证理解，不追赶日历。

### 阶段0：仓库与工程基础

交付物：GitHub仓库、README骨架、Python包、Ruff、mypy、pytest、pre-commit、GitHub Actions和 `docs/STATUS.md`。

学习内容：虚拟环境、依赖锁文件、Git工作区/暂存区/提交、Issue/分支/PR/CI的关系。

验收：克隆全新仓库后，可以按照README安装依赖并通过全部检查。

### 阶段1：PDF解析命令行工具

交付物：输入PDF路径，输出每页页码、字符数和前200字；包含异常处理和单元测试。

学习内容：Python函数、类、Path、异常、类型标注、二进制文件与文本的区别。

验收：合法文本PDF正确解析；空PDF、损坏文件和不存在路径均有明确结果。

### 阶段2：文本清洗与切分

交付物：页内文本清洗、带重叠的Chunk切分器、页码和序号元数据、边界测试。

学习内容：切分大小、重叠和召回效果的关系；纯函数和可测试设计。

验收：切分长度符合配置，重叠正确，页码不丢失，空白片段被过滤。

### 阶段3：Embedding与手写向量检索

交付物：本地Embedding适配器、NumPy余弦相似度、Top-K检索和最小评测脚本。

学习内容：Embedding、向量维度、归一化、相似度和批量计算。

验收：对示例文档的5～10个问题返回合理片段，并保存第一版基线结果。

### 阶段4：大模型回答与可靠引用

交付物：OpenAI兼容模型客户端、结构化答案、引用映射、超时和有限重试。

学习内容：消息与上下文构建、结构化输出、幻觉控制、超时和重试。

验收：答案带真实引用；证据不足时拒绝猜测；测试不消耗真实Token。

### 阶段5：FastAPI与数据库

交付物：文档上传/列表/详情/删除/问答接口，SQLAlchemy模型、Alembic迁移、统一错误、日志和集成测试。

学习内容：HTTP方法、状态码、Pydantic校验、事务、迁移以及API层和服务层职责。

验收：可以通过API完成上传、查询和删除，自动测试覆盖主要成功和失败路径。

### 阶段6：Qdrant、LangChain与演示界面

交付物：迁移Qdrant，在独立分支比较LangChain实现，完成Streamlit界面。

学习内容：向量库与关系数据库的分工、Payload过滤、框架收益和依赖成本。

验收：浏览器完成完整流程；删除文档后无残留向量；能够脱离LangChain解释RAG。

### 阶段7：评测与质量优化

交付物：至少30条评测数据，Hit@1、Hit@5、MRR、平均检索耗时，至少两组参数对比，可选混合检索或Rerank。

学习内容：检索失败与生成失败的区分，以及效果、延迟和成本的取舍。

验收：评测可复现，记录数据集、参数、环境、结果和结论。

### 阶段8：部署、文档与发布

交付物：Dockerfile、Docker Compose、PostgreSQL、Qdrant、API和UI组合启动，完整README、架构图、演示视频和 `v1.0.0` Release。

学习内容：镜像、容器、卷、网络、健康检查和可复现部署。

验收：在干净环境中按照README可以启动系统并完成一次带引用的问答。

---

## 15. 建议Issue清单

| Issue | 标题 | 里程碑 |
|---|---|---|
| #1 | 初始化Python项目与质量工具 | v0.1 CLI |
| #2 | 配置GitHub Actions持续集成 | v0.1 CLI |
| #3 | 实现按页PDF解析器 | v0.1 CLI |
| #4 | 实现页内文本切分器 | v0.1 CLI |
| #5 | 实现Embedding适配器 | v0.1 CLI |
| #6 | 使用NumPy实现Top-K检索 | v0.1 CLI |
| #7 | 接入大模型并返回可靠引用 | v0.2 RAG MVP |
| #8 | 建立文档和Chunk数据模型 | v0.2 RAG MVP |
| #9 | 实现文档管理API | v0.2 RAG MVP |
| #10 | 实现问答API | v0.2 RAG MVP |
| #11 | 迁移到Qdrant向量数据库 | v0.2 RAG MVP |
| #12 | 增加Streamlit演示界面 | v0.2 RAG MVP |
| #13 | 建立30条检索评测集 | v0.3 Quality |
| #14 | 比较切分参数并生成报告 | v0.3 Quality |
| #15 | 加入混合检索或Rerank | v0.3 Quality |
| #16 | 使用LangChain重构并记录对比 | v0.3 Quality |
| #17 | 增加Docker Compose部署 | v1.0 Resume Ready |
| #18 | 完善README、架构图和演示 | v1.0 Resume Ready |
| #19 | 完成安全检查与首次Release | v1.0 Resume Ready |

Issue编号以GitHub实际创建结果为准。可以预先创建和分类Issue，但不要一次生成所有实现代码。

---

## 16. 第一周每日任务

每天只做一项，完成后记录在 `docs/STATUS.md`。

### Day 1：创建仓库

- 创建GitHub仓库
- 添加README、License、`.gitignore` 和本项目章程
- 创建Issue：初始化Python项目

### Day 2：建立Python工程

- 安装Python 3.11和uv
- 创建 `pyproject.toml`
- 建立 `src/` 和 `tests/`
- 运行一个最小测试

### Day 3：配置代码质量

- 配置Ruff、mypy和pytest
- 配置pre-commit
- 确认本地检查全部通过

### Day 4：配置持续集成

- 创建GitHub Actions工作流
- 提交Pull Request
- 确认CI成功后合并

### Day 5：开始PDF解析

- 创建解析器Issue和分支
- 学习PyMuPDF最基本用法
- 输出每一页页码和字符数

### Day 6：处理异常

- 处理文件不存在、文件损坏和空PDF
- 增加对应测试

### Day 7：整理与复盘

- 更新README运行方式
- 更新 `docs/STATUS.md`
- 写下本周学到的3件事和仍不理解的2个问题
- 创建并合并PDF解析PR

---

## 17. AI辅助编码规范

AI可以帮助规划、解释、编码和测试，但代码所有权仍属于开发者。不要合并自己无法解释的代码。

### 17.1 新AI窗口需要读取

1. `PROJECT_PLAN.md`
2. `README.md`
3. `docs/STATUS.md`
4. 当前GitHub Issue
5. `git status` 和最近的提交记录

### 17.2 新AI窗口启动提示词

将下面内容发送给新的AI编码窗口，并替换方括号部分：

```text
你正在协助我开发“科研文献可溯源智能问答系统”。

请先完整阅读仓库中的 PROJECT_PLAN.md、README.md 和 docs/STATUS.md，
然后查看当前代码、测试、git status 和最近提交。当前只处理 GitHub Issue：
[填写Issue标题和链接]

工作要求：
1. 先用中文说明你对当前状态、任务边界和验收标准的理解。
2. 在修改代码前给出一个小步实施计划。
3. 不要实现当前Issue之外的功能，不要进行无关重构。
4. 新增依赖前先说明必要性和替代方案。
5. 重要业务逻辑必须添加测试，外部模型调用必须在测试中Mock。
6. 不要读取、打印或提交任何真实API密钥和个人文档。
7. 完成后运行格式检查、静态检查和测试。
8. 用初学者能够理解的方式解释关键代码和设计取舍。
9. 更新相关文档和 docs/STATUS.md，但不要替我编造评测或性能数据。
10. 最后给出建议的Conventional Commit信息，但不要擅自推送远程仓库。
```

### 17.3 AI完成代码后必须追问

```text
请回答：
1. 这次修改的数据是怎样流动的？
2. 最重要的三个函数分别负责什么？
3. 哪些错误路径已经测试，哪些还没有？
4. 如果不使用当前框架，核心逻辑应如何实现？
5. 请给我一个可以亲手修改的小练习，并说明如何验证。
```

### 17.4 禁止的AI使用方式

- 让AI一次生成完整项目后直接提交
- 不看Diff就合并
- 测试失败时删除测试
- 为了“显得高级”随意增加框架和抽象层
- 将API密钥粘贴到聊天、源码或截图中
- 使用AI编造吞吐量、准确率、成本和用户数据
- 复制自己无法在面试中解释的代码

---

## 18. 暂停与恢复流程

每次开发结束前更新 `docs/STATUS.md`：

```markdown
# Current Status

## 当前版本

## 已完成

## 当前Issue与分支

## 正在处理的问题

## 本地运行命令

## 测试状态

## 下一步最小任务

## 尚未提交的改动

## 最近学到的内容
```

暂停一段时间后按以下顺序恢复：

1. 阅读 `PROJECT_PLAN.md` 和 `docs/STATUS.md`。
2. 查看 `git status`、当前分支和最近5次提交。
3. 安装锁定依赖并运行全部测试。
4. 不立即升级依赖或重构。
5. 从“下一步最小任务”继续。

---

## 19. README最终应包含

- 项目一句话介绍和演示截图
- 解决的问题与使用场景
- 功能列表和限制
- 系统架构图和RAG流程图
- 技术栈及选择理由
- 本地运行和Docker运行步骤
- 环境变量说明和API示例
- 评测数据集、运行方法和实际结果
- 测试和CI状态
- 安全与隐私说明
- 已知问题和后续计划
- License

README不要写成长篇学习笔记。详细设计、决策和面试复盘放在 `docs/`。

---

## 20. 简历与面试准备

### 20.1 简历描述模板

项目完成后根据真实结果填写，不得提前虚构：

```text
科研文献可溯源智能问答系统 | Python、FastAPI、Qdrant、PostgreSQL、LangChain、Docker

- 独立设计并实现PDF解析、分段、Embedding、向量检索和大模型回答链路，
  通过服务端引用映射返回文档名、页码和原文片段，降低模型虚构引用风险。
- 构建包含[X]个问题的检索评测集，对比[X]组切分与检索参数，
  将Hit@5由[X]提升至[X]，平均检索耗时为[X]毫秒。
- 使用FastAPI、PostgreSQL和Qdrant实现文档管理与问答接口，
  通过Docker Compose完成可复现部署，并使用GitHub Actions执行代码检查和自动化测试。
```

### 20.2 必须能回答的面试问题

1. 为什么选择RAG，而不是微调模型？
2. 文本切分大小和重叠是怎样确定的？
3. Embedding和Rerank分别解决什么问题？
4. 为什么要保留页码，怎样避免模型编造引用？
5. Qdrant和PostgreSQL分别保存什么数据？
6. 文档删除时怎样保证数据库和向量库一致？
7. 如何处理重复上传、空PDF和模型超时？
8. 为什么测试时要Mock模型API？
9. Hit@K和MRR怎样计算，各有什么局限？
10. LangChain给项目带来了什么，也带来了哪些成本？
11. 如果用户上传恶意文件或敏感文档，系统应该怎样处理？
12. 如果数据量增加100倍，最先出现的瓶颈可能是什么？

---

## 21. 项目完成标准

- [ ] GitHub仓库历史清晰，使用Issue、分支和PR开发
- [ ] 文本型PDF可以上传、解析、查询和删除
- [ ] 答案包含由服务端验证的文档名、页码和原文片段
- [ ] 非法、空白和重复PDF有明确处理
- [ ] 至少30条检索评测数据及可复现报告
- [ ] 至少比较两组参数，所有数据来自实际运行
- [ ] 关键模块有单元测试，主要接口有集成测试
- [ ] CI自动执行格式、静态检查和测试
- [ ] `.env`、个人PDF和密钥从未进入Git历史
- [ ] Docker Compose可以启动最终系统
- [ ] README足以让陌生人在干净环境中运行项目
- [ ] 有架构图、演示视频、简历描述和面试笔记
- [ ] 开发者能够不依赖AI解释核心RAG流程和主要代码

---

## 22. 当前第一步

现在只执行以下任务，不开始写RAG业务代码：

1. 在GitHub创建仓库 `research-rag-assistant`。
2. 将本文件复制为仓库根目录的 `PROJECT_PLAN.md`。
3. 创建简短的 `README.md` 和 `docs/STATUS.md`。
4. 创建第一个Issue：`chore: 初始化Python项目与质量工具`。
5. 从 `main` 创建分支 `chore/project-bootstrap`。
6. 在新的AI编码窗口中使用第17.2节的启动提示词，只完成第一个Issue。

完成第一个Issue并通过Pull Request合并后，再进入PDF解析阶段。
