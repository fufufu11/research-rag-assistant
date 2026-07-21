# Research RAG Assistant

科研文献可溯源智能问答系统：导入科研 PDF 后，仅基于已导入文档作答，并返回可核查的文档名、页码与原文片段。

> 项目状态：规划中。详细需求、阶段划分与开发规范见 [PROJECT_PLAN.md](./PROJECT_PLAN.md)。
> 当前进度与下一步见 [docs/STATUS.md](./docs/STATUS.md)。

## 功能规划

- 上传文本型 PDF，按页提取文本并保留页码
- 文本清洗、分段、Embedding 与向量检索
- 调用大模型生成仅基于上下文的答案
- 返回文档名、页码、原文片段作为引用
- 提供 FastAPI 接口与 Streamlit 演示界面
- 提供检索评测集与可复现报告

MVP 边界与可选扩展见 [PROJECT_PLAN.md 第 3 节](./PROJECT_PLAN.md#L65)。

## 技术栈

Python 3.11 · uv · FastAPI · Pydantic · PyMuPDF · Qdrant · SQLAlchemy 2 + Alembic · Streamlit · pytest · Ruff + mypy · Docker · GitHub Actions

> 详细选型理由见 [PROJECT_PLAN.md 第 5 节](./PROJECT_PLAN.md#L150)。

## 运行方式

> 需先安装 [uv](https://docs.astral.sh/uv/) 0.11+。当前进度与下一步见 [docs/STATUS.md](./docs/STATUS.md)。

```powershell
# 安装依赖（首次或修改 pyproject.toml 后）
uv sync --extra dev

# 四项质量检查
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest

# PDF 解析 CLI（阶段 1）
uv run python scripts/parse_pdf.py <pdf_path>
```

PDF 解析 CLI 会输出文件总页数，以及每页的页码、字符数和前 200 字预览。退出码：0 成功，2 文件不存在，3 文件损坏，4 空 PDF。

## 开发流程

采用主干开发：`main` 保持可运行，每个任务使用短生命周期分支，通过 Pull Request 合并。规范见 [PROJECT_PLAN.md 第 12 节](./PROJECT_PLAN.md#L506)。

## 安全与隐私

- `.env` 保存本地配置，不提交真实密钥
- 上传的文档与日志中不包含 API 密钥、Authorization 头或完整私人文档内容
- 文件路径限制在项目配置的上传目录内

## License

[MIT License](./LICENSE)
