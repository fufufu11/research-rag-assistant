# 项目状态

## 当前状态

`v1.3` — 阶段 0-10.1 全部完成，530+ 条测试通过，CI 三项全绿。

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
- SQLAlchemy 2.0 数据模型（Document / Chunk / Conversation / Message）+ Alembic 迁移
- sha256 去重、文件落盘、状态机（`pending → processing → ready / failed`）
- 全局异常处理器统一映射业务异常到 HTTP 状态码

### 演示界面

- Streamlit 界面：文档管理、问答（流式 + 多轮）、引用详情展示、会话新建/切换/历史回看
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

## 技术栈

Python 3.11 · uv · FastAPI · Pydantic · PyMuPDF · LangChain · Qdrant · SQLAlchemy 2 + Alembic · Streamlit · pytest · Ruff + mypy · GitHub Actions · Langfuse

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

- pytest：530+ passed（含阶段 9.1 新增 16 个、阶段 9.2 新增 111 个、阶段 9.3 新增 65 个、阶段 10.1 新增 25 个）
- ruff format --check：通过
- ruff check：通过
- mypy：CI 环境（Linux）通过；本机 Windows 因应用程序控制策略阻止 C 扩展加载无法运行

## 已知问题

1. **mypy 本机不可用**：uv 管理的独立 Python 的 C 扩展在 Windows 本机被应用程序控制策略阻止加载。CI 环境（Linux）使用系统 Python，不受影响。已创建 conda 环境 `rrag311`（Python 3.11.15）作为本地替代，但 mypy 仍受 `_sqlite3.dll` 阻塞无法运行，留待 CI 验证。
2. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。
3. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体不含中文字形，CI 环境也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理。
4. **本地 pytest 全量运行注意事项**：部分测试可能加载真实 Embedding 模型导致卡住，建议单独跑改动相关测试文件；API 路由测试需设置 `$env:RERANKER_ENABLED="false"; $env:QDRANT_ENABLED="false"` 避免 lifespan 加载真实 reranker/Qdrant。

## 后续可选方向

- 阶段 10.2 用户反馈闭环（点赞/点踩记录到 DB）
- 阶段 10.3 性能优化（Embedding 缓存 / 并发检索 / Qdrant 调优）
- 阶段 11.4 Docker Compose 一键部署
- 阶段 11.1 认证鉴权
- 表格感知切分与公式识别（阶段 12）
