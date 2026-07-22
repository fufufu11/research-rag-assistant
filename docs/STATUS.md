# 项目状态

## 当前状态

`v1.0` — 全部功能已完成，269 条测试通过，CI 三项全绿。

## 已完成功能

### PDF 解析与切分

- 按页提取文本并保留页码（PyMuPDF）
- 页内文本清洗与带重叠的 Chunk 切分（不跨页，LangChain `RecursiveCharacterTextSplitter`）
- 异常处理：文件不存在、损坏 PDF、空 PDF

### Embedding 与向量检索

- 基于 LangChain 的 Embedding 适配器（默认 `BAAI/bge-small-zh-v1.5`）
- Top-K 向量检索，按余弦相似度降序
- Qdrant 向量数据库（未配置时回退到 `InMemoryVectorStore`）

### 大模型问答与可靠引用

- OpenAI 兼容协议客户端，超时与重试由 httpx 实现指数退避
- 服务端引用映射：模型输出 `[C1]` 编号，服务端映射真实文档名、页码与片段
- 证据不足时拒绝猜测（`[INSUFFICIENT_EVIDENCE]`）

### FastAPI 与数据库

- 文档管理 API：上传 / 列表 / 详情 / 删除
- 问答 API：`POST /api/v1/queries`
- SQLAlchemy 2.0 数据模型 + Alembic 迁移
- sha256 去重、文件落盘、状态机（`pending → processing → ready / failed`）
- 全局异常处理器统一映射业务异常到 HTTP 状态码

### 演示界面

- Streamlit 界面：文档管理、问答、引用详情展示
- 通过 HTTP 调用 FastAPI，不直接 import 业务层

### 评测与质量优化

- 32 条检索评测数据集（基于真实论文 PDF）
- Hit@1 / Hit@5 / MRR / 平均检索耗时指标
- 5 组参数对比实验，详见 [评测报告](./evaluation_report.md)

## 技术栈

Python 3.11 · uv · FastAPI · Pydantic · PyMuPDF · LangChain · Qdrant · SQLAlchemy 2 + Alembic · Streamlit · pytest · Ruff + mypy · GitHub Actions

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
uv run python scripts/evaluate.py verify --pdf <pdf_path>
uv run python scripts/evaluate.py run --pdf <pdf_path>
```

## 测试状态

- pytest：269 passed
- ruff format --check：通过
- ruff check：通过
- mypy：CI 环境（Linux）通过；本机 Windows 因应用程序控制策略阻止 C 扩展加载无法运行

## 已知问题

1. **mypy 本机不可用**：uv 管理的独立 Python 的 C 扩展在 Windows 本机被应用程序控制策略阻止加载。CI 环境（Linux）使用系统 Python，不受影响。
2. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。
3. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体不含中文字形，CI 环境也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理。

## 后续可选方向

- 混合检索（BM25 + 向量）
- Cross-Encoder 或 BGE Reranker 重排序
- 跨页切分与表格感知切分
- 生成阶段（LLM 答案质量）评测
