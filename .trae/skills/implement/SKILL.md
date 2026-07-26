---
name: "implement"
description: "基于 spec Issue 或 tickets 实现工作。内置 tdd + code-review，完成后提 PR。"
---

# Implement（research-rag-assistant 适配版）

> 适配自 mattpocock/skills 的 implement，针对本项目 uv + ruff + mypy + pytest 工作流定制。

实现用户在 spec Issue 或 tickets 中描述的工作。

## 流程

1. **读 spec/ticket** — `issue_read` 读 GitHub Issue body，理解要实现什么、测试接缝在哪
2. **规划** — `TodoWrite` 拆解多步任务
3. **切分支** — `RunCommand` 从 main 切 `feat/` / `fix/` / `chore/` 前缀分支
4. **tdd 优先** — 调 `tdd` skill 在 pre-agreed seams 红绿重构
5. **定期 typecheck** — `uv run mypy src`（本机 Windows DLL 限制，可留 CI）
6. **定期单测** — `uv run pytest tests/unit/test_<file>.py -v`
7. **结尾全量** — `uv run pytest`（API 测试需 `$env:QDRANT_ENABLED="false"; $env:RERANKER_ENABLED="false"`）
8. **code-review** — 调 `code-review` skill 审查工作
9. **commit** — 提交到当前分支
10. **提 PR** — MCP `create_pull_request`，描述含 `Closes #<issue>`
11. **等 CI** — MCP `pull_request_read` method=`get_check_runs`，首次返回空等 30-40 秒
12. **merge** — MCP `merge_pull_request` squash merge
13. **更新文档** — `docs/ROADMAP.md` + `docs/STATUS.md`

## 本地命令速查

```powershell
# 切分支
git checkout main; git pull; git checkout -b feat/<name>

# 代码质量
uv run ruff format .
uv run ruff check .
uv run mypy src

# 测试（API 测试需先设环境变量）
$env:QDRANT_ENABLED="false"; $env:RERANKER_ENABLED="false"; uv run pytest

# 单文件测试
uv run pytest tests/unit/test_<file>.py -v

# 启动服务（验证）
docker start rrag-qdrant
Get-Content .env | Out-String | Invoke-Expression  # 加载环境变量（简化）
uv run uvicorn research_rag.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

## 工具使用约定

- 文件操作：`Read` / `Edit` / `Write`（不用 `cat` / `sed` / `echo`）
- 搜索：`Grep` / `Glob`（不用 `grep` / `find`）
- 终端：`RunCommand`（PowerShell，`;` 分隔，不支持 `&&`）
- Todo：`TodoWrite` 规划多步任务
- 子 agent：`Task` 工具 `subagent_type=search` 做探索

## MCP GitHub 调用

调用前先 `Read` schema：`c:\Users\25831\.trae-cn\mcps\s_research-rag-assistant-997aced1\solo_agent_lite\mcp_plugin_GitHub_github\tools\<tool>.json`

提 PR：

```python
run_mcp(
    server_name="mcp_plugin_GitHub_github",
    tool_name="create_pull_request",
    args={
        "owner": "fufufu11",
        "repo": "research-rag-assistant",
        "title": "<PR 标题>",
        "body": "Closes #<issue-number>\n\n<说明>",
        "head": "<feat-branch>",
        "base": "main"
    }
)
```

## 硬约束

- uv + Python 3.11；CI 三项全绿才算完成
- API key 严禁硬编码，用环境变量
- LF 行结尾（.ps1/.bat/.cmd 除外）
- 默认行为变更要保 backward compat，用环境变量开关（参考 `API_KEY_ENABLED` / `INPUT_VALIDATION_ENABLED` / `RATE_LIMIT_ENABLED` 风格）
- 不创建大量空模块；目录随功能增量添加
- commit message 用 here-string `@' ... '@`（PowerShell 不支持 bash heredoc）
- 不主动 push 到 remote，等用户明确要求
- 不主动 merge PR，等 CI 全绿后由用户决定（或按 handoff 约定自动）

## 完成后

- 更新 `docs/ROADMAP.md`（阶段状态）
- 更新 `docs/STATUS.md`（版本号 + 测试数）
- 可选：调 `handoff` skill 生成交接文档
