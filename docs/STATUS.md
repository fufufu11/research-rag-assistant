# Current Status

## 当前版本

`v0.0.1`（阶段 0：仓库与工程基础已完成，尚未提交 PR）

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

## 当前Issue与分支

- 尚未在 GitHub 创建 Issue（建议立即创建 Issue #1：`chore: 初始化Python项目与质量工具`）
- 尚未创建功能分支（建议 `chore/project-bootstrap`）
- 当前改动均在 `main` 工作区，尚未提交

## 正在处理的问题

无。等待用户确认是否提交代码 + 创建 GitHub Issue/分支。

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

按 [PROJECT_PLAN.md 第 16 节](../PROJECT_PLAN.md#L764) Day 4-7 与 [第 15 节](../PROJECT_PLAN.md#L736) Issue #2：

1. **提交当前改动到 `main`**（或先创建分支 `chore/project-bootstrap` 再提交）
2. **创建 GitHub Issue #2**：`配置GitHub Actions持续集成`（里程碑 `v0.1 CLI`）
3. **创建 `.github/workflows/ci.yml`**：在 PR 上自动执行 ruff format --check、ruff check、mypy、pytest
4. **提交 PR → CI 通过 → 合并到 `main`**
5. **进入阶段 1（PDF 解析）**：创建 Issue #3 `实现按页PDF解析器`，开始 Day 5 任务

> 注意：阶段 0 仅完成工程基础，不写任何 RAG 业务代码（[PROJECT_PLAN.md 第 22 节](../PROJECT_PLAN.md#L979)）。

## 尚未提交的改动

相对 `main` 的未提交文件（`git status` 待用户执行确认）：

- 新增：`README.md`、`docs/STATUS.md`、`pyproject.toml`、`uv.lock`、`.python-version`、`.env.example`、`.pre-commit-config.yaml`
- 新增：`src/research_rag/__init__.py`、`tests/test_smoke.py`
- 修改：`.gitignore`（原为空，已补全）
- 自动生成：`.venv/`（已被 .gitignore 忽略）

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
