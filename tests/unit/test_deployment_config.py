"""部署配置文件测试（阶段 11.5 / 11.6）。

验证 CI/CD 相关的 YAML 配置文件语法正确且结构符合预期：
- ``.github/workflows/deploy.yml``：deploy workflow 存在，含 build-and-push / deploy job
- ``docker-compose.prod.yml``：生产覆盖文件存在，api 服务引用 GHCR 镜像
- ``docker-compose.yml``：postgres 服务支持 ``POSTGRES_PASSWORD_FILE``（阶段 11.6 切片 C）
- ``nginx/nginx.conf``：nginx 反代 api + Let's Encrypt webroot + HTTP→HTTPS 重定向（切片 E #100）
- ``docker/nginx/entrypoint.sh`` / ``docker/certbot/entrypoint.sh``：证书首次签发 + 续期 cron（切片 E #100）

这些配置文件无法用 pytest 直接测试行为（GitHub Actions 在云端运行，
docker compose 需要真实 Docker），但验证 YAML 语法与关键结构能在本地
提前发现拼写错误、缩进错误、关键字段缺失等问题，避免推到远端后 CI 才报错。

PyYAML 在 CI 的 ``uv sync --extra dev`` 中可用（pyproject.toml dev extra 显式声明）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


class _ComposeLoader(yaml.SafeLoader):
    """扩展 SafeLoader 支持 docker compose-spec 的 ``!reset`` / ``!override`` 自定义标签。

    compose-spec 用 ``!reset []`` 清空继承自 base compose 的列表（如 ``ports``），
    ``!override <value>`` 完全替换列表。这些标签 ``yaml.safe_load`` 默认无法解析，
    需注册 multi-constructor 返回节点值（``!reset []`` 解析为空列表 ``[]``）。
    阶段 11.6 切片 E（#100）用 ``ports: !reset []`` 清空 api 服务的 8000 端口发布，
    生产由 nginx 反代，api 仅在 docker network 内可访问。
    """


def _construct_compose_tag(loader: yaml.Loader, suffix: str, node: yaml.Node) -> Any:
    """``!reset`` / ``!override`` 等自定义标签：返回其下节点的值。"""
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None


_ComposeLoader.add_multi_constructor("!", _construct_compose_tag)


def _load_yaml(path: Path) -> dict:
    """加载 YAML 文件为 dict，文件不存在时 fail。

    用 ``_ComposeLoader`` 支持 compose-spec 的 ``!reset`` / ``!override`` 标签
    （标准 YAML 文件不受影响，仍按 SafeLoader 语义解析）。
    """
    with path.open(encoding="utf-8") as f:
        data = yaml.load(f, Loader=_ComposeLoader)
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


# ---------------------------------------------------------------------------
# nginx/nginx.conf 反代配置（阶段 11.6 切片 E #100）
# ---------------------------------------------------------------------------


class TestNginxConfig:
    """``nginx/nginx.conf`` 反代配置结构测试（阶段 11.6 切片 E #100）。

    nginx 反代 api 容器（``proxy_pass http://api:8000``）+ Let's Encrypt
    webroot 路径（``/.well-known/acme-challenge/``）+ HTTP→HTTPS 301 重定向。
    配置文件用 ``${DOMAIN}`` 占位，由 nginx 容器 entrypoint 用 ``envsubst`` 替换。
    """

    @pytest.fixture(scope="class")
    def nginx_conf_text(self) -> str:
        path = REPO_ROOT / "nginx" / "nginx.conf"
        assert path.exists(), "nginx/nginx.conf 不存在：阶段 11.6 切片 E 要求新增 nginx 反代配置"
        return path.read_text(encoding="utf-8")

    def test_nginx_conf_has_http_server_block_port_80(self, nginx_conf_text: str) -> None:
        """HTTP server block 监听 80 端口（Let's Encrypt challenge + HTTP→HTTPS 重定向）。"""
        assert "listen 80" in nginx_conf_text, (
            "nginx.conf 缺少 'listen 80'：HTTP server block 用于 Let's Encrypt "
            "webroot challenge 与 HTTP→HTTPS 重定向"
        )

    def test_nginx_conf_has_acme_challenge_webroot(self, nginx_conf_text: str) -> None:
        """必须配置 ``/.well-known/acme-challenge/`` 路径指向 webroot 卷。"""
        assert "/.well-known/acme-challenge/" in nginx_conf_text, (
            "nginx.conf 缺少 /.well-known/acme-challenge/ location："
            "Let's Encrypt webroot 模式必需此路径"
        )
        assert "/var/www/certbot" in nginx_conf_text, (
            "nginx.conf 缺少 /var/www/certbot：webroot 卷挂载路径"
        )

    def test_nginx_conf_has_https_redirect_301(self, nginx_conf_text: str) -> None:
        """HTTP 必须返回 301 重定向到 HTTPS。"""
        assert "return 301 https://" in nginx_conf_text, (
            "nginx.conf 缺少 'return 301 https://'：HTTP→HTTPS 重定向"
        )

    def test_nginx_conf_has_https_server_block_port_443(self, nginx_conf_text: str) -> None:
        """HTTPS server block 监听 443 端口。"""
        assert "listen 443" in nginx_conf_text, (
            "nginx.conf 缺少 'listen 443'：HTTPS server block 监听端口"
        )
        assert "ssl" in nginx_conf_text, (
            "nginx.conf 缺少 'ssl' 关键字：HTTPS server block 应启用 ssl"
        )

    def test_nginx_conf_has_ssl_cert_paths(self, nginx_conf_text: str) -> None:
        """SSL 证书路径必须指向 Let's Encrypt 默认目录（``fullchain.pem`` + ``privkey.pem``）。"""
        assert "/etc/letsencrypt/live/" in nginx_conf_text, (
            "nginx.conf 缺少 /etc/letsencrypt/live/ 路径：Let's Encrypt 证书默认目录"
        )
        assert "fullchain.pem" in nginx_conf_text, (
            "nginx.conf 缺少 fullchain.pem：SSL 证书链文件路径"
        )
        assert "privkey.pem" in nginx_conf_text, "nginx.conf 缺少 privkey.pem：SSL 私钥文件路径"

    def test_nginx_conf_has_proxy_pass_to_api(self, nginx_conf_text: str) -> None:
        """必须反代到 api 容器（``proxy_pass http://api:8000``）。"""
        assert "proxy_pass http://api:8000" in nginx_conf_text, (
            "nginx.conf 缺少 'proxy_pass http://api:8000'：nginx 必须反代 api 容器的 8000 端口"
        )

    def test_nginx_conf_has_proxy_headers(self, nginx_conf_text: str) -> None:
        """必须设置标准反代头（Host / X-Real-IP / X-Forwarded-For / X-Forwarded-Proto）。"""
        required_headers = [
            "proxy_set_header Host",
            "proxy_set_header X-Real-IP",
            "proxy_set_header X-Forwarded-For",
            "proxy_set_header X-Forwarded-Proto",
        ]
        missing = [h for h in required_headers if h not in nginx_conf_text]
        assert not missing, f"nginx.conf 缺少反代头: {missing}"

    def test_nginx_conf_uses_domain_envsubst_placeholder(self, nginx_conf_text: str) -> None:
        """配置文件用 ``${DOMAIN}`` 占位，由 entrypoint 用 ``envsubst`` 替换。"""
        assert "${DOMAIN}" in nginx_conf_text, (
            "nginx.conf 缺少 ${DOMAIN} 占位：域名应通过 envsubst 在容器启动时替换（不硬编码）"
        )


