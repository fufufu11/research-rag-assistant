"""Qdrant 向量存储适配器。

依据 PROJECT_PLAN.md 第 716-722 行（阶段 6 交付物）、第 158-159 行（技术选型：
InMemoryVectorStore → Qdrant）、第 268 行（Chunk.vector_id 字段）、
第 46 行（Qdrant 集合、向量和 Payload 设计）。

设计取舍（初学者向说明）：
- **与 ``embedding.py`` 的 InMemoryVectorStore 并存**：不删除阶段 3 的内存实现，
  作为测试 fallback 和学习成果保留。本模块是生产实现，通过依赖注入选择用哪个。
- **Payload 设计**：每个向量点的 payload 存 ``document_id`` / ``document_name``
  / ``start_page`` / ``end_page`` / ``chunk_index``（放在 LangChain metadata 下），
  支持按 ``document_id`` 过滤检索和批量删除。``page_content`` 单独存为 content payload。
- **向量 ID 用 chunk UUID**：上传时用 ORM ``Chunk.id`` 作为 Qdrant point ID，
  ``vector_id = str(chunk.id)``，避免"先写 Qdrant 再回填 DB"的二次更新。
- **删除用 Payload 过滤**：Qdrant ``client.delete`` 支持按 Filter 批量删除，
  比遍历 vector_id 删除更可靠（即使 DB 和 Qdrant 短暂不一致也能按文档清理）。
- **惰性导入**：``qdrant_client`` / ``langchain_qdrant`` 在函数内导入，
  未安装时抛 ``VectorStoreError``，归一化依赖错误。
- **手动创建集合**：``create_vector_store`` 先检查集合是否存在，不存在则按
  embedding 维度创建（Cosine 距离），再传 ``validate_collection_config=False``
  跳过 ``QdrantVectorStore`` 的自动校验（避免"Collection not found"错误）。
- **测试用内存 Qdrant**：``QdrantClient(":memory:")`` 创建纯内存实例，
  不需要 Docker 或外部服务，CI 可直接跑。
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from research_rag.embedding import VectorStoreError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from langchain_qdrant import QdrantVectorStore

    from research_rag.db.models import Chunk

# 默认 Qdrant 配置（PROJECT_PLAN.md 第 11 节、.env.example）
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_QDRANT_COLLECTION = "research_chunks"


@dataclass(frozen=True)
class QdrantConfig:
    """Qdrant 连接配置。

    Attributes:
        url: Qdrant 服务地址，默认 ``http://localhost:6333``。
        collection_name: 集合名，默认 ``research_chunks``。
        api_key: API Key（Qdrant Cloud 用），本地部署无需。
    """

    url: str = DEFAULT_QDRANT_URL
    collection_name: str = DEFAULT_QDRANT_COLLECTION
    api_key: str | None = None


@dataclass(frozen=True)
class QdrantSearchResult:
    """Qdrant 检索结果（不可变）。

    与 ``embedding.RetrievalResult`` 的区别：本结构含 ``document_id`` 和
    ``document_name``，因为 Qdrant payload 存了完整元数据，单库检索即可
    知道结果归属（不再需要"每文档单独索引"）。

    Attributes:
        document_id: 所属文档 UUID。
        document_name: 文档原始文件名（展示用）。
        start_page: chunk 内容起始页码。
        end_page: chunk 内容结束页码。跨页切分时 ``end_page > start_page``，
            不跨页时 ``end_page == start_page``。
        chunk_index: 文档内分段序号。
        content: 分段文本。
        score: 相似度分数，越高越相关。
    """

    document_id: uuid.UUID
    document_name: str
    start_page: int
    end_page: int
    chunk_index: int
    content: str
    score: float


def get_qdrant_config() -> QdrantConfig:
    """从环境变量构造 ``QdrantConfig``。

    环境变量（.env.example）：
    - ``QDRANT_URL``：Qdrant 服务地址
    - ``QDRANT_COLLECTION``：集合名
    - ``QDRANT_API_KEY``：API Key（可选，Cloud 用）
    """

    return QdrantConfig(
        url=os.environ.get("QDRANT_URL", DEFAULT_QDRANT_URL),
        collection_name=os.environ.get("QDRANT_COLLECTION", DEFAULT_QDRANT_COLLECTION),
        api_key=os.environ.get("QDRANT_API_KEY") or None,
    )


def create_vector_store(
    config: QdrantConfig,
    embeddings: Embeddings,
) -> QdrantVectorStore:
    """创建 QdrantVectorStore 实例（惰性导入，自动建集合）。

    支持 ``config.url=":memory:"`` 创建纯内存 Qdrant（测试用，不需要 Docker）。
    集合不存在时按 embedding 维度自动创建（Cosine 距离）。

    Args:
        config: Qdrant 连接配置。``url=":memory:"`` 创建内存实例。
        embeddings: LangChain ``Embeddings`` 实例（用于向量化 + 推断集合维度）。

    Returns:
        ``QdrantVectorStore`` 实例，集合已就绪可直接读写。

    Raises:
        VectorStoreError: ``qdrant_client`` / ``langchain_qdrant`` 未安装，
            或连接 Qdrant 服务失败。
    """

    try:
        from langchain_qdrant import QdrantVectorStore
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
    except ImportError as exc:
        msg = f"无法导入 qdrant_client / langchain_qdrant，请确认已安装 langchain-qdrant。原始错误：{exc}"
        raise VectorStoreError(msg) from exc

    try:
        # ":memory:" 用位置参数创建纯内存 Qdrant（测试用，无需 Docker）
        if config.url == ":memory:":
            client = QdrantClient(":memory:")
        else:
            client = QdrantClient(url=config.url, api_key=config.api_key)
    except Exception as exc:
        msg = f"创建 QdrantClient 失败：{exc}"
        raise VectorStoreError(msg) from exc

    # 确保集合存在：不存在则按 embedding 维度创建（Cosine 距离）
    try:
        client.get_collection(collection_name=config.collection_name)
    except Exception:
        vector_size = len(embeddings.embed_query("test"))
        client.create_collection(
            collection_name=config.collection_name,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

    try:
        return QdrantVectorStore(
            client=client,
            collection_name=config.collection_name,
            embedding=embeddings,
            validate_collection_config=False,  # 已手动创建集合，跳过校验
        )
    except Exception as exc:
        msg = f"创建 QdrantVectorStore 失败（可能维度不匹配）：{exc}"
        raise VectorStoreError(msg) from exc


def upsert_chunks(
    store: QdrantVectorStore,
    document_id: uuid.UUID,
    document_name: str,
    chunks: Sequence[Chunk],
) -> list[str]:
    """把 ORM Chunk 列表向量化并写入 Qdrant。

    用 ``Chunk.id``（UUID）作为 Qdrant point ID，返回的 ``vector_id`` 列表
    与 ``str(chunk.id)`` 一致。调用方应在调用后把 ``vector_id`` 写回 DB。

    Args:
        store: ``QdrantVectorStore`` 实例。
        document_id: 所属文档 UUID。
        document_name: 文档原始文件名（存入 payload 供展示）。
        chunks: ORM ``Chunk`` 列表（需已 flush，``id`` 非 None）。

    Returns:
        向量 ID 列表（与 ``chunks`` 顺序一致，值为 ``str(chunk.id)``）。

    Raises:
        VectorStoreError: 写入失败。
    """

    if not chunks:
        return []

    from langchain_core.documents import Document

    documents: list[Document] = [
        Document(
            page_content=chunk.content,
            metadata={
                "document_id": str(document_id),
                "document_name": document_name,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "chunk_index": chunk.chunk_index,
            },
        )
        for chunk in chunks
    ]
    ids = [str(chunk.id) for chunk in chunks]

    try:
        store.add_documents(documents, ids=ids)
    except Exception as exc:
        msg = f"写入 Qdrant 向量失败：{exc}"
        raise VectorStoreError(msg) from exc

    return ids


def delete_by_document(
    store: QdrantVectorStore,
    document_id: uuid.UUID,
) -> None:
    """按 ``document_id`` 删除 Qdrant 中该文档的所有向量。

    用 Payload 过滤删除（``metadata.document_id`` 匹配），比遍历 vector_id
    删除更可靠：即使 DB 与 Qdrant 短暂不一致，只要 payload 里的 document_id
    正确就能清理干净（满足"删除文档后无残留向量"验收）。

    Args:
        store: ``QdrantVectorStore`` 实例。
        document_id: 要清理的文档 UUID。

    Raises:
        VectorStoreError: 删除失败。
    """

    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
    except ImportError as exc:
        msg = f"无法导入 qdrant_client.models，请确认已安装 qdrant-client。原始错误：{exc}"
        raise VectorStoreError(msg) from exc

    try:
        store.client.delete(
            collection_name=store.collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="metadata.document_id",
                        match=MatchValue(value=str(document_id)),
                    )
                ]
            ),
        )
    except Exception as exc:
        msg = f"按 document_id 删除 Qdrant 向量失败：{exc}"
        raise VectorStoreError(msg) from exc


def search(
    store: QdrantVectorStore,
    query: str,
    document_ids: Sequence[uuid.UUID] | None = None,
    top_k: int = 8,
) -> list[QdrantSearchResult]:
    """Qdrant 相似度搜索，支持按 ``document_ids`` 过滤。

    Args:
        store: ``QdrantVectorStore`` 实例。
        query: 查询文本。
        document_ids: 限定检索的文档 UUID 列表。``None`` 或空列表表示不过滤
            （检索全库）。
        top_k: 返回的最相关片段数。

    Returns:
        检索结果列表，按相似度降序。

    Raises:
        VectorStoreError: 检索失败或 ``top_k`` 非正。
    """

    if top_k <= 0:
        msg = f"top_k 必须为正整数，收到 {top_k}"
        raise VectorStoreError(msg)

    filter_obj = None
    if document_ids:
        try:
            from qdrant_client.models import FieldCondition, Filter, MatchAny
        except ImportError as exc:
            msg = f"无法导入 qdrant_client.models。原始错误：{exc}"
            raise VectorStoreError(msg) from exc

        filter_obj = Filter(
            must=[
                FieldCondition(
                    key="metadata.document_id",
                    match=MatchAny(any=[str(doc_id) for doc_id in document_ids]),
                )
            ]
        )

    try:
        results: list[tuple[Document, float]] = store.similarity_search_with_score(
            query, k=top_k, filter=filter_obj
        )
    except Exception as exc:
        msg = f"Qdrant 检索失败：{exc}"
        raise VectorStoreError(msg) from exc

    search_results: list[QdrantSearchResult] = []
    for doc, score in results:
        meta = doc.metadata
        try:
            doc_id = uuid.UUID(meta["document_id"])
        except (KeyError, ValueError) as exc:
            msg = f"Qdrant 结果 payload 缺少合法 document_id：{meta}。原始错误：{exc}"
            raise VectorStoreError(msg) from exc

        search_results.append(
            QdrantSearchResult(
                document_id=doc_id,
                document_name=meta.get("document_name", ""),
                start_page=meta.get("start_page", 0),
                end_page=meta.get("end_page", 0),
                chunk_index=meta.get("chunk_index", 0),
                content=doc.page_content,
                score=score,
            )
        )

    return search_results
