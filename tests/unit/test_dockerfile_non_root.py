"""Dockerfile 非 root 容器改造测试（阶段 11.6 切片 B #98）。

验证 Dockerfile 加 ``USER 65532`` 指令、创建 app 用户、chown /app，
以及 ``docker/entrypoint.sh`` 改用 ``/app/.venv/bin/uvicorn`` 直接调用
绕开 ``uv run`` cache 写权限问题（非 root 用户无权写 ``~/.cache/uv``）。

容器行为测试（``id`` 命令返回 uid=65532、uploads 可写、健康检查通过）需真实
Docker，本地 pytest 只能验证文件结构与关键指令存在性，避免推到远端后 CI 才报错。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
ENTRYPOINT_PATH = REPO_ROOT / "docker" / "entrypoint.sh"


class TestDockerfileNonRoot:
    """Dockerfile 非 root 容器改造（#98）。

    api 容器以 UID 65532:65532 运行，符合 distroless 标准且不与宿主机
    普通用户（1000）冲突。postgres/qdrant 官方镜像已是非 root，无需改动。
    """

    @pytest.fixture(scope="class")
    def dockerfile_text(self) -> str:
        return DOCKERFILE_PATH.read_text(encoding="utf-8")

    def test_dockerfile_has_user_65532_directive(self, dockerfile_text: str) -> None:
        """Dockerfile 必须含 ``USER 65532`` 指令，使容器以非 root 运行。"""
        assert "USER 65532" in dockerfile_text, (
            "Dockerfile 缺少 USER 65532 指令：阶段 11.6 切片 B 要求 api 容器以"
            "非 root 用户（UID 65532）运行，符合容器安全最佳实践"
        )

    def test_dockerfile_creates_app_user_with_uid_65532(self, dockerfile_text: str) -> None:
        """Dockerfile 必须创建 UID 65532 的 app 用户与 app 组。

        - ``groupadd -r app``：创建系统组 ``app``
        - ``useradd -r -g app -u 65532 app``：创建系统用户 ``app``，UID 65532，
          归属 ``app`` 组

        UID 65532 是 distroless 标准，不与宿主机普通用户（典型 UID 1000）冲突。
        """
        assert "groupadd -r app" in dockerfile_text, (
            "Dockerfile 缺少 groupadd -r app：必须创建 app 系统组作为 app 用户的归属组"
        )
        assert "useradd -r -g app -u 65532 app" in dockerfile_text, (
            "Dockerfile 缺少 useradd -r -g app -u 65532 app：必须创建 UID 65532 的"
            "app 系统用户，USER 65532 指令才能生效"
        )

    def test_dockerfile_chowns_app_dir_to_app_user(self, dockerfile_text: str) -> None:
        """Dockerfile 必须 ``chown -R app:app /app`` 让非 root 用户可读写。

        - ``/app`` 包含 ``.venv``（uv sync 装的依赖）、源码、``data/uploads``
        - 整体 chown 简化权限管理，避免逐目录 chown 的遗漏
        - 包含 uploads 目录：非 root 容器需写上传文件，否则 PDF 上传失败
        """
        assert "chown -R app:app /app" in dockerfile_text, (
            "Dockerfile 缺少 chown -R app:app /app：非 root 用户（UID 65532）"
            "需要对 /app 目录（含 .venv、源码、data/uploads）的读写权限，"
            "否则 uvicorn 启动失败 + uploads 写入失败"
        )

    def test_dockerfile_user_directive_after_chown(self, dockerfile_text: str) -> None:
        """``USER 65532`` 必须在 ``chown -R app:app /app`` 之后。

        Dockerfile 指令按顺序执行：``USER`` 之后的 ``RUN`` 以该用户身份执行。
        若 ``USER`` 在 ``chown`` 之前，``chown`` 会以非 root 用户执行，无权限
        修改 /app 属主，构建失败。必须先以 root 完成 ``chown``，再 ``USER`` 切换。
        """
        chown_pos = dockerfile_text.find("chown -R app:app /app")
        user_pos = dockerfile_text.find("USER 65532")
        assert chown_pos != -1 and user_pos != -1, (
            "Dockerfile 必须同时含 chown -R app:app /app 与 USER 65532 指令"
        )
        assert user_pos > chown_pos, (
            "USER 65532 必须在 chown -R app:app /app 之后：Dockerfile 指令按顺序"
            "执行，USER 之后的 RUN 以非 root 身份执行，若 USER 在 chown 之前，"
            "chown 会因无权限而失败"
        )


class TestEntrypointScript:
    """``docker/entrypoint.sh`` 非 root 兼容性改造（#98）。

    非 root 用户（UID 65532）无权写 ``~/.cache/uv``，``uv run`` 会因 cache
    写入失败而崩溃。改用 ``/app/.venv/bin/<binary>`` 直接调用 venv 内的可执行文件，
    绕开 ``uv run`` 的 cache 机制。
    """

    @pytest.fixture(scope="class")
    def entrypoint_text(self) -> str:
        return ENTRYPOINT_PATH.read_text(encoding="utf-8")

    def test_entrypoint_uses_venv_uvicorn_directly(self, entrypoint_text: str) -> None:
        """entrypoint.sh 必须用 ``/app/.venv/bin/uvicorn`` 直接调用 uvicorn。

        ``uv run uvicorn`` 会尝试写 ``~/.cache/uv``，非 root 用户无权写入，
        导致启动失败。直接调用 venv 内的 uvicorn 可执行文件绕开 uv cache。
        """
        assert "/app/.venv/bin/uvicorn" in entrypoint_text, (
            "entrypoint.sh 缺少 /app/.venv/bin/uvicorn：非 root 用户无权写"
            "~/.cache/uv，必须直接调用 venv 内的 uvicorn 绕开 uv run cache 机制"
        )
        assert "uv run uvicorn" not in entrypoint_text, (
            "entrypoint.sh 仍用 uv run uvicorn：非 root 用户下会因 cache 写入"
            "失败而崩溃，必须改为 /app/.venv/bin/uvicorn 直接调用"
        )

    def test_entrypoint_uses_venv_alembic_directly(self, entrypoint_text: str) -> None:
        """entrypoint.sh 必须用 ``/app/.venv/bin/alembic`` 直接调用 alembic。

        与 uvicorn 同理：``uv run alembic`` 会写 ``~/.cache/uv``，非 root 用户
        无权写入。直接调用 venv 内的 alembic 可执行文件绕开 cache。
        """
        assert "/app/.venv/bin/alembic" in entrypoint_text, (
            "entrypoint.sh 缺少 /app/.venv/bin/alembic：与 uvicorn 同理，非 root"
            "用户下 uv run alembic 会因 cache 写入失败而崩溃"
        )
        assert "uv run alembic" not in entrypoint_text, (
            "entrypoint.sh 仍用 uv run alembic：必须改为 /app/.venv/bin/alembic"
            "直接调用，与 uvicorn 改造保持一致"
        )

    def test_entrypoint_preserves_exec_for_pid1(self, entrypoint_text: str) -> None:
        """entrypoint.sh 必须用 ``exec`` 调用 uvicorn，让其接管 PID 1。

        ``exec`` 使 uvicorn 进程替换 entrypoint.sh 进程成为 PID 1：
        - ``docker stop`` 发送 SIGTERM 直接给 uvicorn，触发优雅关闭
        - 不用 ``exec`` 时 uvicorn 是 entrypoint.sh 的子进程，SIGTERM 给
          entrypoint.sh（sh 不转发信号），uvicorn 会在 10s 超时后被 SIGKILL

        非 root 改造不应破坏既有 PID 1 信号传递机制。
        """
        assert "exec /app/.venv/bin/uvicorn" in entrypoint_text, (
            "entrypoint.sh 缺少 exec /app/.venv/bin/uvicorn：必须用 exec 让"
            "uvicorn 接管 PID 1，否则 docker stop 发送 SIGTERM 给 sh 而非 uvicorn，"
            "无法优雅关闭（10s 超时后被 SIGKILL）"
        )
