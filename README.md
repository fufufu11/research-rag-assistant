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

## Docker 部署

通过 Docker Compose 一键启动 API + Qdrant + PostgreSQL 三服务（阶段 11.4）。

### 前置要求

- [Docker](https://www.docker.com/) 20+
- [Docker Compose](https://docs.docker.com/compose/) v2+（Docker Desktop 已内置）

### 配置环境变量

复制部署示例配置并填入真实值：

```powershell
cp .env.docker.example .env
```

至少需要配置 LLM 相关变量（OpenAI 兼容协议），完整配置项见 [.env.docker.example](./.env.docker.example)。

### 启动服务

```powershell
# 构建镜像并后台启动三服务（首次构建约 5-10 分钟，含 Embedding 模型下载）
docker compose up -d --build

# 查看 API 日志
docker compose logs -f api

# 查看服务状态
docker compose ps
```

启动完成后：

- API 服务：http://localhost:8000（API 文档 http://localhost:8000/docs）
- Qdrant 向量库：http://localhost:6333
- PostgreSQL：localhost:5432

API 容器启动时会自动执行 `alembic upgrade head` 数据库迁移，无需手动初始化 schema。

### 停止与清理

```powershell
# 停止服务（保留数据卷）
docker compose down

# 停止并删除数据卷（清空 PostgreSQL + Qdrant + 上传文件，谨慎使用）
docker compose down -v
```

### 数据持久化

| 卷名 | 挂载点 | 用途 |
|---|---|---|
| rrag-postgres-data | /var/lib/postgresql/data | PostgreSQL 元数据（文档/会话/反馈） |
| rrag-qdrant-data | /qdrant/storage | Qdrant 向量数据 |
| rrag-api-uploads | /app/data/uploads | 上传的 PDF 文件 |

### 与 Streamlit UI 配合

Docker Compose 只容器化 API 服务。Streamlit UI 在本地运行，指向容器化 API：

```powershell
uv sync --extra dev
$env:API_BASE_URL="http://localhost:8000/api/v1"
uv run streamlit run src/research_rag/ui/app.py
```

## CI/CD 自动化部署

push 到 main 分支（CI 全绿后）自动构建 Docker 镜像并推送到 GitHub Container Registry，可选 SSH 自动部署到生产服务器（阶段 11.5）。

### 流水线

1. **CI**（`.github/workflows/ci.yml`）：PR 与 push 到 main 时运行 Lint / Type Check / Test 三项
2. **Deploy**（`.github/workflows/deploy.yml`）：CI 在 main 成功完成后自动触发
   - **构建并推送镜像**：构建 Dockerfile，推送到 `ghcr.io/fufufu11/research-rag-assistant`，双标签 `:latest` 与 `:sha-<short-commit>`
   - **SSH 部署**（可选）：SSH 登录生产服务器，`docker compose pull && up -d`，健康检查验证

### 镜像拉取（生产服务器）

生产服务器用 `docker-compose.prod.yml` 覆盖文件，引用 GHCR 预构建镜像而非本地 build：

```bash
# 服务器上准备 docker-compose.yml + docker-compose.prod.yml + .env
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

回滚到历史版本（通过 sha 标签）：

```bash
IMAGE_TAG=sha-1a2b3c4 docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 配置 Secrets / Variables（SSH 自动部署）

在 GitHub 仓库 Settings → Secrets and variables → Actions 中配置：

**Secrets**（敏感信息，部署所需）：

| 名称 | 说明 |
|---|---|
| `SSH_HOST` | 生产服务器 IP 或域名 |
| `SSH_USER` | SSH 登录用户名（如 `ubuntu`） |
| `SSH_PRIVATE_KEY` | SSH 私钥（完整内容，含 `-----BEGIN ... PRIVATE KEY-----`） |

**Variables**（非敏感配置，控制部署行为）：

| 名称 | 说明 | 示例值 |
|---|---|---|
| `ENABLE_SSH_DEPLOY` | 设为 `true` 启用 SSH 自动部署（未设置或非 `true` 时只构建不部署） | `true` |
| `DEPLOY_PATH` | 服务器上 docker-compose.yml 所在目录 | `/opt/rrag` |

未配置 `ENABLE_SSH_DEPLOY=true` 时，Deploy workflow 只构建并推送镜像到 GHCR，不执行 SSH 部署（本地开发友好，不强制配置服务器）。

### 手动触发

在 GitHub 仓库 Actions 页面选择 `Deploy` workflow → `Run workflow` 可手动触发构建（不依赖 CI 完成）。

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
