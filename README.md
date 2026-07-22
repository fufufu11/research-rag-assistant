# Research RAG Assistant

科研文献可溯源智能问答系统：导入科研 PDF 后，仅基于已导入文档作答，并返回可核查的文档名、页码与原文片段。

## 功能特性

- 上传文本型 PDF，按页提取文本并保留页码
- 文本清洗、分段、Embedding 与向量检索
- 调用大模型生成仅基于上下文的答案，证据不足时拒绝猜测
- 返回文档名、页码、原文片段作为引用，引用由服务端映射，避免模型编造
- 提供 FastAPI 接口与 Streamlit 演示界面
- sha256 去重、文档状态机、删除时同步清理数据库与向量库
- 检索评测集与可复现报告（Hit@1 / Hit@5 / MRR / 平均检索耗时）

## 技术栈

Python 3.11 · uv · FastAPI · Pydantic · PyMuPDF · LangChain · Qdrant · SQLAlchemy 2 + Alembic · Streamlit · pytest · Ruff + mypy · GitHub Actions

## 快速开始

### 前置要求

- [uv](https://docs.astral.sh/uv/) 0.11+
- Python 3.11（uv 会自动安装）
- 可选：[Docker](https://www.docker.com/)（用于运行 Qdrant 向量数据库）

### 安装

```powershell
git clone https://github.com/fufufu11/research-rag-assistant.git
cd research-rag-assistant
uv sync --extra dev
```

### 配置环境变量

复制示例配置并填入真实值：

```powershell
cp .env.example .env
```

至少需要配置 LLM 相关变量（OpenAI 兼容协议）：

```dotenv
LLM_BASE_URL=https://api.example.com/v1
LLM_API_KEY=your-key
LLM_MODEL=gpt-4o-mini
```

完整配置项说明见 [.env.example](./.env.example)。

### 初始化数据库

```powershell
uv run alembic upgrade head
```

### 启动服务

```powershell
# 启动 FastAPI API 服务（端口 8000）
uv run uvicorn research_rag.api.app:create_app --factory --reload --port 8000

# 另开终端，启动 Streamlit 演示界面（端口 8501）
uv run streamlit run src/research_rag/ui/app.py
```

打开 http://localhost:8501 即可使用演示界面：上传 PDF → 提问 → 查看带引用的答案。

## 使用说明

### API 接口

API 文档由 FastAPI 自动生成，启动服务后访问 http://localhost:8000/docs。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/documents` | 上传 PDF（`multipart/form-data`） |
| GET | `/api/v1/documents` | 文档列表 |
| GET | `/api/v1/documents/{doc_id}` | 文档详情 |
| DELETE | `/api/v1/documents/{doc_id}` | 删除文档 |
| POST | `/api/v1/queries` | 提交问答请求 |

问答请求示例：

```bash
curl -X POST http://localhost:8000/api/v1/queries \
  -H "Content-Type: application/json" \
  -d '{"question": "这篇论文使用了什么数据集？", "top_k": 5}'
```

### 命令行工具

```powershell
# PDF 解析 CLI：输出每页页码、字符数和前 200 字预览
# 退出码：0 成功，2 文件不存在，3 文件损坏，4 空 PDF
uv run python scripts/parse_pdf.py <pdf_path>
```

### 评测

```powershell
# 安装 Embedding 推理后端
uv sync --extra embedding

# 验证评测数据集子串匹配
uv run python scripts/evaluate.py verify --pdf <pdf_path>

# 运行检索评测（Hit@1 / Hit@5 / MRR / 平均耗时 + 参数对比）
uv run python scripts/evaluate.py run --pdf <pdf_path>
```

评测结果与结论见 [docs/evaluation_report.md](./docs/evaluation_report.md)。

## 项目结构

```text
research-rag-assistant/
├─ src/research_rag/
│  ├─ api/            # FastAPI 路由、schema、依赖注入、异常处理
│  ├─ db/             # SQLAlchemy 模型、Repository、session
│  ├─ services/       # 业务编排层（DocumentService / QaService）
│  ├─ ui/             # Streamlit 演示界面与 API 客户端
│  ├─ pdf_parser.py   # 按页 PDF 解析
│  ├─ chunker.py      # 页内文本清洗与切分
│  ├─ embedding.py    # Embedding 适配器与向量检索
│  ├─ vector_store.py # Qdrant 适配器
│  ├─ qa_service.py   # LLM 调用与引用映射
│  └─ evaluation.py   # 检索评测指标
├─ tests/unit/        # 单元测试（269 条）
├─ scripts/           # CLI 脚本（parse_pdf / evaluate / run_server）
├─ eval/              # 评测数据集
├─ alembic/           # 数据库迁移
└─ docs/              # 架构文档、状态总览、评测报告
```

架构设计、数据模型、API 设计与 RAG 处理流程详见 [docs/architecture.md](./docs/architecture.md)。

## 测试

```powershell
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
```

测试中 Mock 外部模型调用（LLM、Embedding），CI 不依赖外部网络或消耗付费 Token。

## 开发流程

采用主干开发：`main` 保持可运行，每个任务使用短生命周期分支，通过 Pull Request 合并。Commit 遵循 [Conventional Commits](https://www.conventionalcommits.org/)。

## 安全与隐私

- `.env` 保存本地配置，不提交真实密钥
- 上传的文档与日志中不包含 API 密钥、Authorization 头或完整私人文档内容
- 文件路径限制在项目配置的上传目录内，`stored_name` 用 sha256 前缀生成以避免路径遍历

## License

[MIT License](./LICENSE)