# ---------------------------------------------------------------------------
# docker-compose.prod.yml nginx + certbot 服务（阶段 11.6 切片 E #100）
# ---------------------------------------------------------------------------


class TestProdComposeNginxCertbot:
    """``docker-compose.prod.yml`` nginx + certbot 服务结构测试（阶段 11.6 切片 E #100）。

    生产覆盖文件新增 nginx 服务（反代 api + TLS 终止）+ certbot 服务（证书签发与续期）
    + 共享 webroot 卷（Let's Encrypt challenge 文件）+ 证书卷（``/etc/letsencrypt``）。
    nginx 暴露 80/443 端口，api 不再发布 8000 端口（仅 docker network 内可访问）。
    """

    @pytest.fixture(scope="class")
    def compose(self) -> dict:
        return _load_yaml(REPO_ROOT / "docker-compose.prod.yml")

    def test_nginx_service_exists(self, compose: dict) -> None:
        assert "nginx" in compose["services"], (
            "docker-compose.prod.yml 缺少 nginx 服务：阶段 11.6 切片 E 要求 nginx 反代 api"
        )

    def test_nginx_service_uses_nginx_alpine_image(self, compose: dict) -> None:
        """nginx 服务用官方 ``nginx:alpine`` 镜像（维护成本低，体积小）。"""
        image = compose["services"]["nginx"].get("image", "")
        assert "nginx" in image and "alpine" in image, (
            f"nginx 服务应用 nginx:alpine 镜像，实际 image={image!r}"
        )

    def test_nginx_service_exposes_80_and_443(self, compose: dict) -> None:
        """nginx 必须发布 80（HTTP）和 443（HTTPS）端口到宿主机。"""
        ports = compose["services"]["nginx"].get("ports", [])
        # ports 可能是字符串列表（短语法）或 dict 列表（长语法），统一转字符串
        ports_str = " ".join(str(p) for p in ports)
        assert "80" in ports_str, f"nginx 服务应发布 80 端口，实际 ports={ports!r}"
        assert "443" in ports_str, f"nginx 服务应发布 443 端口，实际 ports={ports!r}"

    def test_nginx_service_depends_on_api(self, compose: dict) -> None:
        """nginx 反代 api，应在 api 启动后再启动。"""
        depends = compose["services"]["nginx"].get("depends_on", {})
        # depends_on 可为列表（短语法）或 dict（长语法）
        if isinstance(depends, dict):
            assert "api" in depends, f"nginx 服务 depends_on 应含 api，实际 {depends!r}"
        else:
            assert "api" in list(depends), f"nginx 服务 depends_on 应含 api，实际 {depends!r}"

    def test_nginx_service_mounts_nginx_conf(self, compose: dict) -> None:
        """nginx 服务必须挂载本地 ``nginx/nginx.conf`` 到容器（只读）。"""
        volumes = compose["services"]["nginx"].get("volumes", [])
        volumes_str = " ".join(str(v) for v in volumes)
        assert "nginx.conf" in volumes_str, (
            f"nginx 服务 volumes 应挂载 nginx.conf，实际 {volumes!r}"
        )
        assert ":ro" in volumes_str, f"nginx 服务挂载 nginx.conf 应只读 (:ro)，实际 {volumes!r}"

    def test_nginx_service_mounts_webroot_volume(self, compose: dict) -> None:
        """nginx 服务必须挂载 webroot 卷到 ``/var/www/certbot``（与 certbot 共享）。"""
        volumes = compose["services"]["nginx"].get("volumes", [])
        volumes_str = " ".join(str(v) for v in volumes)
        assert "/var/www/certbot" in volumes_str, (
            "nginx 服务 volumes 应挂载卷到 /var/www/certbot（Let's Encrypt webroot）"
        )

    def test_nginx_service_mounts_certs_volume(self, compose: dict) -> None:
        """nginx 服务必须挂载证书卷到 ``/etc/letsencrypt``（读 certbot 签发的证书）。"""
        volumes = compose["services"]["nginx"].get("volumes", [])
        volumes_str = " ".join(str(v) for v in volumes)
        assert "/etc/letsencrypt" in volumes_str, (
            "nginx 服务 volumes 应挂载卷到 /etc/letsencrypt（读 Let's Encrypt 证书）"
        )

    def test_nginx_service_runs_custom_entrypoint(self, compose: dict) -> None:
        """nginx 服务必须用自定义 entrypoint（``envsubst`` + 证书占位 + 启动 nginx）。"""
        nginx = compose["services"]["nginx"]
        # entrypoint 可为字符串或列表
        entrypoint = nginx.get("entrypoint", "")
        entrypoint_str = " ".join(entrypoint) if isinstance(entrypoint, list) else str(entrypoint)
        assert "entrypoint.sh" in entrypoint_str, (
            f"nginx 服务 entrypoint 应指向 docker/nginx/entrypoint.sh，实际 {entrypoint!r}"
        )

    def test_certbot_service_exists(self, compose: dict) -> None:
        assert "certbot" in compose["services"], (
            "docker-compose.prod.yml 缺少 certbot 服务：阶段 11.6 切片 E 要求 certbot 容器"
        )

    def test_certbot_service_uses_certbot_image(self, compose: dict) -> None:
        """certbot 服务用官方 ``certbot/certbot`` 镜像。"""
        image = compose["services"]["certbot"].get("image", "")
        assert "certbot/certbot" in image, (
            f"certbot 服务应用 certbot/certbot 镜像，实际 image={image!r}"
        )

    def test_certbot_service_mounts_webroot_volume(self, compose: dict) -> None:
        """certbot 服务必须挂载 webroot 卷（与 nginx 共享，写 challenge 响应）。"""
        volumes = compose["services"]["certbot"].get("volumes", [])
        volumes_str = " ".join(str(v) for v in volumes)
        assert "/var/www/certbot" in volumes_str, (
            "certbot 服务 volumes 应挂载卷到 /var/www/certbot（与 nginx 共享 webroot）"
        )

    def test_certbot_service_mounts_certs_volume(self, compose: dict) -> None:
        """certbot 服务必须挂载证书卷（写签发的证书到 ``/etc/letsencrypt``）。"""
        volumes = compose["services"]["certbot"].get("volumes", [])
        volumes_str = " ".join(str(v) for v in volumes)
        assert "/etc/letsencrypt" in volumes_str, (
            "certbot 服务 volumes 应挂载卷到 /etc/letsencrypt（写 Let's Encrypt 证书）"
        )

    def test_certbot_service_runs_custom_entrypoint(self, compose: dict) -> None:
        """certbot 服务必须用自定义 entrypoint（``certbot certonly --webroot`` + 续期 cron）。"""
        certbot = compose["services"]["certbot"]
        entrypoint = certbot.get("entrypoint", "")
        entrypoint_str = " ".join(entrypoint) if isinstance(entrypoint, list) else str(entrypoint)
        assert "entrypoint.sh" in entrypoint_str, (
            f"certbot 服务 entrypoint 应指向 docker/certbot/entrypoint.sh，实际 {entrypoint!r}"
        )

    def test_shared_webroot_volume_defined(self, compose: dict) -> None:
        """必须有共享 webroot 卷（命名卷，nginx + certbot 共用）。"""
        volumes = compose.get("volumes", {})
        # webroot 卷名应包含 'certbot' 或 'webroot' 关键字
        webroot_volumes = [
            name for name in volumes if "certbot" in name.lower() or "webroot" in name.lower()
        ]
        assert webroot_volumes, (
            "docker-compose.prod.yml 顶级 volumes 应声明 webroot 共享卷"
            "（命名卷名含 'certbot' 或 'webroot'）"
        )

    def test_shared_certs_volume_defined(self, compose: dict) -> None:
        """必须有共享证书卷（命名卷，nginx + certbot 共用）。"""
        volumes = compose.get("volumes", {})
        # 证书卷名应包含 'cert' 或 'letsencrypt' 关键字
        cert_volumes = [
            name for name in volumes if "cert" in name.lower() or "letsencrypt" in name.lower()
        ]
        assert cert_volumes, (
            "docker-compose.prod.yml 顶级 volumes 应声明证书共享卷"
            "（命名卷名含 'cert' 或 'letsencrypt'）"
        )

    def test_api_service_does_not_publish_8000_port(self, compose: dict) -> None:
        """api 服务在生产必须用 ``!reset []`` 清空继承的 8000 端口发布。

        base compose 的 api 服务发布 ``8000:8000``（开发用）；生产由 nginx 反代，
        api 仅在 docker network 内可访问（``http://api:8000``），不发布到宿主机。
        compose merge 对 ``ports`` 取并集（非 unique 资源策略），需用 ``!reset []``
        清空，否则宿主机仍可直连 api 绕过 TLS。
        """
        api = compose["services"]["api"]
        ports = api.get("ports")
        # 用 !reset [] 标签清空时，_ComposeLoader 解析为空列表 []
        assert ports == [], (
            "api 服务在生产应清空 published ports（ports: !reset []）："
            "base compose 的 8000 端口在生产被 nginx 反代覆盖，"
            f"api 仅在 docker network 内可访问，实际 ports={ports!r}"
        )


