"""FastAPI 应用工厂。

依据 PROJECT_PLAN.md 第 8 节（API 草案）、第 10 节（仓库结构）、
第 13.6 节（API 层负责将异常转换为稳定错误码，业务服务不直接拼接 HTTP 响应）。

设计取舍（初学者向说明）：
- **应用工厂 ``create_app()``**：不创建模块级 ``app`` 单例，返回新实例。便于
  ① 测试时注入不同配置（如内存 SQLite factory、Mock service）；② 未来在同进程
  跑多个应用实例（如灰度发布）。FastAPI 官方推荐模式。
- **``lifespan`` 管理资源**：engine/session_factory 在应用启动时创建一次，
  关闭时 ``engine.dispose()`` 释放连接池。比 ``@app.on_event("startup")``
  现代（FastAPI 0.93+ 推荐 ``lifespan``，旧 API 已弃用）。
  - 如果调用方传入了 ``session_factory``（测试场景），lifespan 不再创建 engine，
    也不在关闭时 dispose（由调用方管理生命周期）。
- **CORS 中间件**：开发环境允许 localhost 前端（Vite 默认 5173、常见 dev
  server 3000/8000）访问。生产环境同源托管静态文件无需 CORS（ADR 0005）。
- **异常处理器集中注册**：``DuplicateDocumentError`` → 409 Conflict，
  ``DocumentNotFoundError`` → 404 Not Found，统一返回 ``ErrorResponse`` 格式
  （``{"detail": "..."}``）。业务 service 只抛异常，不感知 HTTP（PROJECT_PLAN
  第 13.6 节）。新增业务异常只需在此处加一个 ``@app.exception_handler``。
- **``/api/v1`` 前缀由路由自身声明**：``documents.router`` 自带
  ``prefix="/api/v1/documents"``，``app.include_router`` 不再额外加前缀，
  避免前缀重复拼接错误。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from research_rag.api.auth import verify_api_key
from research_rag.api.rate_limit import (
    RateLimitExceeded,
    SlowAPIMiddleware,
    configure_limiter,
    handle_rate_limit_exceeded,
    limiter,
)
from research_rag.api.routes.conversations import router as conversations_router
from research_rag.api.routes.documents import router as documents_router
from research_rag.api.routes.feedback import router as feedback_router
from research_rag.api.routes.queries import router as queries_router
from research_rag.api.schemas import ErrorResponse
from research_rag.db.models import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    FeedbackNotFoundError,
)
from research_rag.db.session import create_engine_for_url, get_database_url
from research_rag.embedding import EmbeddingServiceError, VectorStoreError
from research_rag.observability import flush as flush_langfuse
from research_rag.qa_service import InsufficientEvidenceError, LlmServiceError
from research_rag.reranker import RerankerError
from research_rag.services.qa_service import ConversationNotFoundError, NoAvailableDocumentsError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langchain_qdrant import QdrantVectorStore
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session

# 开发环境允许的前端来源（Vite 5173 / 常见 dev server）。
# 生产环境后端托管 frontend/dist 静态文件，同源无需 CORS（ADR 0005）。
DEFAULT_CORS_ORIGINS = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用启动时创建 engine + session_factory + vector_store，关闭时 dispose。

    若 ``app.state.session_factory`` 已由 ``create_app`` 调用方注入（测试场景），
    则跳过创建与 dispose，生命周期由调用方管理。

    向量库（``QdrantVectorStore``）在启动时创建并挂到 ``app.state.vector_store``。
    创建失败（如 Qdrant 服务未启动）不阻断应用启动：service 层会回退到
    InMemoryVectorStore，文档上传和问答仍可用（但无持久化向量）。
    """

    created_engine: Engine | None = None
    if getattr(app.state, "session_factory", None) is None:
        engine = create_engine_for_url(get_database_url())
        factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )
        app.state.session_factory = factory
        created_engine = engine

    # 创建 QdrantVectorStore（best-effort：失败不阻断启动）
    if getattr(app.state, "vector_store", None) is None:
        try:
            app.state.vector_store = _create_vector_store()
        except VectorStoreError:
            # Qdrant 不可用：app.state.vector_store 保持 None，
            # service 层回退到 InMemoryVectorStore
            app.state.vector_store = None

    # 创建 Reranker（best-effort：失败不阻断启动）
    # RERANKER_ENABLED=true 时尝试加载 CrossEncoder，失败时保持 None
    if getattr(app.state, "reranker", None) is None:
        from research_rag.reranker import create_reranker_if_enabled

        app.state.reranker = create_reranker_if_enabled()

    # 阶段 10.3：创建 BM25 索引缓存（仅 bm25_enabled 时）
    # BM25IndexCache 无 IO 依赖，创建轻量；未启用 BM25 时不创建（保持 None，
    # QaService 不会走 _retrieve_hybrid 路径，cache 不会被访问）
    if getattr(app.state, "bm25_cache", None) is None:
        from research_rag.hybrid_retriever import BM25IndexCache, is_bm25_enabled

        if is_bm25_enabled():
            app.state.bm25_cache = BM25IndexCache()

    try:
        yield
    finally:
        if created_engine is not None:
            created_engine.dispose()
        # 阶段 10.1：应用关闭时刷新 Langfuse 异步上报队列，避免最后一批 trace 丢失。
        # 未启用 Langfuse 时 ``flush_langfuse`` 内部 no-op。
        flush_langfuse()


