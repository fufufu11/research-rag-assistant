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

from typing import TYPE_CHECKING, ClassVar

import pytest
from langchain_core.embeddings import Embeddings

from research_rag.chunker import Chunk
from research_rag.embedding import (
    DASHSCOPE_DEFAULT_BASE_URL,
    DASHSCOPE_DEFAULT_DIMENSIONS,
    DASHSCOPE_DEFAULT_MODEL,
    DASHSCOPE_MAX_BATCH_SIZE,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_PROVIDER,
    DEFAULT_TOP_K,
    JINA_DEFAULT_BASE_URL,
    JINA_DEFAULT_DIMENSIONS,
    JINA_DEFAULT_MODEL,
    JINA_MAX_BATCH_SIZE,
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
    from pathlib import Path


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
    """默认配置应为 bge-small-zh-v1.5（中文优化，生产面向中文用户）。"""
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


# ---------------------------------------------------------------------------
# create_embeddings：dashscope API 模式（阶段 8.4）
# ---------------------------------------------------------------------------


class _MockOpenAIEmbeddings:
    """记录构造参数的 ``OpenAIEmbeddings`` 替身，用于验证 API 模式传参。

    不发真实 HTTP 请求，``embed_documents`` / ``embed_query`` 返回占位向量。
    """

    instances: ClassVar[list[_MockOpenAIEmbeddings]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs: dict[str, object] = dict(kwargs)
        _MockOpenAIEmbeddings.instances.append(self)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        del text  # 满足 Embeddings 协议签名，参数本身不使用
        return [0.0]


@pytest.fixture
def _reset_mock_embeddings() -> None:
    """每个测试前清空 Mock 实例记录，避免相互污染。"""
    _MockOpenAIEmbeddings.instances.clear()


def test_embedding_config_default_provider_is_local() -> None:
    """默认 EmbeddingConfig 应为本地模式（向后兼容）。"""
    config = EmbeddingConfig()
    assert config.provider == DEFAULT_EMBEDDING_PROVIDER
    assert config.provider == "local"
    assert config.api_key == ""
    assert config.base_url == ""
    assert config.dimensions == 0
    assert config.batch_size == 0


def test_create_embeddings_dashscope_missing_api_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider=dashscope 但 API Key 缺失（config 与环境变量均空）时应抛错。"""
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    config = EmbeddingConfig(provider="dashscope", model_name="text-embedding-v4")
    with pytest.raises(EmbeddingServiceError, match="API Key"):
        create_embeddings(config)


def test_create_embeddings_dashscope_missing_langchain_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider=dashscope 但 langchain_openai 不可导入时应抛 EmbeddingServiceError。"""
    import sys

    monkeypatch.setitem(sys.modules, "langchain_openai", None)
    config = EmbeddingConfig(provider="dashscope", api_key="sk-test")
    with pytest.raises(EmbeddingServiceError, match="langchain_openai"):
        create_embeddings(config)


def test_create_embeddings_dashscope_uses_config_api_key(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
) -> None:
    """config.api_key 非空时优先使用，并按 dashscope 默认填充 base_url/dimensions/batch。"""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    config = EmbeddingConfig(
        provider="dashscope", model_name="text-embedding-v4", api_key="sk-test"
    )
    emb = create_embeddings(config)

    assert isinstance(emb, _MockOpenAIEmbeddings)
    assert emb.kwargs["api_key"] == "sk-test"
    assert emb.kwargs["model"] == "text-embedding-v4"
    assert emb.kwargs["base_url"] == DASHSCOPE_DEFAULT_BASE_URL
    assert emb.kwargs["dimensions"] == DASHSCOPE_DEFAULT_DIMENSIONS
    assert emb.kwargs["chunk_size"] == DASHSCOPE_MAX_BATCH_SIZE
    assert emb.kwargs["check_embedding_ctx_length"] is False


def test_create_embeddings_dashscope_uses_env_api_key(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
) -> None:
    """config.api_key 为空时应从 DASHSCOPE_API_KEY 环境变量读取。"""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env")
    config = EmbeddingConfig(provider="dashscope")  # api_key 与 model_name 均空

    emb = create_embeddings(config)

    assert emb.kwargs["api_key"] == "sk-env"
    assert emb.kwargs["model"] == DASHSCOPE_DEFAULT_MODEL  # 默认 text-embedding-v4


def test_create_embeddings_dashscope_reads_api_key_from_file(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
    tmp_path: Path,
) -> None:
    """``DASHSCOPE_API_KEY_FILE`` 指向文件时优先读文件内容（覆盖 env）。

    阶段 11.6 切片 C：embedding 模块在 ``config.api_key`` 为空时从环境变量
    fallback 读取，该 fallback 路径也支持 ``_FILE`` 后缀挂载 docker secrets。
    """

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)

    key_file = tmp_path / "dashscope_key.txt"
    key_file.write_text("sk-from-file\n", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY_FILE", str(key_file))
    # 即使环境变量也设置了，_FILE 优先
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-env-should-be-ignored")

    config = EmbeddingConfig(provider="dashscope")  # api_key 为空，触发 fallback
    emb = create_embeddings(config)

    assert emb.kwargs["api_key"] == "sk-from-file"


def test_create_embeddings_dashscope_custom_dimensions_and_batch(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
) -> None:
    """config 显式指定 dimensions/batch_size 时应覆盖 dashscope 默认。"""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)
    config = EmbeddingConfig(provider="dashscope", api_key="sk-test", dimensions=512, batch_size=5)

    emb = create_embeddings(config)

    assert emb.kwargs["dimensions"] == 512
    assert emb.kwargs["chunk_size"] == 5


def test_create_embeddings_dashscope_custom_base_url(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
) -> None:
    """config.base_url 非空时应覆盖 dashscope 默认 endpoint。"""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)
    config = EmbeddingConfig(
        provider="dashscope",
        api_key="sk-test",
        base_url="https://custom.example.com/v1",
    )

    emb = create_embeddings(config)

    assert emb.kwargs["base_url"] == "https://custom.example.com/v1"


