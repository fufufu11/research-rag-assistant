# Current Status

## 当前版本

`v0.0.0`（阶段 0、1 已合并到 `main`；项目方向调整为直接使用 LangChain，文档已提交；阶段 2 文本切分器代码完成，待提交 PR）

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
  - 阶段 2 起为 `PageInfo` 增加 `text` 字段保存完整页面文本，供下游切分器使用
- `scripts/parse_pdf.py`：CLI 入口，退出码区分 4 种结果（成功 / 不存在 / 损坏 / 空 PDF）
- `tests/unit/test_pdf_parser.py`：5 条测试，覆盖合法 / 空 / 损坏 / 不存在 / preview 长度
- 新增依赖 `pymupdf>=1.28.0`（PROJECT_PLAN 第 5 节指定）

### 阶段 2：文本切分器（Issue #8，分支 `feat/chunker`，待提交 PR）

- `src/research_rag/chunker.py`：页内文本清洗与带重叠的 Chunk 切分
  - `Chunk`（page_number / chunk_index / content / char_count）
  - `ChunkerConfig`（chunk_size=500 / chunk_overlap=80 / min_chunk_chars=20，均可配置）
  - `clean_page_text(text)`：合并多余空白、统一换行，不过度清洗
  - `chunk_pages(pages, config) -> list[Chunk]`：使用 LangChain `RecursiveCharacterTextSplitter` 按页切分
  - 不跨页切分：每页独立调用 `split_text`
  - 过滤少于 `min_chunk_chars` 的片段（页眉/页脚/噪声）
  - `chunk_index` 文档内从 0 开始连续编号
- `tests/unit/test_chunker.py`：14 条测试，覆盖短文本/长文本/多页/不跨页/重叠/过滤/自定义配置/边界/clean_page_text
- 新增依赖 `langchain-text-splitters>=1.0`（LangChain 官方切分子包）
- 修改 `pdf_parser.py`：`PageInfo` 增加 `text` 字段（向后兼容，现有测试不受影响）

## 当前Issue与分支

- Issue #1（初始化Python项目与质量工具）：已关闭（PR #3 合并）
- Issue #2（配置GitHub Actions持续集成）：已关闭（PR #4 合并）
- Issue #5（feat: 实现按页PDF解析器）：已关闭（PR #6 合并，commit `e2056bc`）
  - 注：PROJECT_PLAN 第 15 节建议编号 #3，实际为 #5（PR #3/#4 占用编号 3/4，PR #6 占用编号 6）
- Issue #8（feat: 实现页内文本切分器）：进行中，分支 `feat/chunker`（本地，未推送）

## 正在处理的问题

无。阶段 2 代码与测试已完成，四项检查全部通过，等待用户确认后提交、推送并开 PR。

## 本地运行命令

```powershell
# 安装依赖（首次或修改 pyproject.toml 后）
uv sync --extra dev

# 四项检查（PROJECT_PLAN.md 第 13.4 节）
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest

# 运行 PDF 解析 CLI
uv run python scripts/parse_pdf.py <pdf_path>

# 安装 pre-commit 钩子（一次性）
uv run pre-commit install
```

## 测试状态

- pytest：21 passed（2 冒烟 + 5 PDF 解析 + 14 切分器）
- ruff format --check：通过
- ruff check：通过
- mypy：通过（`Success: no issues found in 3 source files`）

## 下一步最小任务

按 [PROJECT_PLAN.md 第 15 节](../PROJECT_PLAN.md#L736) Issue #5 与[阶段 3](../PROJECT_PLAN.md#L691)：

1. **提交并推送 `feat/chunker` 分支**，开 PR 关联 Issue #8（`Closes #8`）
2. **CI 通过后合并 PR**，切回 `main` 并 `git pull`
3. **进入阶段 3（Embedding 与向量检索）**：创建 Issue `feat: 基于LangChain实现Embedding适配器`
4. 新增依赖 `langchain` + `langchain-core`，使用 LangChain Embedding 接口 + `InMemoryVectorStore`
5. 实现 Top-K 检索与最小评测脚本

## 尚未提交的改动

`feat/chunker` 分支上的改动（均未提交）：

- 修改：`pyproject.toml`（添加 `langchain-text-splitters` 依赖）
- 修改：`uv.lock`（依赖解析结果）
- 修改：`src/research_rag/pdf_parser.py`（`PageInfo` 增加 `text` 字段）
- 新增：`src/research_rag/chunker.py`
- 新增：`tests/unit/test_chunker.py`
- 修改：`docs/STATUS.md`（本次更新）

## 已知问题

1. **mypy 增量缓存不可用**：uv 管理的独立 Python 的 `_sqlite3.dll` 在本机被应用程序控制策略阻止加载，已在 `pyproject.toml` 中用 `no_incremental = true` 规避。CI 环境使用 Linux 上的系统 Python，不会有此问题。
2. **uv 硬链接警告**：缓存与目标目录在不同文件系统，uv 回退为完整复制。不影响功能。可设置 `$env:UV_LINK_MODE="copy"` 静默警告。
3. **PyMuPDF 类型存根不完整**：`pymupdf.open` / `page.get_text` / `doc.close` 在 mypy strict 下报 `no-untyped-call`，已在调用处用 `# type: ignore[no-untyped-call]` 精确抑制。`warn_unused_ignores = true` 会在 PyMuPDF 修复存根后提醒清理。
4. **测试 PDF 用英文文本**：PyMuPDF 的 `insert_text` 默认字体（Helvetica）不含中文字形，CI 环境（Linux）也不一定有中文字体，故测试用英文。解析器本身对中文无特殊处理，中文提取能力由 PyMuPDF 保证。

## 最近学到的内容

- PyMuPDF 1.24+ 推荐用 `import pymupdf`（旧版本 `import fitz` 仍可用）
- `pymupdf.open()` / `doc.save()` / `doc.tobytes()` 都不允许 0 页文档，测试 0 页 PDF 需手工拼接最小合法 PDF 字节
- `dataclass(frozen=True)` 让数据类不可变，适合作为解析结果（避免被意外修改）
- mypy 的 `disallow_untyped_calls` 是调用方属性，对被调用方模块设置 override 无效；正确做法是在调用处用 `# type: ignore[no-untyped-call]` 精确抑制
- ruff 的 `TC003` 规则建议把只用于类型标注的导入放进 `TYPE_CHECKING` block（配合 `from __future__ import annotations`）
- GitHub 的 Issue/PR 共享编号空间，PR #3/#4 占用编号后，下一个 Issue 是 #5
- squash merge 后，基于旧分支的 PR 需要 `git rebase origin/main` 并 force-push 才能消除冲突