def _create_vector_store() -> QdrantVectorStore | None:
    """创建 QdrantVectorStore 实例（best-effort）。

    从环境变量读 Qdrant 配置，创建 Embedding + QdrantVectorStore。
    任何失败（Qdrant 服务未启动、依赖缺失、embedding 加载失败）都返回
    ``None``，让 service 层回退到 InMemoryVectorStore。

    返回 ``None`` 的情况：
    - ``QDRANT_ENABLED`` 环境变量为 ``"false"``（显式禁用）
    - Qdrant 服务连接失败
    - Embedding 模型加载失败
    """

    import os

    # 允许显式禁用 Qdrant（测试或无 Docker 环境时）
    if os.environ.get("QDRANT_ENABLED", "true").strip().lower() == "false":
        return None

    try:
        from research_rag.api.dependencies import get_embedding_config
        from research_rag.embedding import create_embeddings
        from research_rag.vector_store import create_vector_store, get_qdrant_config
    except ImportError:
        return None

    try:
        # 阶段 8.4：从 EMBEDDING_MODEL 环境变量读取模型名（默认 bge-small-zh-v1.5 中文优化）
        embeddings = create_embeddings(get_embedding_config())
        config = get_qdrant_config()
        return create_vector_store(config, embeddings)
    except Exception:
        # Qdrant 不可用或 embedding 加载失败，回退到 None
        return None


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例。

    Args:
        session_factory: 可选的 session 工厂。测试时传入内存 SQLite factory
            隔离数据库；``None``（默认）时由 ``lifespan`` 在启动时用
            ``get_database_url()`` 创建真实 engine + factory。
        cors_origins: 可选的 CORS 允许来源列表。``None`` 时用
            ``DEFAULT_CORS_ORIGINS``（开发环境 localhost）。

    Returns:
        配置好的 ``FastAPI`` 实例，未启动（由 ``uvicorn`` 或 ``TestClient``
        驱动 lifespan）。
    """

    app = FastAPI(
        title="Research RAG Assistant",
        description="科研文献可溯源智能问答系统 —— 文档管理 API",
        version="0.0.0",
        lifespan=_lifespan,
    )

    # 挂载 session_factory 到 app.state，供 lifespan 和 get_session_factory 读取
    app.state.session_factory = session_factory
    # vector_store 初始为 None，lifespan 中按需创建（测试时不创建）
    # 注意：app.state 是 Starlette.State，不支持类型标注赋值，只能直接赋值
    app.state.vector_store = None
    # reranker 初始为 None，lifespan 中按需创建（测试时不创建）
    app.state.reranker = None
    # bm25_cache 初始为 None，lifespan 中按需创建（仅 bm25_enabled 时）
    app.state.bm25_cache = None

    # CORS 中间件（开发环境允许 localhost）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or DEFAULT_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 阶段 11.3：限流配置（slowapi）
    # configure_limiter 按当前 RATE_LIMIT_ENABLED 环境变量设置 limiter.enabled。
    # limiter 是模块级单例，default_limits 用 callable 在请求时动态读
    # RATE_LIMIT_PER_MINUTE，支持 monkeypatch 测试。
    # SlowAPIMiddleware 对所有 /api/v1/* 端点应用默认限流（60/min），
    # upload_document 路由用 @limiter.limit 覆盖为更严的 10/min。
    # limiter.enabled=False 时中间件 no-op（向后兼容现有 720+ 测试）。
    configure_limiter()
    app.state.limiter = limiter
    # mypy: Starlette 的 ExceptionHandler 类型签名要求第二参数为 ``Exception``
    # （非子类），但 slowapi 默认 handler 与项目其他 handler 都用具体异常子类
    # （如 ``DuplicateDocumentError``）。运行时按注册类型分发，类型签名差异是
    # Starlette typing 的已知限制（异步 handler 用装饰器形式可绕过，同步用
    # ``add_exception_handler`` 需显式 ignore）。
    app.add_exception_handler(RateLimitExceeded, handle_rate_limit_exceeded)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    # 异常处理器：业务异常 → HTTP 状态码 + ErrorResponse（PROJECT_PLAN 第 13.6 节）
    @app.exception_handler(DuplicateDocumentError)
    async def handle_duplicate(_request: Request, exc: DuplicateDocumentError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(DocumentNotFoundError)
    async def handle_not_found(_request: Request, exc: DocumentNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(NoAvailableDocumentsError)
    async def handle_no_available(
        _request: Request, exc: NoAvailableDocumentsError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(ConversationNotFoundError)
    async def handle_conversation_not_found(
        _request: Request, exc: ConversationNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(FeedbackNotFoundError)
    async def handle_feedback_not_found(
        _request: Request, exc: FeedbackNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(InsufficientEvidenceError)
    async def handle_insufficient_evidence(
        _request: Request, exc: InsufficientEvidenceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(LlmServiceError)
    async def handle_llm_error(_request: Request, exc: LlmServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(EmbeddingServiceError)
    async def handle_embedding_error(_request: Request, exc: EmbeddingServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(VectorStoreError)
    async def handle_vector_store_error(_request: Request, exc: VectorStoreError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    @app.exception_handler(RerankerError)
    async def handle_reranker_error(_request: Request, exc: RerankerError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(detail=str(exc)).model_dump(),
        )

    # 路由（阶段 11.1：所有 /api/v1/* 端点接入 API Key 认证）
    # 用 include_router 的 dependencies 参数集中挂载，无需改每个路由文件。
    # verify_api_key 在 API_KEY_ENABLED 非 true 时直接放行（向后兼容现有测试）。
    auth_dependency = [Depends(verify_api_key)]
    app.include_router(documents_router, dependencies=auth_dependency)
    app.include_router(queries_router, dependencies=auth_dependency)
    app.include_router(conversations_router, dependencies=auth_dependency)
    app.include_router(feedback_router, dependencies=auth_dependency)

    return app
