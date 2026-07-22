"""FastAPI 依赖注入：DB Session、DocumentService、LlmConfig、QaService。

依据 PROJECT_PLAN.md 第 10 节（仓库结构：``api/dependencies.py``）、
第 13.6 节（API 层与服务层职责分离）、第 9.1 节（LLM 配置）。

设计取舍（初学者向说明）：
- **三层依赖链**：``get_session_factory`` → ``get_db`` → ``get_document_service``。
  每层只做一件事，便于在测试中按需替换。
  - ``get_session_factory``：从 ``app.state`` 取应用启动时创建的工厂（lifespan
    里建好），避免每个请求都新建 engine。
  - ``get_db``：每请求开一个 ``Session``，``yield`` 后 ``close``。这是
    FastAPI 推荐的"每请求一会话"模式，保证请求间数据库状态隔离。
  - ``get_document_service``：用当前请求的 Session 构造 ``DocumentService``。
- **问答依赖链**：``get_session_factory`` → ``get_db`` → ``get_qa_service``，
  另有 ``get_llm_config`` 从环境变量构造 ``LlmConfig``。``get_qa_service``
  依赖 ``get_db``（Session）和 ``get_llm_config``（LlmConfig）。
- **``yield`` 依赖**：FastAPI 支持 generator 依赖，``yield`` 之前的代码在请求
  开始执行，``yield`` 之后的代码在请求结束（包括异常）执行。这样无论请求
  成功还是抛异常，Session 都会被关闭，不会泄漏连接。
- **测试用 ``app.dependency_overrides`` 替换**：测试时把 ``get_document_service``
  或 ``get_qa_service`` 换成返回 Mock 的函数，``get_db`` 和 ``get_session_factory``
  都不会被调用，因此测试不依赖真实数据库（PROJECT_PLAN 第 13.2 节"测试中应
  Mock ... CI 不依赖外部网络"）。
- **不在 ``get_db`` 里 commit/rollback**：事务边界由 service 层控制
  （``DocumentService`` / ``QaService`` 决定何时 commit）。``get_db`` 只负责
  Session 生命周期。
- **``get_llm_config`` 每请求读环境变量**：``LlmConfig`` 是不可变 dataclass，
  每请求从环境变量构造，确保运行时改环境变量能生效（如切换模型 / 切换 provider）。
  根据 ``LLM_PROVIDER`` 分发：``openai`` 读 ``LLM_BASE_URL`` / ``LLM_API_KEY``
  / ``LLM_MODEL``；``ollama`` 读 ``OLLAMA_BASE_URL`` / ``OLLAMA_MODEL``（无需
  API Key）。测试时通过 ``monkeypatch`` 修改环境变量或用
  ``dependency_overrides`` 替换。``LLM_TIMEOUT`` / ``LLM_MAX_RETRIES`` 做
  int/float 转换，格式错误时回退到默认值，避免请求因配置格式错误而失败。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import Depends, Request

from research_rag.qa_service import (
    DEFAULT_LLM_MAX_RETRIES,
    DEFAULT_LLM_TIMEOUT,
    DEFAULT_OLLAMA_BASE_URL,
    LlmConfig,
)
from research_rag.services.document_service import DocumentService
from research_rag.services.qa_service import QaService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """从 ``app.state`` 取应用启动时创建的 session 工厂。

    工厂在 ``create_app`` 的 ``lifespan`` 中创建并挂到 ``app.state.session_factory``，
    整个应用生命周期共用一个 engine + factory，避免每请求新建连接池。
    """

    factory: sessionmaker[Session] | None = getattr(request.app.state, "session_factory", None)
    if factory is None:  # pragma: no cover - lifespan 未运行属于编程错误
        raise RuntimeError(
            "session_factory 未初始化：请通过 create_app() 启动应用，或在测试中"
            "传入 session_factory 参数。"
        )
    return factory


def get_db(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> Iterator[Session]:
    """每请求一个 Session，请求结束（含异常）自动关闭。

    依赖 ``get_session_factory``（从 ``app.state`` 取启动时创建的工厂）。
    不在此 commit/rollback：事务边界由 service 层控制。
    """

    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_document_service(session: Session = Depends(get_db)) -> DocumentService:
    """用当前请求的 Session 构造 ``DocumentService``。

    测试时通过 ``app.dependency_overrides[get_document_service]`` 替换为 Mock，
    完全跳过真实数据库与文件 IO。
    """

    return DocumentService(session)


# ---------------------------------------------------------------------------
# 问答 API 依赖（PROJECT_PLAN.md 第 9.1 节 LLM 配置、第 8.4 节问答 API）
# ---------------------------------------------------------------------------


def _parse_float(value: str, default: float) -> float:
    """安全解析 float，格式错误时回退到默认值。"""

    try:
        return float(value)
    except ValueError:
        return default


def _parse_int(value: str, default: int) -> int:
    """安全解析 int，格式错误时回退到默认值。"""

    try:
        return int(value)
    except ValueError:
        return default


def get_llm_config() -> LlmConfig:
    """从环境变量构造 ``LlmConfig``（PROJECT_PLAN.md 第 9.1 节、.env.example）。

    根据 ``LLM_PROVIDER`` 分发读不同环境变量：
    - ``LLM_PROVIDER=openai``（默认）：读 ``LLM_BASE_URL`` / ``LLM_API_KEY``
      / ``LLM_MODEL``
    - ``LLM_PROVIDER=ollama``：读 ``OLLAMA_BASE_URL``（默认
      ``DEFAULT_OLLAMA_BASE_URL``）/ ``OLLAMA_MODEL``（Ollama 无需 API Key）

    两个 provider 共用 ``LLM_TIMEOUT`` / ``LLM_MAX_RETRIES``（格式错误时回退
    默认值）。注意 ``max_retries`` 仅对 OpenAI provider 生效（Ollama 服务
    自行管理重试，``ChatOllama`` 不接受该参数）。

    测试时通过 ``app.dependency_overrides[get_llm_config]`` 替换为固定配置，
    或用 ``monkeypatch`` 修改环境变量。
    """

    provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
    timeout = _parse_float(os.environ.get("LLM_TIMEOUT", ""), DEFAULT_LLM_TIMEOUT)
    max_retries = _parse_int(os.environ.get("LLM_MAX_RETRIES", ""), DEFAULT_LLM_MAX_RETRIES)

    if provider == "ollama":
        return LlmConfig(
            provider="ollama",
            base_url=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            model=os.environ.get("OLLAMA_MODEL", ""),
            timeout=timeout,
            max_retries=max_retries,
        )

    # 默认 openai
    return LlmConfig(
        provider="openai",
        base_url=os.environ.get("LLM_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
        model=os.environ.get("LLM_MODEL", ""),
        timeout=timeout,
        max_retries=max_retries,
    )


def get_qa_service(
    session: Session = Depends(get_db),
    llm_config: LlmConfig = Depends(get_llm_config),
) -> QaService:
    """用当前请求的 Session + LlmConfig 构造 ``QaService``。

    依赖 ``get_db``（Session）和 ``get_llm_config``（LlmConfig）。
    ``QaService`` 内部会惰性创建 Embedding 和 ChatModel（第一次调用
    ``answer`` 时），测试时通过 ``app.dependency_overrides[get_qa_service]``
    替换为 Mock，完全跳过真实数据库、LLM 和 Embedding 调用。
    """

    return QaService(session, llm_config)
