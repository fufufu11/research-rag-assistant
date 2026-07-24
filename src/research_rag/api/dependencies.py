"""FastAPI 依赖注入：DB Session、DocumentService、LlmConfig、QaService、VectorStore。

依据 PROJECT_PLAN.md 第 10 节（仓库结构：``api/dependencies.py``）、
第 13.6 节（API 层与服务层职责分离）、第 9.1 节（LLM 配置）、
第 716-722 行（阶段 6：Qdrant 向量库依赖注入）。

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
  依赖 ``get_db``（Session）、``get_llm_config``（LlmConfig）和
  ``get_vector_store``（QdrantVectorStore）。
- **向量库依赖**：``get_vector_store`` 从 ``app.state`` 取应用启动时创建的
  ``QdrantVectorStore``（lifespan 里建好）。``None`` 表示未配置 Qdrant，
  service 层会回退到 InMemoryVectorStore（阶段 5 行为）。
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
  每请求从环境变量构造，确保运行时改环境变量能生效（如切换模型）。
  读 ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL``。测试时通过
  ``monkeypatch`` 修改环境变量或用 ``dependency_overrides`` 替换。
  ``LLM_TIMEOUT`` / ``LLM_MAX_RETRIES`` 做 int/float 转换，格式错误时回退
  到默认值，避免请求因配置格式错误而失败。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from fastapi import Depends, Request

from research_rag.embedding import (
    DASHSCOPE_DEFAULT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_PROVIDER,
    JINA_DEFAULT_MODEL,
    EmbeddingConfig,
)
from research_rag.qa_service import (
    DEFAULT_LLM_MAX_RETRIES,
    DEFAULT_LLM_TIMEOUT,
    LlmConfig,
)
from research_rag.services.document_service import DocumentService
from research_rag.services.qa_service import QaService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_qdrant import QdrantVectorStore
    from sqlalchemy.orm import Session, sessionmaker

    from research_rag.hybrid_retriever import BM25IndexCache
    from research_rag.reranker import BaseReranker


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


def get_vector_store(request: Request) -> QdrantVectorStore | None:
    """从 ``app.state`` 取应用启动时创建的 ``QdrantVectorStore``。

    工厂在 ``create_app`` 的 ``lifespan`` 中创建并挂到 ``app.state.vector_store``。
    ``None`` 表示未配置 Qdrant（测试环境或未启用向量库），service 层会回退
    到 InMemoryVectorStore。
    """

    return getattr(request.app.state, "vector_store", None)


def get_reranker(request: Request) -> BaseReranker | None:
    """从 ``app.state`` 取应用启动时创建的 Reranker。

    工厂在 ``create_app`` 的 ``lifespan`` 中创建并挂到 ``app.state.reranker``。
    ``None`` 表示未启用 Reranker（``RERANKER_ENABLED`` 非 true 或创建失败），
    service 层跳过重排序，直接使用向量检索原始排序。
    """

    return getattr(request.app.state, "reranker", None)


def get_bm25_cache(request: Request) -> BM25IndexCache | None:
    """从 ``app.state`` 取应用启动时创建的 BM25 索引缓存（阶段 10.3）。

    工厂在 ``create_app`` 的 ``lifespan`` 中创建并挂到 ``app.state.bm25_cache``。
    仅当 ``BM25_ENABLED=true`` 时创建（``QaService`` 未启用 BM25 时不会走
    ``_retrieve_hybrid`` 路径，cache 不会被访问）。``None`` 表示未配置，
    ``QaService._retrieve_hybrid`` 会回退到每次重建 BM25 索引（旧行为）。
    """

    return getattr(request.app.state, "bm25_cache", None)


def get_document_service(
    session: Session = Depends(get_db),
    vector_store: QdrantVectorStore | None = Depends(get_vector_store),
) -> DocumentService:
    """用当前请求的 Session + VectorStore 构造 ``DocumentService``。

    测试时通过 ``app.dependency_overrides[get_document_service]`` 替换为 Mock，
    完全跳过真实数据库、文件 IO 和向量库。
    """

    return DocumentService(session, vector_store=vector_store)


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

    读 ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL``。
    ``LLM_TIMEOUT`` / ``LLM_MAX_RETRIES`` 做 int/float 转换，格式错误时回退
    默认值，避免请求因配置格式错误而失败。

    测试时通过 ``app.dependency_overrides[get_llm_config]`` 替换为固定配置，
    或用 ``monkeypatch`` 修改环境变量。
    """

    timeout = _parse_float(os.environ.get("LLM_TIMEOUT", ""), DEFAULT_LLM_TIMEOUT)
    max_retries = _parse_int(os.environ.get("LLM_MAX_RETRIES", ""), DEFAULT_LLM_MAX_RETRIES)

    return LlmConfig(
        base_url=os.environ.get("LLM_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
        model=os.environ.get("LLM_MODEL", ""),
        timeout=timeout,
        max_retries=max_retries,
    )


def get_embedding_config() -> EmbeddingConfig:
    """从环境变量构造 ``EmbeddingConfig``（阶段 8.4、.env.example）。

    读 ``EMBEDDING_PROVIDER`` 选择后端（默认 ``local``）：

    - ``local``（默认）：本地 HuggingFace 推理。读 ``EMBEDDING_MODEL``（模型名），
      未设置用 ``DEFAULT_EMBEDDING_MODEL``（``BAAI/bge-small-zh-v1.5``，中文优化，
      生产面向中文用户）。英文场景可传 ``BAAI/bge-small-en-v1.5``，混合场景可传
      ``BAAI/bge-m3``（dense 1024 维，约 2.2GB，CPU 推理慢）。
    - ``dashscope``：阿里百炼 OpenAI 兼容 API。读 ``EMBEDDING_MODEL``（百炼模型名，
      未设置用 ``text-embedding-v4``，Qwen3-Embedding 系列，1024 维）、
      ``DASHSCOPE_API_KEY``（API Key，**绝不硬编码**）、``EMBEDDING_BASE_URL``
      （可选，默认百炼 endpoint）、``EMBEDDING_DIMENSIONS``（可选，默认 1024）、
      ``EMBEDDING_BATCH_SIZE``（可选，默认 10，受百炼单次请求行数限制）。
    - ``jina``：Jina AI OpenAI 兼容 API。读 ``EMBEDDING_MODEL``（Jina 模型名，
      未设置用 ``jina-embeddings-v3``，多语言含中文，1024 维）、``JINA_API_KEY``
      （API Key，**绝不硬编码**）、``EMBEDDING_BASE_URL``（可选，默认 Jina endpoint）、
      ``EMBEDDING_DIMENSIONS`` / ``EMBEDDING_BATCH_SIZE``（可选）。

    适合本地 CPU 推理慢（如 bge-m3）或需更大模型但不想本地部署的场景。.env 不会被
    FastAPI 自动加载，API Key 需显式设置环境变量。

    注：阶段 8.4 实测 bge-m3 在纯英文论文评测下不及 bge-small-en（Hit@5
    70.0% vs 76.7%），故本地默认仍保留小模型；中文场景的 API 对比见
    ``docs/evaluation_report_zh.md``。

    测试时通过 ``app.dependency_overrides[get_embedding_config]`` 替换为固定配置，
    或用 ``monkeypatch`` 修改环境变量。
    """

    provider = (
        os.environ.get("EMBEDDING_PROVIDER", "").strip().lower() or DEFAULT_EMBEDDING_PROVIDER
    )
    model_name = os.environ.get("EMBEDDING_MODEL", "").strip()

    if provider == "dashscope":
        return EmbeddingConfig(
            provider="dashscope",
            model_name=model_name or DASHSCOPE_DEFAULT_MODEL,
            api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            base_url=os.environ.get("EMBEDDING_BASE_URL", ""),
            dimensions=_parse_int(os.environ.get("EMBEDDING_DIMENSIONS", ""), 0),
            batch_size=_parse_int(os.environ.get("EMBEDDING_BATCH_SIZE", ""), 0),
        )

    if provider == "jina":
        return EmbeddingConfig(
            provider="jina",
            model_name=model_name or JINA_DEFAULT_MODEL,
            api_key=os.environ.get("JINA_API_KEY", ""),
            base_url=os.environ.get("EMBEDDING_BASE_URL", ""),
            dimensions=_parse_int(os.environ.get("EMBEDDING_DIMENSIONS", ""), 0),
            batch_size=_parse_int(os.environ.get("EMBEDDING_BATCH_SIZE", ""), 0),
        )

    # local（默认）
    return EmbeddingConfig(model_name=model_name or DEFAULT_EMBEDDING_MODEL)


def get_qa_service(
    session: Session = Depends(get_db),
    llm_config: LlmConfig = Depends(get_llm_config),
    embedding_config: EmbeddingConfig = Depends(get_embedding_config),
    vector_store: QdrantVectorStore | None = Depends(get_vector_store),
    reranker: BaseReranker | None = Depends(get_reranker),
    bm25_cache: BM25IndexCache | None = Depends(get_bm25_cache),
) -> QaService:
    """用当前请求的 Session + LlmConfig + EmbeddingConfig + VectorStore + Reranker 构造 ``QaService``。

    依赖 ``get_db``（Session）、``get_llm_config``（LlmConfig）、
    ``get_embedding_config``（EmbeddingConfig）、``get_vector_store``
    （QdrantVectorStore）、``get_reranker``（BaseReranker）和
    ``get_bm25_cache``（BM25IndexCache，阶段 10.3）。
    ``QaService`` 内部会惰性创建 Embedding 和 ChatModel（第一次调用
    ``answer`` 时），测试时通过 ``app.dependency_overrides[get_qa_service]``
    替换为 Mock，完全跳过真实数据库、LLM 和 Embedding 调用。

    BM25 混合检索通过环境变量 ``BM25_ENABLED=true`` 启用（阶段 8.3）。
    启用时 ``QaService.answer`` 会构建 BM25 索引与向量检索并行召回 + RRF 融合。
    阶段 10.3 起，``BM25IndexCache`` 经依赖注入，命中时复用已构建索引避免重建。
    ``bm25_cache`` 为 ``None``（未启用 BM25 或 lifespan 未创建）时，
    ``_retrieve_hybrid`` 回退到每次重建（旧行为）。
    """

    from research_rag.hybrid_retriever import is_bm25_enabled

    return QaService(
        session,
        llm_config,
        embedding_config=embedding_config,
        vector_store=vector_store,
        reranker=reranker,
        bm25_enabled=is_bm25_enabled(),
        bm25_cache=bm25_cache,
    )
