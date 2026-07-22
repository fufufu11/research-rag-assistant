# Current Status

## 当前版本

`v0.0.0`（阶段 0、1、2 已合并到 `main`；阶段 3 Embedding 与向量检索代码完成，待提交 PR）

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

### 阶段 3：Embedding 与向量检索（Issue #10，分支 `feat/embedding`，待提交 PR）

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

## 当前Issue与分支

- Issue #1（初始化Python项目与质量工具）：已关闭（PR #3 合并）
- Issue #2（配置GitHub Actions持续集成）：已关闭（PR #4 合并）
- Issue #5（feat: 实现按页PDF解析器）：已关闭（PR #6 合并）
- Issue #8（feat: 实现页内文本切分器）：已关闭（PR #9 合并）
- Issue #10（feat: 基于LangChain实现Embedding适配器）：进行中，分支 `feat/embedding`（本地，未推送）

## 正在处理的问题

无。阶段 3 代码与测试已完成，四项检查中 ruff format/check 和 pytest 通过，mypy 因本机 DLL 策略限制无法运行（CI 无此问题），等待用户确认后提交、推送并开 PR。

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

# 安装 pre-commit 钩子（一次性）
uv run pre-commit install
```

## 测试状态

- pytest：38 passed（2 冒烟 + 5 PDF 解析 + 14 切分器 + 17 Embedding）
- ruff format --check：通过
- ruff check：通过
- mypy：本机因 `librt` C 扩展被应用程序控制策略阻止无法运行，CI 环境（Linux）正常

## 下一步最小任务

按 [PROJECT_PLAN.md 阶段 4](../PROJECT_PLAN.md#L701)：

1. **提交并推送 `feat/embedding` 分支**，开 PR 关联 Issue #10（`Closes #10`）
2. **CI 通过后合并 PR**，切回 `main` 并 `git pull`
3. **进入阶段 4（大模型回答与可靠引用）**：创建 Issue，实现 OpenAI 兼容模型客户端、结构化答案、引用映射

## 尚未提交的改动

`feat/embedding` 分支上的改动（均未提交）：

- 修改：`pyproject.toml`（新增 langchain/langchain-core/langchain-huggingface/numpy 依赖 + embedding 可选 extra）
- 修改：`uv.lock`（依赖解析结果）
- 修改：`.env.example`（向量数据库注释改为 LangChain InMemoryVectorStore）
- 修改：`README.md`（新增评测脚本说明）
- 修改：`docs/STATUS.md`（本次更新）
- 新增：`src/research_rag/embedding.py`
- 新增：`tests/unit/test_embedding.py`
- 新增：`scripts/evaluate_retrieval.py`

## 已知问题

1. **mypy 增量缓存不可用**：uv 管理的独立 Python 的 `_sqlite3.dll` 在本机被应用程序控制策略阻止加载，已在 `pyproject.toml` 中用 `no_incremental = true` 规避。CI 环境使用 Linux 上的系统 Python，不会有此问题。
2. **mypy 完全无法启动（本机）**：mypy 新版本依赖 `librt` C 扩展，本机 Windows 应用程序控制策略阻止其加载。CI 环境（Linux）不受影响。本机可考虑用系统级 Python 安装 mypy 作为临时方案。
3. **uv 硬链接警告**：缓存与目标目录在不同文件系统，uv 回退为完整复制。不影响功能。可设置 `$env:UV_LINK_MODE="copy"` 静默警告。
4. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。
5. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体不含中文字形，CI 环境（Linux）也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理。

## 最近学到的内容

- LangChain 的 `InMemoryVectorStore` 内部用 NumPy 计算余弦相似度，但 `langchain-core` 不强制依赖 NumPy，使用时需自行安装
- `HuggingFaceEmbeddings.__init__` 惰性导入 `sentence_transformers`，未装则抛 `ImportError`，适合用可选 extra 隔离重型依赖
- `InMemoryVectorStore.similarity_search_with_score` 返回余弦相似度（分数越高越相关），已按相关度降序排列
- 依赖注入模式让 `index_chunks`/`retrieve` 与真实模型解耦，测试用 `FakeEmbeddings`（字符袋向量）即可验证完整检索流程
- `sys.modules["module"] = None` 可在测试中模拟模块未安装，`import module` 会抛 `ImportError`
- `dataclass(frozen=True)` 在赋值时抛 `AttributeError`，可用于测试不可变性
- `from __future__ import annotations` 只影响函数/类签名注解，不影响函数体内局部变量注解（后者运行时不求值）
- mypy 新版本引入 `librt` C 扩展用于 IPC，在 Windows 应用程序控制策略下可能被阻止
