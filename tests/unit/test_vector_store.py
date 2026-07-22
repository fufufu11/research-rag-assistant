"""Qdrant 向量存储适配器单元测试。

测试覆盖（PROJECT_PLAN.md 第 716-722 行、阶段 6 验收）：
- ``QdrantConfig``：默认值、自定义值、``get_qdrant_config`` 从环境变量读
- ``create_vector_store``：正常创建、依赖缺失抛异常、连接失败抛异常
- ``upsert_chunks``：写入成功返回 vector_id、空列表、写入失败
- ``delete_by_document``：按 payload 删除、删除失败
- ``search``：正常检索、document_ids 过滤、top_k 校验、空结果
- 集成测试：用内存 Qdrant（``QdrantClient(":memory:")``）验证完整流程

测试策略：
- 用 ``qdrant_client.QdrantClient(":memory:")`` 创建纯内存 Qdrant，
  不需要 Docker 或外部服务（PROJECT_PLAN 第 13.2 节"CI 不依赖外部网络"）。
- 用 ``FakeEmbeddings``（确定性向量）避免加载真实模型。
- 集成测试验证"删除文档后无残留向量"验收标准。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from langchain_core.embeddings import FakeEmbeddings

from research_rag.db.models import Chunk
from research_rag.embedding import VectorStoreError
from research_rag.vector_store import (
    DEFAULT_QDRANT_COLLECTION,
    DEFAULT_QDRANT_URL,
    QdrantConfig,
    QdrantSearchResult,
    create_vector_store,
    delete_by_document,
    get_qdrant_config,
    search,
    upsert_chunks,
)

if TYPE_CHECKING:
    from langchain_qdrant import QdrantVectorStore


# ---------------------------------------------------------------------------
# 辅助：创建内存 QdrantVectorStore
# ---------------------------------------------------------------------------


def make_memory_store() -> QdrantVectorStore:
    """创建基于内存 Qdrant 的 QdrantVectorStore（不依赖外部服务）。

    用 ``QdrantConfig(url=":memory:")`` + ``create_vector_store`` 创建纯内存实例，
    复用生产代码的建集合逻辑。集合名用随机 UUID 避免测试间干扰。
    """

    config = QdrantConfig(url=":memory:", collection_name=f"test_{uuid.uuid4().hex[:8]}")
    embeddings = FakeEmbeddings(size=10)
    return create_vector_store(config, embeddings)


def make_chunks(document_id: uuid.UUID, count: int = 3) -> list[Chunk]:
    """构造测试用 ORM Chunk 列表（需 flush 后 id 才非 None）。"""

    return [
        Chunk(
            document_id=document_id,
            start_page=1,
            end_page=1,
            chunk_index=i,
            content=f"这是第 {i} 个测试片段，关于检索增强生成。test chunk {i}",
            char_count=30 + i,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# QdrantConfig 测试
# ---------------------------------------------------------------------------


def test_qdrant_config_default_values() -> None:
    """默认配置应符合 .env.example 值。"""

    config = QdrantConfig()
    assert config.url == DEFAULT_QDRANT_URL
    assert config.collection_name == DEFAULT_QDRANT_COLLECTION
    assert config.api_key is None


def test_qdrant_config_custom_values() -> None:
    """自定义配置应生效。"""

    config = QdrantConfig(
        url="http://qdrant.example.com:6333",
        collection_name="custom_collection",
        api_key="secret-key",
    )
    assert config.url == "http://qdrant.example.com:6333"
    assert config.collection_name == "custom_collection"
    assert config.api_key == "secret-key"


def test_get_qdrant_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_qdrant_config`` 应从环境变量读取。"""

    monkeypatch.setenv("QDRANT_URL", "http://env-qdrant:6334")
    monkeypatch.setenv("QDRANT_COLLECTION", "env_collection")
    monkeypatch.setenv("QDRANT_API_KEY", "env-key")

    config = get_qdrant_config()
    assert config.url == "http://env-qdrant:6334"
    assert config.collection_name == "env_collection"
    assert config.api_key == "env-key"