# ---------------------------------------------------------------------------
# docker/nginx/entrypoint.sh nginx 容器入口脚本（阶段 11.6 切片 E #100）
# ---------------------------------------------------------------------------


class TestNginxEntrypoint:
    """``docker/nginx/entrypoint.sh`` nginx 容器入口脚本测试（阶段 11.6 切片 E #100）。

    nginx 容器启动时需：(1) ``envsubst`` 把 ``${DOMAIN}`` 占位替换进 nginx.conf；
    (2) 若真实证书不存在，生成临时自签证书占位（SSL server block 要求 cert 文件存在
    才能 nginx 启动）；(3) 启动 ``crond`` 周期 reload nginx 以读取 certbot 续期的新证书；
    (4) 前台运行 nginx（``nginx -g 'daemon off;'``）。
    """

    @pytest.fixture(scope="class")
    def entrypoint_text(self) -> str:
        path = REPO_ROOT / "docker" / "nginx" / "entrypoint.sh"
        assert path.exists(), (
            "docker/nginx/entrypoint.sh 不存在：阶段 11.6 切片 E 要求 nginx 容器入口脚本"
        )
        return path.read_text(encoding="utf-8")

    def test_entrypoint_runs_envsubst(self, entrypoint_text: str) -> None:
        """必须用 ``envsubst`` 替换 ``${DOMAIN}`` 占位进 nginx.conf。"""
        assert "envsubst" in entrypoint_text, "nginx entrypoint 必须用 envsubst 替换 ${DOMAIN} 占位"

    def test_entrypoint_substitutes_domain_var(self, entrypoint_text: str) -> None:
        """``envsubst`` 必须替换 ``${DOMAIN}`` 变量（指定变量名避免误替换 nginx 内置 ``$host`` 等）。"""
        assert "${DOMAIN}" in entrypoint_text, (
            "nginx entrypoint 的 envsubst 必须指定 ${DOMAIN} 变量名"
            "（避免误替换 nginx 内置 $host / $remote_addr 等变量）"
        )

    def test_entrypoint_generates_placeholder_cert_if_missing(self, entrypoint_text: str) -> None:
        """若真实证书不存在，必须生成临时自签证书占位（让 nginx 能启动）。"""
        # openssl req -x509 生成自签证书
        assert "openssl" in entrypoint_text, (
            "nginx entrypoint 必须用 openssl 生成占位自签证书（SSL server block "
            "要求 cert 文件存在 nginx 才能启动）"
        )
        assert "req" in entrypoint_text and "-x509" in entrypoint_text, (
            "nginx entrypoint 应用 'openssl req -x509' 生成自签证书"
        )
        # 必须检查证书文件存在（避免覆盖已签发的真实证书）
        assert "fullchain.pem" in entrypoint_text, (
            "nginx entrypoint 必须检查 fullchain.pem 是否存在再决定是否生成占位"
        )

    def test_entrypoint_starts_nginx_foreground(self, entrypoint_text: str) -> None:
        """必须以前台模式启动 nginx（``nginx -g 'daemon off;'``）。"""
        assert "daemon off" in entrypoint_text, (
            "nginx entrypoint 必须用 'nginx -g \"daemon off;\"' 前台运行"
            "（docker 容器要求前台进程，否则容器立即退出）"
        )

    def test_entrypoint_sets_up_reload_cron(self, entrypoint_text: str) -> None:
        """必须设置 ``crond`` 周期性 reload nginx（读取 certbot 续期的新证书）。"""
        assert "crond" in entrypoint_text, (
            "nginx entrypoint 必须启动 crond 周期 reload nginx（certbot 续期后需 reload 才能生效）"
        )
        assert "nginx -s reload" in entrypoint_text, (
            "nginx entrypoint 的 cron 任务必须包含 'nginx -s reload'"
        )


