"""FastAPI 依赖注入：DB Session 与 DocumentService。

依据 PROJECT_PLAN.md 第 10 节（仓库结构：``api/dependencies.py``）、
第 13.6 节（API 层与服务层职责分离）。

设计取舍（初学者向说明）：
- **三层依赖链**：``get_session_factory`` → ``get_db`` → ``get_document_service``。
  每层只做一件事，便于在测试中按需替换。
  - ``get_session_factory``：从 ``app.state`` 取应用启动时创建的工厂（lifespan
    里建好），避免每个请求都新建 engine。
  - ``get_db``：每请求开一个 ``Session``，``yield`` 后 ``close``。这是
    FastAPI 推荐的"每请求一会话"模式，保证请求间数据库状态隔离。
  - ``get_document_service``：用当前请求的 Session 构造 ``DocumentService``。
- **``yield`` 依赖**：FastAPI 支持 generator 依赖，``yield`` 之前的代码在请求
  开始执行，``yield`` 之后的代码在请求结束（包括异常）执行。这样无论请求
  成功还是抛异常，Session 都会被关闭，不会泄漏连接。
- **测试用 ``app.dependency_overrides`` 替换**：测试时把 ``get_document_service``
  换成返回 Mock 的函数，``get_db`` 和 ``get_session_factory`` 都不会被调用，
  因此测试不依赖真实数据库（PROJECT_PLAN 第 13.2 节"测试中应 Mock ... CI 不
  依赖外部网络"）。
- **不在 ``get_db`` 里 commit/rollback**：事务边界由 service 层控制
  （``DocumentService`` 决定何时 commit）。``get_db`` 只负责 Session 生命周期。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

from research_rag.services.document_service import DocumentService

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


def get_db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """每请求一个 Session，请求结束（含异常）自动关闭。

    不在此 commit/rollback：事务边界由 ``DocumentService`` 控制。
    """

    session = session_factory()
    try:
        yield session
    finally:
        session.close()


def get_document_service(session: Session) -> DocumentService:
    """用当前请求的 Session 构造 ``DocumentService``。

    测试时通过 ``app.dependency_overrides[get_document_service]`` 替换为 Mock，
    完全跳过真实数据库与文件 IO。
    """

    return DocumentService(session)
