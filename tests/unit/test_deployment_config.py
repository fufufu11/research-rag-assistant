"""部署配置文件测试（阶段 11.5）。

验证 CI/CD 相关的 YAML 配置文件语法正确且结构符合预期：
- ``.github/workflows/deploy.yml``：deploy workflow 存在，含 build-and-push / deploy job
- ``docker-compose.prod.yml``：生产覆盖文件存在，api 服务引用 GHCR 镜像

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
