"""部署配置文件测试（阶段 11.5 / 11.6）。

验证 CI/CD 相关的 YAML 配置文件语法正确且结构符合预期：
- ``.github/workflows/deploy.yml``：deploy workflow 存在，含 build-and-push / deploy job
- ``docker-compose.prod.yml``：生产覆盖文件存在，api 服务引用 GHCR 镜像
- ``docker-compose.yml``：postgres 服务支持 ``POSTGRES_PASSWORD_FILE``（阶段 11.6 切片 C）

这些配置文件无法用 pytest 直接测试行为（GitHub Actions 在云端运行，
docker compose 需要真实 Docker），但验证 YAML 语法与关键结构能在本地
提前发现拼写错误、缩进错误、关键字段缺失等问题，避免推到远端后 CI 才报错。

PyYAML 在 CI 的 ``uv sync --extra dev`` 中可用（pyproject.toml dev extra 显式声明）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(path: Path) -> dict:
    """加载 YAML 文件为 dict，文件不存在时 fail。"""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{path} 顶层结构应为 dict，实际 {type(data)}"
    return data


# ---------------------------------------------------------------------------
# GitHub Actions deploy.yml workflow
# ---------------------------------------------------------------------------


class TestDeployWorkflow:
    """``.github/workflows/deploy.yml`` 配置测试。"""

    @pytest.fixture(scope="class")
    def workflow(self) -> dict:
        return _load_yaml(REPO_ROOT / ".github" / "workflows" / "deploy.yml")

    def test_workflow_name(self, workflow: dict) -> None:
        assert workflow["name"] == "Deploy"

    def test_workflow_triggers(self, workflow: dict) -> None:
        # PyYAML 1.1 把 on/off 解析为 True/False，GitHub Actions 的 'on:' 键
        # 会被 safe_load 解析为 True 键
        on = workflow.get("on") if "on" in workflow else workflow.get(True)
        assert on is not None, "workflow 缺少 'on:' 触发器配置"
        # workflow_run：CI 成功后自动触发
        assert on["workflow_run"]["workflows"] == ["CI"]
        assert on["workflow_run"]["types"] == ["completed"]
        assert on["workflow_run"]["branches"] == ["main"]
        # workflow_dispatch：手动触发
        assert "workflow_dispatch" in on

    def test_build_and_push_job_exists(self, workflow: dict) -> None:
        jobs = workflow["jobs"]
        assert "build-and-push" in jobs

    def test_build_and_push_gated_on_ci_success(self, workflow: dict) -> None:
        """build-and-push 仅在 CI 成功 或 手动触发 时运行。"""
        if_cond = workflow["jobs"]["build-and-push"]["if"]
        assert "workflow_run" in str(if_cond)
        assert "success" in str(if_cond)

    def test_build_and_push_permissions(self, workflow: dict) -> None:
        """GHCR 推送需要 packages:write 权限。"""
        perms = workflow["jobs"]["build-and-push"]["permissions"]
        assert perms["packages"] == "write"
        assert perms["contents"] == "read"

    def test_build_and_push_has_required_steps(self, workflow: dict) -> None:
        steps = workflow["jobs"]["build-and-push"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        # 必须有 checkout / login / build-push 三个核心步骤
        assert any("Checkout" in n for n in step_names)
        assert any("Login" in n for n in step_names)
        assert any("Build" in n for n in step_names)

    def test_build_and_push_uses_ghcr_registry(self, workflow: dict) -> None:
        """镜像必须推到 ghcr.io，且镜像名基于 github.repository（owner/repo）。"""
        steps = workflow["jobs"]["build-and-push"]["steps"]
        # 找到 metadata step（用 docker/metadata-action）
        meta_step = next(s for s in steps if "uses" in s and "metadata-action" in s["uses"])
        images = meta_step["with"]["images"]
        assert "ghcr.io" in images
        assert "${{ github.repository }}" in images

    def test_deploy_job_exists(self, workflow: dict) -> None:
        assert "deploy" in workflow["jobs"]

    def test_deploy_depends_on_build(self, workflow: dict) -> None:
        """deploy job 必须 needs build-and-push（先构建后部署）。"""
        deploy = workflow["jobs"]["deploy"]
        assert deploy["needs"] == "build-and-push"

    def test_deploy_gated_by_variable(self, workflow: dict) -> None:
        """deploy job 必须用 ENABLE_SSH_DEPLOY 变量门控（未配置时跳过）。"""
        if_cond = str(workflow["jobs"]["deploy"]["if"])
        assert "ENABLE_SSH_DEPLOY" in if_cond
        assert "true" in if_cond

    def test_deploy_uses_ssh_action(self, workflow: dict) -> None:
        """deploy job 用 appleboy/ssh-action 执行 SSH 部署。"""
        steps = workflow["jobs"]["deploy"]["steps"]
        ssh_step = next(s for s in steps if "uses" in s and "ssh-action" in s["uses"])
        with_ = ssh_step["with"]
        # 必须引用 SSH secrets
        assert with_["host"] == "${{ secrets.SSH_HOST }}"
        assert with_["username"] == "${{ secrets.SSH_USER }}"
        assert with_["key"] == "${{ secrets.SSH_PRIVATE_KEY }}"

    def test_deploy_script_pulls_and_restarts(self, workflow: dict) -> None:
        """deploy 脚本必须 pull 镜像 + up -d + 健康检查。"""
        steps = workflow["jobs"]["deploy"]["steps"]
        ssh_step = next(s for s in steps if "uses" in s and "ssh-action" in s["uses"])
        script = ssh_step["with"]["script"]
        assert "docker compose" in script
        assert "pull" in script
        assert "up -d" in script
        # 健康检查（curl + sleep 循环）
        assert "curl" in script
        assert "sleep" in script

    def test_concurrency_no_cancel(self, workflow: dict) -> None:
        """部署不可中途取消（避免镜像推一半/容器拉一半造成服务不一致）。"""
        concurrency = workflow["concurrency"]
        assert concurrency["cancel-in-progress"] is False


# ---------------------------------------------------------------------------
# docker-compose.prod.yml 生产覆盖文件
# ---------------------------------------------------------------------------


class TestProdCompose:
    """``docker-compose.prod.yml`` 生产覆盖文件测试。"""

    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return _load_yaml(REPO_ROOT / "docker-compose.prod.yml")

    def test_api_service_uses_ghcr_image(self, compose: dict) -> None:
        api = compose["services"]["api"]
        image = api["image"]
        assert "ghcr.io" in image
        assert "research-rag-assistant" in image

    def test_api_image_supports_tag_override(self, compose: dict) -> None:
        """镜像 tag 应可通过 IMAGE_TAG 环境变量切换（回滚用）。"""
        image = compose["services"]["api"]["image"]
        # ${IMAGE_TAG:-latest} 模式：支持环境变量覆盖，默认 latest
        assert "IMAGE_TAG" in image
        assert "latest" in image


# ---------------------------------------------------------------------------
# docker-compose.yml postgres 密钥文件支持（阶段 11.6 切片 C #99）
# ---------------------------------------------------------------------------


class TestPostgresPasswordFile:
    """``docker-compose.yml`` postgres 服务支持 ``POSTGRES_PASSWORD_FILE``。

    Postgres 官方镜像原生支持 ``POSTGRES_PASSWORD_FILE`` 环境变量：设置后
    容器启动时读取文件内容作为密码（优先于 ``POSTGRES_PASSWORD``），用于
    docker secrets 场景（生产环境把密码以文件形式挂载到 ``/run/secrets/``）。
    本测试验证 compose 文件显式传递该变量到 postgres 容器，使切片 D
    （#101 docker secrets 配置）能直接通过设置 ``POSTGRES_PASSWORD_FILE``
    环境变量启用文件密钥，无需再改 compose。
    """

    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return _load_yaml(REPO_ROOT / "docker-compose.yml")

    def test_postgres_service_exists(self, compose: dict) -> None:
        assert "postgres" in compose["services"]

    def test_postgres_password_file_var_present(self, compose: dict) -> None:
        """postgres environment 必须显式声明 ``POSTGRES_PASSWORD_FILE``。

        使用 ``${POSTGRES_PASSWORD_FILE:-}`` 模式：未设置环境变量时为空字符串
        （Postgres 官方镜像遇空值忽略 ``_FILE`` 后缀，回退到 ``POSTGRES_PASSWORD``），
        保持开发/CI 向后兼容；生产环境设置该变量为 docker secrets 文件路径即可启用。
        """
        env = compose["services"]["postgres"]["environment"]
        assert "POSTGRES_PASSWORD_FILE" in env, (
            "postgres.environment 缺少 POSTGRES_PASSWORD_FILE：阶段 11.6 切片 C 要求"
            "显式声明以支持 docker secrets 文件挂载（生产），未设置时回退到"
            "POSTGRES_PASSWORD 环境变量（开发/CI）"
        )
        # ${POSTGRES_PASSWORD_FILE:-} 模式：空时回退到空字符串
        value = env["POSTGRES_PASSWORD_FILE"]
        assert "POSTGRES_PASSWORD_FILE" in value

    def test_postgres_password_var_still_present(self, compose: dict) -> None:
        """``POSTGRES_PASSWORD`` 必须保留（开发/CI 不挂载 secrets 时使用）。"""
        env = compose["services"]["postgres"]["environment"]
        assert "POSTGRES_PASSWORD" in env, (
            "POSTGRES_PASSWORD 应保留作为开发/CI 默认路径；生产环境设置"
            "POSTGRES_PASSWORD_FILE 时由 Postgres 官方镜像优先使用文件内容"
        )


# ---------------------------------------------------------------------------
# docker-compose.prod.yml docker secrets 配置（阶段 11.6 切片 D #101）
# ---------------------------------------------------------------------------

# 8 个 secrets 名字与对应环境变量后缀的映射（名字以下划线分隔，docker secrets 约定）。
# Postgres 官方镜像用 POSTGRES_PASSWORD_FILE，其余 7 个由应用层 get_secret helper 读取。
EXPECTED_PROD_SECRETS: list[str] = [
    "postgres_password",
    "llm_api_key",
    "judge_llm_api_key",
    "api_keys",
    "langfuse_public_key",
    "langfuse_secret_key",
    "dashscope_api_key",
    "jina_api_key",
]


class TestProdComposeDockerSecrets:
    """``docker-compose.prod.yml`` docker secrets 配置测试（阶段 11.6 切片 D #101）。

    生产环境用 docker compose ``secrets:`` 顶级键 + 各服务 ``secrets:`` 引用，
    把 8 个密钥以文件形式挂载到 ``/run/secrets/<name>``，避免 secrets 进程环境变量
    （``docker inspect`` 不可见）。决策见 ADR 0004。
    """

    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return _load_yaml(REPO_ROOT / "docker-compose.prod.yml")

    def test_top_level_secrets_block_exists(self, compose: dict) -> None:
        """顶层必须有 ``secrets:`` 块声明所有 docker secrets。"""
        assert "secrets" in compose, (
            "docker-compose.prod.yml 缺少顶层 'secrets:' 块：阶段 11.6 切片 D 要求"
            "生产环境用 docker secrets 把密钥以文件形式挂载到 /run/secrets/<name>"
        )

    def test_all_eight_secrets_defined_with_file(self, compose: dict) -> None:
        """8 个 secrets 必须全部定义，且每个含 ``file:`` 指向宿主机文件路径。"""
        secrets = compose.get("secrets", {})
        missing = [name for name in EXPECTED_PROD_SECRETS if name not in secrets]
        assert not missing, f"docker-compose.prod.yml secrets 缺少: {missing}"

        for name in EXPECTED_PROD_SECRETS:
            secret_def = secrets[name]
            assert isinstance(secret_def, dict), (
                f"secrets.{name} 应为 dict 含 'file:' 字段，实际 {type(secret_def)}"
            )
            assert "file" in secret_def, (
                f"secrets.{name} 缺少 'file:' 字段：docker secrets 必须指定宿主机文件路径"
            )
            file_value = secret_def["file"]
            assert isinstance(file_value, str) and file_value, (
                f"secrets.{name}.file 必须是非空字符串，实际 {file_value!r}"
            )

    def test_api_service_references_seven_secrets(self, compose: dict) -> None:
        """api 服务必须引用 7 个 secrets（除 postgres_password）挂载到 /run/secrets/。

        postgres_password 由 postgres 服务独占（api 不直连 postgres 卷读取密码，
        而是通过 DATABASE_URL 拼装，由 entrypoint 读 POSTGRES_PASSWORD_FILE 内容）。
        """
        api_secrets = compose["services"]["api"].get("secrets", [])
        # docker compose secrets: 可为列表（短语法）或 dict（长语法），统一提取 name
        if isinstance(api_secrets, dict):
            api_secret_names = list(api_secrets.keys())
        else:
            api_secret_names = [
                s if isinstance(s, str) else s.get("source", "") for s in api_secrets
            ]

        expected_api_secrets = [
            name for name in EXPECTED_PROD_SECRETS if name != "postgres_password"
        ]
        missing = [n for n in expected_api_secrets if n not in api_secret_names]
        assert not missing, (
            f"api 服务 secrets 缺少: {missing}（应引用除 postgres_password 外的 7 个 secrets）"
        )

    def test_postgres_service_references_password_secret(self, compose: dict) -> None:
        """postgres 服务必须引用 postgres_password secret 挂载到 /run/secrets/。"""
        pg_secrets = compose["services"].get("postgres", {}).get("secrets", [])
        if isinstance(pg_secrets, dict):
            pg_secret_names = list(pg_secrets.keys())
        else:
            pg_secret_names = [s if isinstance(s, str) else s.get("source", "") for s in pg_secrets]
        assert "postgres_password" in pg_secret_names, (
            "postgres 服务 secrets 必须引用 postgres_password（用于挂载到"
            "/run/secrets/postgres_password 供 POSTGRES_PASSWORD_FILE 读取）"
        )

    def test_postgres_password_file_points_to_secret_mount(self, compose: dict) -> None:
        """postgres.environment.POSTGRES_PASSWORD_FILE 必须指向 /run/secrets/postgres_password。

        覆盖 base docker-compose.yml 的 ``${POSTGRES_PASSWORD_FILE:-}``（开发为空），
        生产环境固定指向 docker secrets 挂载路径，由 Postgres 官方镜像读取文件内容。
        """
        pg_env = compose["services"]["postgres"]["environment"]
        assert pg_env.get("POSTGRES_PASSWORD_FILE") == "/run/secrets/postgres_password", (
            "生产环境 POSTGRES_PASSWORD_FILE 应固定为 /run/secrets/postgres_password"
            f"（覆盖 base compose 的 ${{POSTGRES_PASSWORD_FILE:-}}），实际 {pg_env.get('POSTGRES_PASSWORD_FILE')!r}"
        )

    def test_api_environment_has_seven_file_vars(self, compose: dict) -> None:
        """api.environment 必须含 7 个 ``{NAME}_FILE`` 指向 ``/run/secrets/<secret_name>``。

        应用层 ``get_secret`` helper（src/research_rag/secrets.py）优先读 ``{NAME}_FILE``
        环境变量指向的文件内容；生产环境必须把这 7 个 ``_FILE`` 变量指向 docker secrets
        挂载路径（``/run/secrets/<secret_name>``），secrets 才能被 helper 读到。
        """
        api_env = compose["services"]["api"].get("environment", {})
        # 7 个 secrets（除 postgres_password）对应的环境变量名 → 挂载路径
        # 命名约定：环境变量名 = secret_name 大写；挂载路径 = /run/secrets/<secret_name>
        expected_file_vars = {
            "LLM_API_KEY_FILE": "/run/secrets/llm_api_key",
            "JUDGE_LLM_API_KEY_FILE": "/run/secrets/judge_llm_api_key",
            "API_KEYS_FILE": "/run/secrets/api_keys",
            "LANGFUSE_PUBLIC_KEY_FILE": "/run/secrets/langfuse_public_key",
            "LANGFUSE_SECRET_KEY_FILE": "/run/secrets/langfuse_secret_key",
            "DASHSCOPE_API_KEY_FILE": "/run/secrets/dashscope_api_key",
            "JINA_API_KEY_FILE": "/run/secrets/jina_api_key",
        }
        missing = [k for k in expected_file_vars if k not in api_env]
        assert not missing, f"api.environment 缺少 _FILE 变量: {missing}"

        for var, expected_path in expected_file_vars.items():
            assert api_env[var] == expected_path, (
                f"api.environment.{var} 应为 {expected_path!r}，实际 {api_env[var]!r}"
            )

    def test_service_secret_refs_declared_at_top_level(self, compose: dict) -> None:
        """所有服务 ``secrets:`` 引用的 secret 名必须在顶级 ``secrets:`` 块中声明。

        防止拼写错误：若服务引用了未声明的 secret，docker compose 启动时会报错
        ``secret not found``；本测试在 CI 阶段提前捕获此类配置漂移。
        """
        top_level_secrets = set(compose.get("secrets", {}).keys())
        assert top_level_secrets, "顶级 secrets: 块应为非空（前序测试已验证）"

        for service_name, service_def in compose["services"].items():
            service_secrets = service_def.get("secrets", [])
            if isinstance(service_secrets, dict):
                ref_names = set(service_secrets.keys())
            else:
                ref_names = {
                    s if isinstance(s, str) else s.get("source", "") for s in service_secrets
                }
            undeclared = ref_names - top_level_secrets
            assert not undeclared, (
                f"服务 {service_name} 引用了未在顶级 secrets: 声明的 secret: {undeclared}"
            )

    def test_api_env_file_paths_match_declared_secrets(self, compose: dict) -> None:
        """api.environment 的 ``{NAME}_FILE`` 路径必须与 ``api.secrets`` 引用一致。

        防止路径拼写错误：如 ``LLM_API_KEY_FILE: /run/secrets/llm_apikey``（少下划线）
        会导致 ``get_secret`` 读到空文件（secret 挂载在 ``/run/secrets/llm_api_key``）。
        """
        api_secrets = compose["services"]["api"].get("secrets", [])
        if isinstance(api_secrets, dict):
            api_secret_names = set(api_secrets.keys())
        else:
            api_secret_names = {
                s if isinstance(s, str) else s.get("source", "") for s in api_secrets
            }

        api_env = compose["services"]["api"].get("environment", {})
        file_vars = {k: v for k, v in api_env.items() if k.endswith("_FILE")}
        for var, path in file_vars.items():
            # 路径形如 /run/secrets/<secret_name>
            assert path.startswith("/run/secrets/"), (
                f"api.environment.{var} 路径应以 /run/secrets/ 开头，实际 {path!r}"
            )
            secret_name = path.removeprefix("/run/secrets/")
            assert secret_name in api_secret_names, (
                f"api.environment.{var} 指向 /run/secrets/{secret_name}，"
                f"但 api.secrets 未引用该 secret（拼写错误？）"
            )


class TestDockerSecretsEnvExample:
    """``.env.docker.secrets.example`` 示例文件测试（阶段 11.6 切片 D #101）。

    生产环境 secrets 文件路径通过 ``.env.docker.secrets`` 文件注入 docker compose
    的 ``${VAR}_FILE`` 引用（指向宿主机 secrets 文件路径）。example 文件提供占位
    模板，运维复制为 ``.env.docker.secrets`` 后填入真实路径。
    """

    @pytest.fixture(scope="class")
    def example_text(self) -> str:
        path = REPO_ROOT / ".env.docker.secrets.example"
        assert path.exists(), (
            ".env.docker.secrets.example 不存在：阶段 11.6 切片 D 要求提供"
            "8 个 secrets 文件路径占位的示例文件供运维参考"
        )
        return path.read_text(encoding="utf-8")

    def test_example_contains_all_eight_secret_file_vars(self, example_text: str) -> None:
        """示例文件必须含 8 个 ``{NAME}_FILE`` 变量占位。"""
        expected_vars = [
            "POSTGRES_PASSWORD_FILE",
            "LLM_API_KEY_FILE",
            "JUDGE_LLM_API_KEY_FILE",
            "API_KEYS_FILE",
            "LANGFUSE_PUBLIC_KEY_FILE",
            "LANGFUSE_SECRET_KEY_FILE",
            "DASHSCOPE_API_KEY_FILE",
            "JINA_API_KEY_FILE",
        ]
        missing = [v for v in expected_vars if v not in example_text]
        assert not missing, (
            f".env.docker.secrets.example 缺少变量: {missing}（应含 8 个 _FILE 变量占位）"
        )

    def test_example_uses_placeholder_paths(self, example_text: str) -> None:
        """每个 ``_FILE`` 变量应赋值为占位路径（非空），引导运维填入真实宿主机路径。"""
        # 简单校验：每个 _FILE 变量行应形如 VAR=/some/path（不能只声明变量名不赋值）
        lines = [
            line.strip()
            for line in example_text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        file_var_lines = [line for line in lines if "_FILE=" in line]
        assert len(file_var_lines) >= 8, (
            f".env.docker.secrets.example 应含至少 8 行 _FILE 赋值，实际 {len(file_var_lines)} 行"
        )
        for line in file_var_lines:
            # 形如 VAR=/path/to/file（= 后必须紧跟非空路径）
            _, _, value = line.partition("=")
            assert value.strip(), f".env.docker.secrets.example 行 {line!r} 的 _FILE 变量未赋值"