# ---------------------------------------------------------------------------
# create_embeddings：jina API 模式（阶段 8.4）
# ---------------------------------------------------------------------------


def test_embedding_config_jina_defaults_model() -> None:
    """provider=jina 时 model_name 应自动切换到 jina-embeddings-v3。"""
    config = EmbeddingConfig(provider="jina")
    assert config.model_name == JINA_DEFAULT_MODEL
    assert config.model_name == "jina-embeddings-v3"


def test_create_embeddings_jina_missing_api_key_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider=jina 但 API Key 缺失（config 与 JINA_API_KEY 均空）时应抛错。"""
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    config = EmbeddingConfig(provider="jina", model_name="jina-embeddings-v3")
    with pytest.raises(EmbeddingServiceError, match="Jina"):
        create_embeddings(config)


def test_create_embeddings_jina_uses_config_api_key(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
) -> None:
    """jina 模式应使用 config.api_key 并按 jina 默认填充 base_url/dimensions/batch。"""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    config = EmbeddingConfig(provider="jina", model_name="jina-embeddings-v3", api_key="jina-test")

    emb = create_embeddings(config)

    assert isinstance(emb, _MockOpenAIEmbeddings)
    assert emb.kwargs["api_key"] == "jina-test"
    assert emb.kwargs["model"] == "jina-embeddings-v3"
    assert emb.kwargs["base_url"] == JINA_DEFAULT_BASE_URL
    assert emb.kwargs["dimensions"] == JINA_DEFAULT_DIMENSIONS
    assert emb.kwargs["chunk_size"] == JINA_MAX_BATCH_SIZE
    assert emb.kwargs["check_embedding_ctx_length"] is False


def test_create_embeddings_jina_uses_env_api_key(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
) -> None:
    """jina 模式 config.api_key 为空时应从 JINA_API_KEY 环境变量读取。"""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)
    monkeypatch.setenv("JINA_API_KEY", "jina-env")
    config = EmbeddingConfig(provider="jina")

    emb = create_embeddings(config)

    assert emb.kwargs["api_key"] == "jina-env"
    assert emb.kwargs["model"] == JINA_DEFAULT_MODEL
    assert emb.kwargs["base_url"] == JINA_DEFAULT_BASE_URL


def test_create_embeddings_jina_reads_api_key_from_file(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
    tmp_path: Path,
) -> None:
    """``JINA_API_KEY_FILE`` 指向文件时优先读文件内容（覆盖 env）。"""

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)

    key_file = tmp_path / "jina_key.txt"
    key_file.write_text("jina-from-file\n", encoding="utf-8")
    monkeypatch.setenv("JINA_API_KEY_FILE", str(key_file))
    # 即使环境变量也设置了，_FILE 优先
    monkeypatch.setenv("JINA_API_KEY", "jina-env-should-be-ignored")

    config = EmbeddingConfig(provider="jina")  # api_key 为空，触发 fallback
    emb = create_embeddings(config)

    assert emb.kwargs["api_key"] == "jina-from-file"


def test_create_embeddings_jina_does_not_use_dashscope_env(
    monkeypatch: pytest.MonkeyPatch,
    _reset_mock_embeddings: None,
) -> None:
    """jina 模式应忽略 DASHSCOPE_API_KEY，只读 JINA_API_KEY。"""
    import langchain_openai

    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", _MockOpenAIEmbeddings)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-ignored")
    monkeypatch.delenv("JINA_API_KEY", raising=False)
    config = EmbeddingConfig(provider="jina")

    with pytest.raises(EmbeddingServiceError, match="JINA_API_KEY"):
        create_embeddings(config)
