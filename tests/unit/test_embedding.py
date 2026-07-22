"""Embedding 适配器与向量检索单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.1 节、阶段 3 验收）：
- create_embeddings：依赖缺失时抛 EmbeddingServiceError（Mock 外部导入）
- index_chunks：元数据保留、空列表、多 chunk 索引
- retrieve：返回数量、按分数降序、元数据正确、空 store、top_k 非正
- retrieve 语义相关性：查询与共享字符的 chunk 排名靠前
- RetrievalResult 不可变
- EmbeddingConfig 默认值

外部模型调用全部通过 FakeEmbeddings（确定性字符袋向量）Mock，CI 无需安装 torch。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from langchain_core.embeddings import Embeddings

from research_rag.chunker import Chunk
from research_rag.embedding import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_TOP_K,
    EmbeddingConfig,
    EmbeddingServiceError,
    RetrievalResult,
    VectorStoreError,
    create_embeddings,
    index_chunks,
    retrieve,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


# ---------------------------------------------------------------------------
# 辅助：确定性 FakeEmbeddings（Mock 外部模型调用）
# ---------------------------------------------------------------------------


class FakeEmbeddings(Embeddings):
    """确定性 Fake Embeddings，用于单元测试。

    按字符袋生成向量：对文本中每个字符，在对应维度（``ord(char) % dim``）+1，
    最后 L2 归一化。相同文本生成相同向量；共享字符的文本余弦相似度更高。
    不依赖任何外部模型，CI 无需安装 torch/sentence-transformers。
    """

    def __init__(self, dim: int = 32) -> None:
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for char in text:
            vec[ord(char) % self.dim] += 1.0
        norm = sum(v * v for v in vec) ** 0.5
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


def _make_chunks() -> list[Chunk]:
    """构造一组用于测试的 Chunk（水果 vs 电脑主题）。"""
    return [
        Chunk(
            start_page=1,
            end_page=1,
            chunk_index=0,
            content="苹果香蕉橙子葡萄西瓜，这些都是常见的水果种类。",
            char_count=24,
        ),
        Chunk(
            start_page=1,
            end_page=1,
            chunk_index=1,
            content="电脑键盘鼠标显示器，这些是计算机的外部设备。",
            char_count=22,
        ),
        Chunk(
            start_page=2,
            end_page=2,
            chunk_index=2,
            content="向量检索是信息检索的重要方法，通过余弦相似度匹配。",
            char_count=24,
        ),
    ]


# ---------------------------------------------------------------------------
# create_embeddings 测试
# ---------------------------------------------------------------------------


def test_create_embeddings_raises_on_missing_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未安装 sentence-transformers 时应抛 EmbeddingServiceError。"""
    import sys

    # 模拟 sentence_transformers 未安装：sys.modules 中设为 None 会让 import 抛 ImportError
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(EmbeddingServiceError, match="sentence-transformers"):
        create_embeddings()


