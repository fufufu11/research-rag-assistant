# ADR 0004: docker secrets 用代码层 get_secret helper（方案 A）

## 状态

Accepted（2026-07-26）

## 背景

阶段 11.6 生产级安全加固需要把 8 个密钥（`POSTGRES_PASSWORD` / `LLM_API_KEY` / `JUDGE_LLM_API_KEY` / `API_KEYS` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `DASHSCOPE_API_KEY` / `JINA_API_KEY`）从环境变量迁移到 docker secrets，让生产环境 secrets 不进进程环境变量（`docker inspect` 不可见）。

两种实现路径：

- **方案 A**：代码层引入统一 `get_secret(name) -> str | None` helper，替换所有 `os.environ.get(...)` 密钥读取点。helper 先读 `{name}_FILE` 环境变量指向的文件内容，再 fallback `os.environ.get(name)`。保持向后兼容（开发/CI 仍可用环境变量）。
- **方案 B**：只改 `docker-compose.prod.yml`，`entrypoint.sh` 把 `/run/secrets/<name>` 文件内容读到环境变量后 `exec uvicorn`。代码不动。

当前密钥读取点散布在 `observability.py`（`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`）/ `api/dependencies.py`（`LLM_API_KEY`）/ `answer_evaluation.py`（`JUDGE_LLM_API_KEY`）/ `api/auth.py`（`API_KEYS`）/ `embedding.py`（`DASHSCOPE_API_KEY` / `JINA_API_KEY`），均为 `os.environ.get(...)`，无统一 helper，无 `_FILE` 后缀支持。postgres 官方镜像原生支持 `POSTGRES_PASSWORD_FILE`。

## 决策

选**方案 A**：新增 `src/research_rag/secrets.py` 统一 `get_secret(name) -> str | None` helper，替换 8 个密钥读取点，保持向后兼容。

- **`get_secret(name)` 行为**：
  1. 先读 `{name}_FILE` 环境变量；若存在且文件可读，返回文件内容（`strip()` 处理尾部换行）
  2. 若 `{name}_FILE` 未设置或文件读取失败，fallback 到 `os.environ.get(name)`
  3. 返回 `str | None`
- **postgres 用 `POSTGRES_PASSWORD_FILE`**：官方镜像原生支持，无需改代码，只改 compose。
- **向后兼容**：开发与 CI 用环境变量不受影响；生产用 `_FILE` 后缀挂载 docker secrets。

## 后果

- **正面**：
  - secrets 不进环境变量，`docker inspect` 看不到密钥，是真正的密钥管理升级（方案 B 的 secrets 仍出现在环境变量中，`docker inspect` 可见，仅是"文件来源"变化）。
  - helper 统一读取入口，未来扩展（如支持 Vault `vault://` 前缀）只改一处，调用点不变。
  - 向后兼容：开发与 CI 用环境变量不受影响，生产用 `_FILE`。
- **负面 / 已知局限**：
  - 侵入代码：需替换 8 个读取点（`observability.py` / `api/dependencies.py` / `answer_evaluation.py` / `api/auth.py` / `embedding.py`）。
  - postgres 官方镜像用 `POSTGRES_PASSWORD_FILE`（原生支持，无需改代码，但 compose 配置需调整）。
- **风险**：
  - fallback 兼容性需测试覆盖：`_FILE` 优先 / fallback env / 文件不存在 / 文件为空四分支，任一分支有 bug 影响所有密钥读取。
  - 若 helper 实现有 bug，影响所有密钥读取——需单元测试严格覆盖。
- **未来演进**：
  - 若引入 Vault，`get_secret` 可扩展支持 `vault://` 前缀，调用点不变。
  - 若未来切换到 pydantic-settings BaseSettings，`get_secret` 可作为 `SecretsSettingsSource` 的实现基础。