def test_get_qdrant_config_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量未设置时应返回默认值。"""

    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.delenv("QDRANT_COLLECTION", raising=False)
    monkeypatch.delenv("QDRANT_API_KEY", raising=False)

    config = get_qdrant_config()
    assert config.url == DEFAULT_QDRANT_URL
    assert config.collection_name == DEFAULT_QDRANT_COLLECTION
    assert config.api_key is None


# ---------------------------------------------------------------------------
# create_vector_store 测试
# ---------------------------------------------------------------------------


def test_create_vector_store_returns_qdrant_store() -> None:
    """正常创建应返回 QdrantVectorStore 实例。"""

    config = QdrantConfig(url=":memory:", collection_name=f"test_{uuid.uuid4().hex[:8]}")
    embeddings = FakeEmbeddings(size=10)

    store = create_vector_store(config, embeddings)

    from langchain_qdrant import QdrantVectorStore

    assert isinstance(store, QdrantVectorStore)


def test_create_vector_store_raises_on_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """依赖缺失时应抛 VectorStoreError。"""

    import sys

    monkeypatch.setitem(sys.modules, "qdrant_client", None)
    monkeypatch.setitem(sys.modules, "langchain_qdrant", None)

    config = QdrantConfig(url=":memory:")
    embeddings = FakeEmbeddings(size=10)

    with pytest.raises(VectorStoreError, match="无法导入"):
        create_vector_store(config, embeddings)


# ---------------------------------------------------------------------------
# upsert_chunks 测试
# ---------------------------------------------------------------------------


def test_upsert_chunks_returns_vector_ids() -> None:
    """写入成功应返回 vector_id 列表（与 chunk.id 一致）。"""

    store = make_memory_store()
    doc_id = uuid.uuid4()
    chunks = make_chunks(doc_id, count=3)

    # 模拟 flush：手动设置 chunk.id（测试中 ORM 未 flush）
    for chunk in chunks:
        chunk.id = uuid.uuid4()

    vector_ids = upsert_chunks(store, doc_id, "test.pdf", chunks)

    assert len(vector_ids) == 3
    assert all(vid == str(chunk.id) for vid, chunk in zip(vector_ids, chunks, strict=True))


def test_upsert_chunks_empty_list_returns_empty() -> None:
    """空 Chunk 列表应返回空列表，不调 Qdrant。"""

    store = make_memory_store()
    doc_id = uuid.uuid4()

    vector_ids = upsert_chunks(store, doc_id, "test.pdf", [])

    assert vector_ids == []


def test_upsert_chunks_raises_on_write_failure() -> None:
    """写入失败应抛 VectorStoreError。"""

    from unittest.mock import MagicMock

    store = MagicMock()
    store.add_documents.side_effect = RuntimeError("Qdrant 写入失败")

    doc_id = uuid.uuid4()
    chunks = make_chunks(doc_id, count=1)
    chunks[0].id = uuid.uuid4()

    with pytest.raises(VectorStoreError, match="写入 Qdrant 向量失败"):
        upsert_chunks(store, doc_id, "test.pdf", chunks)


# ---------------------------------------------------------------------------
# delete_by_document 测试
# ---------------------------------------------------------------------------


def test_delete_by_document_succeeds() -> None:
    """删除成功不应抛异常。"""

    store = make_memory_store()
    doc_id = uuid.uuid4()
    chunks = make_chunks(doc_id, count=2)
    for chunk in chunks:
        chunk.id = uuid.uuid4()

    upsert_chunks(store, doc_id, "test.pdf", chunks)

    # 删除不应抛异常
    delete_by_document(store, doc_id)

    # 验证已删除：检索应返回空
    results = search(store, "测试查询", document_ids=[doc_id], top_k=10)
    assert results == []


def test_delete_by_document_raises_on_failure() -> None:
    """删除失败应抛 VectorStoreError。"""

    from unittest.mock import MagicMock

    store = MagicMock()
    store.client.delete.side_effect = RuntimeError("Qdrant 删除失败")
    store.collection_name = "test"

    with pytest.raises(VectorStoreError, match="按 document_id 删除 Qdrant 向量失败"):
        delete_by_document(store, uuid.uuid4())


# ---------------------------------------------------------------------------
# search 测试
# ---------------------------------------------------------------------------


def test_search_returns_results() -> None:
    """检索应返回结果列表，按相似度降序。"""

    store = make_memory_store()
    doc_id = uuid.uuid4()
    chunks = make_chunks(doc_id, count=3)
    for chunk in chunks:
        chunk.id = uuid.uuid4()

    upsert_chunks(store, doc_id, "test.pdf", chunks)

    results = search(store, "检索增强生成", document_ids=[doc_id], top_k=3)

    assert len(results) <= 3
    assert all(isinstance(r, QdrantSearchResult) for r in results)
    assert all(r.document_id == doc_id for r in results)
    # 结果应按 score 降序（Qdrant similarity_search_with_score 默认行为）
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_filters_by_document_ids() -> None:
    """``document_ids`` 过滤应只返回指定文档的结果。"""

    store = make_memory_store()
    doc1_id = uuid.uuid4()
    doc2_id = uuid.uuid4()

    chunks1 = make_chunks(doc1_id, count=2)
    chunks2 = make_chunks(doc2_id, count=2)
    for chunk in chunks1 + chunks2:
        chunk.id = uuid.uuid4()

    upsert_chunks(store, doc1_id, "doc1.pdf", chunks1)
    upsert_chunks(store, doc2_id, "doc2.pdf", chunks2)

    # 只检索 doc1
    results = search(store, "检索增强生成", document_ids=[doc1_id], top_k=10)

    assert len(results) > 0
    assert all(r.document_id == doc1_id for r in results)
    assert all(r.document_name == "doc1.pdf" for r in results)


def test_search_no_filter_returns_all() -> None:
    """``document_ids=None`` 应检索全库。"""

    store = make_memory_store()
    doc_id = uuid.uuid4()
    chunks = make_chunks(doc_id, count=2)
    for chunk in chunks:
        chunk.id = uuid.uuid4()

    upsert_chunks(store, doc_id, "test.pdf", chunks)

    results = search(store, "检索增强生成", document_ids=None, top_k=10)

    assert len(results) > 0


def test_search_empty_store_returns_empty() -> None:
    """空库检索应返回空列表。"""

    store = make_memory_store()

    results = search(store, "不存在的查询", document_ids=None, top_k=5)

    assert results == []


def test_search_invalid_top_k_raises() -> None:
    """``top_k`` 非正应抛 VectorStoreError。"""

    store = make_memory_store()

    with pytest.raises(VectorStoreError, match="top_k 必须为正整数"):
        search(store, "查询", top_k=0)

    with pytest.raises(VectorStoreError, match="top_k 必须为正整数"):
        search(store, "查询", top_k=-1)


# ---------------------------------------------------------------------------
# 集成测试：完整流程（验收"删除文档后无残留向量"）
# ---------------------------------------------------------------------------


def test_upload_delete_no_residual_vectors() -> None:
    """验收测试：上传文档 → 检索有结果 → 删除文档 → 检索无结果（无残留向量）。

    对应 PROJECT_PLAN.md 第 722 行验收标准"删除文档后无残留向量"。
    """

    store = make_memory_store()
    doc_id = uuid.uuid4()
    chunks = make_chunks(doc_id, count=3)
    for chunk in chunks:
        chunk.id = uuid.uuid4()

    # 1. 上传（写入向量）
    upsert_chunks(store, doc_id, "paper.pdf", chunks)

    # 2. 检索应有结果
    results_before = search(store, "检索增强生成", document_ids=[doc_id], top_k=10)
    assert len(results_before) > 0

    # 3. 删除文档
    delete_by_document(store, doc_id)

    # 4. 检索应无结果（无残留向量）
    results_after = search(store, "检索增强生成", document_ids=[doc_id], top_k=10)
    assert results_after == []

    # 5. 全库检索也不应返回该文档的结果
    results_all = search(store, "检索增强生成", document_ids=None, top_k=100)
    assert all(r.document_id != doc_id for r in results_all)