def test_create_embeddings_raises_on_missing_langchain_huggingface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未安装 langchain-huggingface 时应抛 EmbeddingServiceError。"""
    import sys

    # 模拟 langchain_huggingface 不可导入
    monkeypatch.setitem(sys.modules, "langchain_huggingface", None)

    with pytest.raises(EmbeddingServiceError, match="langchain_huggingface"):
        create_embeddings()


# ---------------------------------------------------------------------------
# EmbeddingConfig 测试
# ---------------------------------------------------------------------------


def test_embedding_config_default_values() -> None:
    """默认配置应符合 PROJECT_PLAN 第 9.2 节。"""
    config = EmbeddingConfig()
    assert config.model_name == DEFAULT_EMBEDDING_MODEL
    assert config.model_name == "BAAI/bge-small-zh-v1.5"


def test_embedding_config_custom_model() -> None:
    """自定义模型名应生效。"""
    config = EmbeddingConfig(model_name="some-other-model")
    assert config.model_name == "some-other-model"


# ---------------------------------------------------------------------------
# index_chunks 测试
# ---------------------------------------------------------------------------


def test_index_chunks_returns_store_with_correct_count() -> None:
    """index_chunks 应返回包含所有 chunk 的向量存储。"""
    chunks = _make_chunks()
    store = index_chunks(chunks, FakeEmbeddings())

    # 检索所有文档验证数量
    results = retrieve(store, "测试查询", top_k=10)
    assert len(results) == len(chunks)


def test_index_chunks_empty_list() -> None:
    """空 chunk 列表应返回空 store，不抛异常。"""
    store = index_chunks([], FakeEmbeddings())
    results = retrieve(store, "任意查询", top_k=5)
    assert results == []


def test_index_chunks_preserves_metadata() -> None:
    """索引后检索应保留 start_page/end_page 和 chunk_index 元数据。"""
    chunks = _make_chunks()
    store = index_chunks(chunks, FakeEmbeddings())

    results = retrieve(store, "水果", top_k=10)
    # 每条结果的 start_page/end_page 和 chunk_index 应在原始 chunk 范围内
    valid_start_pages = {c.start_page for c in chunks}
    valid_indices = {c.chunk_index for c in chunks}
    for r in results:
        assert r.start_page in valid_start_pages
        assert r.end_page in valid_start_pages
        assert r.chunk_index in valid_indices


# ---------------------------------------------------------------------------
# retrieve 测试
# ---------------------------------------------------------------------------


def test_retrieve_returns_top_k_results() -> None:
    """retrieve 应返回不超过 top_k 条结果。"""
    chunks = _make_chunks()
    store = index_chunks(chunks, FakeEmbeddings())

    results = retrieve(store, "水果", top_k=2)
    assert len(results) == 2

    results_all = retrieve(store, "水果", top_k=10)
    assert len(results_all) == len(chunks)


def test_retrieve_sorted_by_score_descending() -> None:
    """检索结果应按余弦相似度降序排列（分数越高越相关）。"""
    chunks = _make_chunks()
    store = index_chunks(chunks, FakeEmbeddings())

    results = retrieve(store, "苹果水果", top_k=3)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_retrieve_semantic_relevance() -> None:
    """查询与某 chunk 共享更多字符时，该 chunk 应排名靠前。

    FakeEmbeddings 按字符袋生成向量，共享字符越多余弦相似度越高。
    "苹果水果"与水果 chunk 共享"苹/果/水/果"等字符，应排名第一。
    """
    chunks = _make_chunks()
    store = index_chunks(chunks, FakeEmbeddings())

    results = retrieve(store, "苹果水果", top_k=3)
    assert len(results) > 0
    top = results[0]
    assert "水果" in top.content or "苹果" in top.content


def test_retrieve_empty_store() -> None:
    """空 store 检索应返回空列表。"""
    store = index_chunks([], FakeEmbeddings())
    results = retrieve(store, "任何查询", top_k=5)
    assert results == []


def test_retrieve_invalid_top_k_raises() -> None:
    """top_k 非正时应抛 VectorStoreError。"""
    chunks = _make_chunks()
    store = index_chunks(chunks, FakeEmbeddings())

    with pytest.raises(VectorStoreError, match="top_k"):
        retrieve(store, "查询", top_k=0)

    with pytest.raises(VectorStoreError, match="top_k"):
        retrieve(store, "查询", top_k=-1)


def test_retrieve_content_matches_chunk() -> None:
    """检索结果的 content 应与原始 chunk 的 content 一致。"""
    chunks = _make_chunks()
    store = index_chunks(chunks, FakeEmbeddings())

    results = retrieve(store, "水果", top_k=10)
    original_contents = {c.content for c in chunks}
    for r in results:
        assert r.content in original_contents


def test_retrieve_default_top_k() -> None:
    """默认 top_k 应为 DEFAULT_TOP_K（8）。"""
    assert DEFAULT_TOP_K == 8


# ---------------------------------------------------------------------------
# RetrievalResult 测试
# ---------------------------------------------------------------------------


def test_retrieval_result_is_frozen() -> None:
    """RetrievalResult 应为不可变 dataclass。"""
    result = RetrievalResult(
        start_page=1,
        end_page=1,
        chunk_index=0,
        content="测试内容",
        score=0.95,
    )
    with pytest.raises(AttributeError):
        result.score = 0.50  # type: ignore[misc]


def test_retrieval_result_fields() -> None:
    """RetrievalResult 字段应正确赋值。"""
    result = RetrievalResult(
        start_page=3,
        end_page=3,
        chunk_index=5,
        content="一段文本",
        score=0.88,
    )
    assert result.start_page == 3
    assert result.end_page == 3
    assert result.chunk_index == 5
    assert result.content == "一段文本"
    assert result.score == pytest.approx(0.88)


# ---------------------------------------------------------------------------
# 端到端：chunk_pages → index_chunks → retrieve
# ---------------------------------------------------------------------------


def test_end_to_end_chunk_pages_to_retrieve() -> None:
    """端到端：从 Chunk 列表索引并检索，元数据应正确溯源。"""
    chunks: Sequence[Chunk] = [
        Chunk(
            start_page=1,
            end_page=1,
            chunk_index=0,
            content="注意力机制是深度学习的核心。",
            char_count=16,
        ),
        Chunk(
            start_page=1,
            end_page=1,
            chunk_index=1,
            content="梯度下降用于优化神经网络参数。",
            char_count=16,
        ),
        Chunk(
            start_page=2,
            end_page=2,
            chunk_index=2,
            content="余弦相似度衡量向量方向差异。",
            char_count=15,
        ),
    ]

    store = index_chunks(chunks, FakeEmbeddings())
    results = retrieve(store, "深度学习注意力", top_k=3)

    assert len(results) == 3
    # 第一条应与查询最相关（共享"深度学习/注意力"字符）
    assert "注意力" in results[0].content
    # 所有结果的 start_page/end_page 和 chunk_index 在有效范围内
    for r in results:
        assert r.start_page in (1, 2)
        assert r.end_page in (1, 2)
        assert r.chunk_index in (0, 1, 2)
