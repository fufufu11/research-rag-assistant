# Current Status

## 当前版本

`v0.0.1`（阶段 0：仓库与工程基础，PR #3 和 PR #4 已开，等待合并）

## 已完成

### 仓库基础
- 仓库已创建：`research-rag-assistant`
- 添加 `.gitignore`（完整 Python + 安全规则，忽略 `.env`、`data/`、密钥）
- 添加 `LICENSE`（MIT）
- 添加 `PROJECT_PLAN.md`（项目章程 v1.0）
- 添加 `README.md`（骨架）
- 添加 `docs/STATUS.md`

### 阶段 0：Python 工程与质量工具（Issue #1 内容，未提交 PR）
- 安装 `uv 0.11.30`（winget）
- `pyproject.toml`：uv + Hatchling + Python 3.11 + Ruff + mypy + pytest 配置
- `.python-version`：锁定 3.11（uv 自动下载 Python 3.11.15 独立运行时）
- `src/research_rag/__init__.py`：最小包，仅暴露 `__version__`
- `tests/test_smoke.py`：冒烟测试 2 条
- `.env.example`：环境变量模板（含注释说明启用阶段）
- `.pre-commit-config.yaml`：pre-commit 钩子（ruff + mypy + 安全检查）
- `uv.lock`：依赖锁文件

### 质量工具验证（全部通过）
- `uv run ruff format --check .` → 2 files already formatted
- `uv run ruff check .` → All checks passed!
- `uv run mypy src` → Success: no issues found in 1 source file
- `uv run pytest` → 2 passed

### GitHub Issues 与 PR
- Issue #1：`chore: 初始化Python项目与质量工具`（已开，PR #3 关联，等待合并）
- Issue #2：`配置GitHub Actions持续集成`（已开，PR #4 关联，等待合并）
- PR #3：https://github.com/fufufu11/research-rag-assistant/pull/3（`chore/project-bootstrap` → `main`，Closes #1）
- PR #4：https://github.com/fufufu11/research-rag-assistant/pull/4（`chore/github-actions` → `main`，Closes #2，包含 PR #3 全部内容 + CI 配置）

### 阶段 0：CI 配置（Issue #2 内容）
- `.github/workflows/ci.yml`：三个作业（Lint / Type Check / Test），PR 和 push 到 main 时触发
- `.gitattributes`：统一 LF 换行符，Windows 脚本保留 CRLF，二进制文件不做转换

## 当前Issue与分支

- Issue #1 已创建（https://github.com/fufufu11/research-rag-assistant/issues/1）
- Issue #2 已创建（https://github.com/fufufu11/research-rag-assistant/issues/2）
- 分支 `chore/project-bootstrap` 已推送，PR #3 已开
- 分支 `chore/github-actions` 已推送，PR #4 已开
- **当前所在分支**：`chore/github-actions`（含未提交的 STATUS.md 微调）
- main 分支尚未合并任何 PR

## 正在处理的问题

无。阶段 0 全部代码与 CI 配置已完成，等待用户在 GitHub 网页合并 PR #3 和 PR #4。

## 本地运行命令

```powershell
# 安装依赖（首次或修改 pyproject.toml 后）
uv sync --extra dev

# 四项检查（PROJECT_PLAN.md 第 13.4 节）
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest

# 安装 pre-commit 钩子（一次性）
uv run pre-commit install

# 手动运行 pre-commit
uv run pre-commit run --all-files
```

## 测试状态

- pytest：2 passed（`tests/test_smoke.py`）
- ruff：通过
- mypy：通过（关闭增量检查，见下文已知问题）

## 下一步最小任务

按 [PROJECT_PLAN.md 第 16 节](../PROJECT_PLAN.md#L764) Day 7 与 [第 15 节](../PROJECT_PLAN.md#L736) Issue #3：

1. **合并 PR #3 和 PR #4 到 `main`**（在 GitHub 网页操作）
2. **切回 `main` 并 pull**：`git checkout main; git pull`
3. **进入阶段 1（PDF 解析）**：创建 Issue #3 `实现按页PDF解析器`（里程碑 `v0.1 CLI`）
4. **从 `main` 创建分支** `feat/pdf-parser`
5. **学习 PyMuPDF 最基本用法**（[第 5 节](../PROJECT_PLAN.md#L150)）
6. **实现按页提取文本**，输出每页页码、字符数和前 200 字（[阶段 1 验收](../PROJECT_PLAN.md#L670)）
7. **处理异常**：文件不存在、文件损坏、空 PDF（[阶段 1 验收](../PROJECT_PLAN.md#L676)）
8. **添加单元测试**：合法 PDF、空 PDF、损坏文件（[第 13.1 节](../PROJECT_PLAN.md#L598)）

> 注意：阶段 0 仅完成工程基础，不写任何 RAG 业务代码（[PROJECT_PLAN.md 第 22 节](../PROJECT_PLAN.md#L979)）。PDF 解析是阶段 1 的第一个业务模块。

## 尚未提交的改动

`chore/github-actions` 分支上有一处未提交的 `docs/STATUS.md` 微调（更新 PR #4 状态为"已开"）。

**建议下一个 AI 窗口第一步**：将此微调追加到上一个 commit 并 force-push，或新起一个 `docs:` commit 推送。

## 已知问题

1. **mypy 增量缓存不可用**：uv 管理的独立 Python 的 `_sqlite3.dll` 在本机被应用程序控制策略阻止加载，已在 `pyproject.toml` 中用 `no_incremental = true` 规避。若后续迁移到系统级 Python，可移除此项。
2. **uv 硬链接警告**：缓存与目标目录在不同文件系统，uv 回退为完整复制。性能略降但不影响功能。可设置 `$env:UV_LINK_MODE="copy"` 静默警告。
3. **GitHub Actions 尚未配置**：阶段 0 的 CI 部分由 Issue #2 完成。

## 最近学到的内容

- `uv` 是 Astral 出品的 Python 包/运行时管理器，比 pip+venv 快，可托管多个 Python 版本
- `src/` 布局比扁平布局更安全（强制安装后才可导入，避免测试误导入本地源码）
- `requires-python = ">=3.11"` 允许 3.13，需配合 `.python-version` 精确锁定
- Ruff 的 RUF001/002/003 对中文项目会误报全角标点，需在配置中忽略
- mypy 默认用 sqlite 做增量缓存，运行时若 sqlite3 不可用会内部错误