# ---------------------------------------------------------------------------
# docker/certbot/entrypoint.sh certbot 容器入口脚本（阶段 11.6 切片 E #100）
# ---------------------------------------------------------------------------


class TestCertbotEntrypoint:
    """``docker/certbot/entrypoint.sh`` certbot 容器入口脚本测试（阶段 11.6 切片 E #100）。

    certbot 容器启动时需：(1) 等待 nginx 启动（webroot challenge 需 nginx 服务 80 端口）；
    (2) 首次签发：若证书目录不存在，运行 ``certbot certonly --webroot`` 签发证书；
    (3) 续期 cron：用 ``crond`` 周期性运行 ``certbot renew``（certbot renew 仅在证书
    临近过期时实际续期，否则 no-op）；(4) 前台运行 ``crond`` 保持容器存活。
    """

    @pytest.fixture(scope="class")
    def entrypoint_text(self) -> str:
        path = REPO_ROOT / "docker" / "certbot" / "entrypoint.sh"
        assert path.exists(), (
            "docker/certbot/entrypoint.sh 不存在：阶段 11.6 切片 E 要求 certbot 容器入口脚本"
        )
        return path.read_text(encoding="utf-8")

    def test_entrypoint_runs_certbot_certonly_webroot(self, entrypoint_text: str) -> None:
        """首次签发必须用 ``certbot certonly --webroot``（不停服模式）。"""
        assert "certbot certonly" in entrypoint_text, (
            "certbot entrypoint 必须用 'certbot certonly' 进行首次签发"
        )
        assert "--webroot" in entrypoint_text, (
            "certbot entrypoint 必须用 --webroot 模式（不停服，"
            "standalone 模式需停 nginx 占用 80 端口）"
        )

    def test_entrypoint_uses_webroot_path_var_www_certbot(self, entrypoint_text: str) -> None:
        """``--webroot-path`` 必须指向 ``/var/www/certbot``（与 nginx 共享的 webroot 卷）。"""
        assert "/var/www/certbot" in entrypoint_text, (
            "certbot entrypoint 的 --webroot-path 必须指向 /var/www/certbot"
            "（与 nginx 共享的 webroot 卷挂载路径）"
        )

    def test_entrypoint_runs_renew(self, entrypoint_text: str) -> None:
        """续期 cron 必须运行 ``certbot renew``（仅在证书临近过期时实际续期）。"""
        assert "certbot renew" in entrypoint_text, (
            "certbot entrypoint 必须用 'certbot renew' 进行续期"
            "（certbot renew 自动检查证书有效期，仅临近过期时实际续期）"
        )

    def test_entrypoint_sets_up_renewal_cron(self, entrypoint_text: str) -> None:
        """必须用 ``crond`` 周期性运行 ``certbot renew``。"""
        assert "crond" in entrypoint_text, (
            "certbot entrypoint 必须启动 crond 周期运行 certbot renew"
            "（Let's Encrypt 证书 90 天有效期，需自动续期）"
        )

    def test_entrypoint_uses_domain_and_email_env_vars(self, entrypoint_text: str) -> None:
        """必须用 ``${DOMAIN}`` 和 ``${LETSENCRYPT_EMAIL}`` 环境变量。"""
        assert "${DOMAIN}" in entrypoint_text, (
            "certbot entrypoint 必须用 ${DOMAIN} 环境变量指定签发证书的域名"
        )
        assert "${LETSENCRYPT_EMAIL}" in entrypoint_text, (
            "certbot entrypoint 必须用 ${LETSENCRYPT_EMAIL} 环境变量"
            "（Let's Encrypt 注册邮箱，用于证书到期提醒）"
        )
