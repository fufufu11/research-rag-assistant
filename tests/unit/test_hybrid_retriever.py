"""BM25 + 向量混合检索模块单元测试（阶段 8.3）。

测试覆盖：
- ``tokenize``：英文/中文/混合/空字符串/use_jieba 开关
- ``BM25Config``：默认值、自定义值
- ``BM25Retriever``：构造、空输入、检索、top_k、依赖缺失
- ``rrf_fusion``：两路/单路/空/top_k 截断/相同 content 合并
- ``hybrid_retrieve``：编排逻辑（向量 + BM25 + RRF 融合）
- ``get_bm25_config`` / ``is_bm25_enabled``：环境变量

测试策略：
- 用真实 ``rank_bm25``（轻量纯 Python，无 torch 依赖）
- 用 ``FakeEmbeddings``（确定性字符袋向量）建向量索引
- ``jieba`` 可选：测试 ``use_jieba=False`` 强制 fallback 路径，确保 CI 不依赖 jieba
"""

from __future__ import annotations

import pytest
from langchain_core.embeddings import Embeddings

from research_rag.chunker import Chunk
from research_rag.embedding import RetrievalResult, index_chunks
from research_rag.hybrid_retriever import (
    DEFAULT_RRF_K,
    BM25Config,
    BM25Retriever,
    HybridRetrievalError,
    get_bm25_config,
    is_bm25_enabled,
    rrf_fusion,
    tokenize,
)

# ---------------------------------------------------------------------------
# 辅助：确定性 FakeEmbeddings（与 test_embedding.py 一致）
# ---------------------------------------------------------------------------


class FakeEmbeddings(Embeddings):
    """确定性 Fake Embeddings，按字符袋生成向量。"""

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
    """构造测试用 Chunk 列表（水果/电脑/检索 三个主题）。"""
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


def _make_result(content: str, score: float, chunk_index: int = 0) -> RetrievalResult:
    """构造 ``RetrievalResult`` 辅助函数。"""
    return RetrievalResult(
        start_page=1,
        end_page=1,
        chunk_index=chunk_index,
        content=content,
        score=score,
    )


# ---------------------------------------------------------------------------
# tokenize 测试
# ---------------------------------------------------------------------------


class TestTokenize:
    """分词函数测试。"""

    def test_english_text(self) -> None:
        """英文文本按 \\w+ 切分。"""
        tokens = tokenize("hello world attention mechanism")
        assert "hello" in tokens
        assert "world" in tokens
        assert "attention" in tokens
        assert "mechanism" in tokens

    def test_english_with_punctuation(self) -> None:
        """英文标点不作为 token 一部分。"""
        tokens = tokenize("hello, world! attention-mechanism")
        # 标点被丢弃，单词保留
        assert "hello" in tokens
        assert "world" in tokens
        assert "attention" in tokens
        assert "mechanism" in tokens
        assert "," not in tokens
        assert "!" not in tokens

    def test_empty_string(self) -> None:
        """空字符串返回空列表。"""
        assert tokenize("") == []

    def test_numbers_preserved(self) -> None:
        """数字作为 token 保留。"""
        tokens = tokenize("model has 100 parameters and 3 layers")
        assert "100" in tokens
        assert "3" in tokens

    def test_use_jieba_false_forces_fallback(self) -> None:
        """``use_jieba=False`` 时强制走 fallback 路径（字符级切分）。"""
        # 含 CJK 字符，use_jieba=False 应走字符切分
        tokens = tokenize("苹果水果", use_jieba=False)
        # 字符级切分：每个 CJK 字符是一个 token
        assert "苹" in tokens
        assert "果" in tokens
        assert "水" in tokens
        assert "果" in tokens

    def test_mixed_chinese_english(self) -> None:
        """中英文混合文本，两种字符都能被切分。"""
        # use_jieba=False 强制 fallback，避免依赖 jieba 是否安装
        tokens = tokenize("使用transformer模型", use_jieba=False)
        # CJK 字符单字切分，英文单词合并
        assert "使" in tokens
        assert "transformer" in tokens
        assert "模" in tokens
        assert "型" in tokens


# ---------------------------------------------------------------------------
# BM25Config 测试
# ---------------------------------------------------------------------------


class TestBM25Config:
    """BM25 配置测试。"""

    def test_default_values(self) -> None:
        """默认值符合 BM25 经典参数。"""
        config = BM25Config()
        assert config.k1 == 1.5
        assert config.b == 0.75
        assert config.use_jieba is True

    def test_custom_values(self) -> None:
        """自定义参数。"""
        config = BM25Config(k1=2.0, b=0.5, use_jieba=False)
        assert config.k1 == 2.0
        assert config.b == 0.5
        assert config.use_jieba is False

    def test_frozen(self) -> None:
        """BM25Config 不可变。"""
        config = BM25Config()
        with pytest.raises(AttributeError):
            config.k1 = 2.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BM25Retriever 测试
# ---------------------------------------------------------------------------


class TestBM25Retriever:
    """BM25 检索器测试。"""

    def test_construct_normal(self) -> None:
        """正常构造不抛异常。"""
        retriever = BM25Retriever(_make_chunks())
        assert retriever.config.k1 == 1.5

    def test_construct_with_custom_config(self) -> None:
        """自定义配置构造。"""
        config = BM25Config(k1=2.0, b=0.5, use_jieba=False)
        retriever = BM25Retriever(_make_chunks(), config=config)
        assert retriever.config.k1 == 2.0
        assert retriever.config.b == 0.5

    def test_construct_empty_chunks(self) -> None:
        """空 chunk 列表：不抛异常，retrieve 返回空。"""
        retriever = BM25Retriever([])
        assert retriever.retrieve("任意查询", top_k=5) == []

    def test_retrieve_returns_retrieval_result(self) -> None:
        """retrieve 返回 RetrievalResult 列表。"""
        retriever = BM25Retriever(_make_chunks(), config=BM25Config(use_jieba=False))
        results = retriever.retrieve("水果", top_k=2)
        assert len(results) > 0
        assert all(isinstance(r, RetrievalResult) for r in results)
        # 检索到的结果应包含"水果"相关 chunk
        contents = [r.content for r in results]
        assert any("水果" in c for c in contents)

    def test_retrieve_top_k_limit(self) -> None:
        """top_k 限制返回数量。"""
        retriever = BM25Retriever(_make_chunks(), config=BM25Config(use_jieba=False))
        results = retriever.retrieve("水果", top_k=1)
        assert len(results) <= 1

    def test_retrieve_sorted_by_score_descending(self) -> None:
        """结果按 BM25 分数降序排列。"""
        retriever = BM25Retriever(_make_chunks(), config=BM25Config(use_jieba=False))
        results = retriever.retrieve("水果 苹果", top_k=3)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_retrieve_no_match_returns_empty(self) -> None:
        """query 无共同词时返回空列表（BM25 分数 <= 0 被过滤）。"""
        retriever = BM25Retriever(
            [Chunk(1, 1, 0, "苹果香蕉", 4)], config=BM25Config(use_jieba=False)
        )
        # query 用完全无关的词
        results = retriever.retrieve("zzzxxxyyy", top_k=5)
        assert results == []

    def test_retrieve_preserves_chunk_metadata(self) -> None:
        """检索结果保留 start_page/end_page/chunk_index。"""
        chunks = _make_chunks()
        retriever = BM25Retriever(chunks, config=BM25Config(use_jieba=False))
        results = retriever.retrieve("水果", top_k=10)
        valid_indices = {c.chunk_index for c in chunks}
        valid_pages = {c.start_page for c in chunks}
        for r in results:
            assert r.chunk_index in valid_indices
            assert r.start_page in valid_pages
            assert r.end_page in valid_pages

    def test_retrieve_top_k_non_positive_raises(self) -> None:
        """top_k <= 0 抛 HybridRetrievalError。"""
        retriever = BM25Retriever(_make_chunks(), config=BM25Config(use_jieba=False))
        with pytest.raises(HybridRetrievalError, match="top_k"):
            retriever.retrieve("查询", top_k=0)

        with pytest.raises(HybridRetrievalError, match="top_k"):
            retriever.retrieve("查询", top_k=-1)

    def test_retrieve_empty_query_returns_empty(self) -> None:
        """空 query 或全标点 query 返回空。"""
        retriever = BM25Retriever(_make_chunks(), config=BM25Config(use_jieba=False))
        assert retriever.retrieve("", top_k=5) == []
        # 纯标点无法分词
        assert retriever.retrieve("！！！？？？", top_k=5) == []

    def test_construct_dependency_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """rank_bm25 未安装时抛 HybridRetrievalError。"""
        # 模拟 rank_bm25 导入失败
        import builtins

        original_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "rank_bm25":
                raise ImportError("模拟 rank_bm25 未安装")
            return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises(HybridRetrievalError, match="无法导入 rank_bm25"):
            BM25Retriever(_make_chunks())

    def test_keyword_match_stronger_than_vector(self) -> None:
        """BM25 对关键词精确匹配优于向量检索（验证设计目标）。

        构造一个"术语列表"chunk，验证 BM25 能用关键词精确召回。

        Note:
            BM25Okapi 的 IDF = log((N - n + 0.5) / (n + 0.5))。当 N=2 且词
            仅出现在 1 个文档时 IDF=log(1.5/1.5)=0，导致分数为 0 被过滤。
            因此测试需至少 3 个文档让 IDF>0（与真实评测场景一致，几十 chunks）。
        """
        chunks = [
            Chunk(
                1,
                1,
                0,
                "关键词列表：attention, transformer, encoder, decoder, "
                "embedding, positional encoding",
                60,
            ),
            Chunk(
                2,
                2,
                1,
                "本论文研究自然语言处理中的序列建模方法，提出了一种新的神经网络架构。",
                30,
            ),
            # 增加不包含目标术语的 chunk，让 IDF > 0
            Chunk(
                3,
                3,
                2,
                "Deep learning models require large datasets and "
                "powerful GPUs for training optimization.",
                80,
            ),
        ]
        retriever = BM25Retriever(chunks, config=BM25Config(use_jieba=False))
        # query 含具体术语
        results = retriever.retrieve("transformer encoder decoder", top_k=2)
        # 第一个结果应是关键词列表 chunk
        assert len(results) > 0
        assert "关键词列表" in results[0].content


# ---------------------------------------------------------------------------
# rrf_fusion 测试
# ---------------------------------------------------------------------------


class TestRRFFusion:
    """RRF 融合算法测试。"""

    def test_empty_inputs(self) -> None:
        """两路都为空时返回空列表。"""
        assert rrf_fusion([], []) == []

    def test_only_vector_results(self) -> None:
        """仅向量有结果：融合后返回向量结果（score 更新为 RRF 分数）。"""
        vector = [_make_result("文档A", 0.9), _make_result("文档B", 0.7)]
        fused = rrf_fusion(vector, [])
        assert len(fused) == 2
        # RRF 分数 = 1/(k+1) + 0 = 1/(60+1) ≈ 0.0164
        assert abs(fused[0].score - 1.0 / (DEFAULT_RRF_K + 1)) < 1e-9
        assert fused[0].content == "文档A"

    def test_only_bm25_results(self) -> None:
        """仅 BM25 有结果：融合后返回 BM25 结果。"""
        bm25 = [_make_result("文档C", 5.0), _make_result("文档D", 3.0)]
        fused = rrf_fusion([], bm25)
        assert len(fused) == 2
        assert fused[0].content == "文档C"

    def test_union_of_both(self) -> None:
        """两路有不同结果：取并集。"""
        vector = [_make_result("A", 0.9), _make_result("B", 0.7)]
        bm25 = [_make_result("C", 5.0), _make_result("D", 3.0)]
        fused = rrf_fusion(vector, bm25)
        assert len(fused) == 4
        contents = {r.content for r in fused}
        assert contents == {"A", "B", "C", "D"}

    def test_same_content_accumulates_score(self) -> None:
        """两路有相同 content：分数累加。"""
        vector = [_make_result("共享文档", 0.9)]  # rank 1
        bm25 = [_make_result("共享文档", 5.0)]  # rank 1
        fused = rrf_fusion(vector, bm25)
        # 只有一条结果（content 去重）
        assert len(fused) == 1
        # RRF 分数 = 1/(k+1) + 1/(k+1) = 2/(k+1)
        expected = 2.0 / (DEFAULT_RRF_K + 1)
        assert abs(fused[0].score - expected) < 1e-9

    def test_top_k_truncation(self) -> None:
        """top_k 截断。"""
        vector = [_make_result(f"V{i}", 0.9) for i in range(5)]
        bm25 = [_make_result(f"B{i}", 5.0) for i in range(5)]
        fused = rrf_fusion(vector, bm25, top_k=3)
        assert len(fused) == 3

    def test_rank_affects_score(self) -> None:
        """排名靠前的结果 RRF 分数更高。"""
        # vector 中 "A" 排第 1，"B" 排第 2
        vector = [_make_result("A", 0.9), _make_result("B", 0.7)]
        bm25 = []
        fused = rrf_fusion(vector, bm25)
        # A 的 RRF 分数 = 1/(k+1)，B = 1/(k+2)，A > B
        assert fused[0].content == "A"
        assert fused[0].score > fused[1].score

    def test_custom_k(self) -> None:
        """自定义 k 值影响分数。"""
        vector = [_make_result("A", 0.9)]
        # k=1 时分数 = 1/(1+1) = 0.5
        fused = rrf_fusion(vector, [], k=1)
        assert abs(fused[0].score - 0.5) < 1e-9

    def test_score_updated_to_rrf(self) -> None:
        """融合后 score 字段更新为 RRF 分数（非原始分数）。"""
        vector = [_make_result("A", 0.999)]  # 原始余弦分数
        fused = rrf_fusion(vector, [])
        # RRF 分数应远小于 0.999
        assert fused[0].score < 0.1
        assert fused[0].score != 0.999


# ---------------------------------------------------------------------------
# hybrid_retrieve 测试（编排）
# ---------------------------------------------------------------------------


class TestHybridRetrieve:
    """混合检索编排测试。"""

    def test_returns_fused_results(self) -> None:
        """混合检索返回 RRF 融合后的结果。"""
        from research_rag.hybrid_retriever import hybrid_retrieve

        chunks = _make_chunks()
        store = index_chunks(chunks, FakeEmbeddings())
        bm25 = BM25Retriever(chunks, config=BM25Config(use_jieba=False))

        results = hybrid_retrieve(store, bm25, "水果", top_k=3)
        assert len(results) <= 3
        assert len(results) > 0
        # 至少有一个结果包含"水果"
        assert any("水果" in r.content for r in results)

    def test_top_k_limit(self) -> None:
        """top_k 限制最终返回数量。"""
        from research_rag.hybrid_retriever import hybrid_retrieve

        chunks = _make_chunks()
        store = index_chunks(chunks, FakeEmbeddings())
        bm25 = BM25Retriever(chunks, config=BM25Config(use_jieba=False))

        results = hybrid_retrieve(store, bm25, "水果", top_k=1)
        assert len(results) <= 1

    def test_empty_store_and_bm25(self) -> None:
        """空向量库和空 BM25 索引：返回空列表。"""
        from research_rag.hybrid_retriever import hybrid_retrieve

        store = index_chunks([], FakeEmbeddings())
        bm25 = BM25Retriever([])
        results = hybrid_retrieve(store, bm25, "任意查询", top_k=5)
        assert results == []


# ---------------------------------------------------------------------------
# 环境变量工厂函数测试
# ---------------------------------------------------------------------------


class TestEnvFunctions:
    """环境变量工厂函数测试。"""

    def test_get_bm25_config_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无环境变量时用默认值。"""
        monkeypatch.delenv("BM25_K1", raising=False)
        monkeypatch.delenv("BM25_B", raising=False)
        monkeypatch.delenv("BM25_USE_JIEBA", raising=False)
        config = get_bm25_config()
        assert config.k1 == 1.5
        assert config.b == 0.75
        assert config.use_jieba is True

    def test_get_bm25_config_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """从环境变量读取自定义值。"""
        monkeypatch.setenv("BM25_K1", "2.0")
        monkeypatch.setenv("BM25_B", "0.5")
        monkeypatch.setenv("BM25_USE_JIEBA", "false")
        config = get_bm25_config()
        assert config.k1 == 2.0
        assert config.b == 0.5
        assert config.use_jieba is False

    def test_get_bm25_config_invalid_float_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BM25_K1 格式错误时回退默认值。"""
        monkeypatch.setenv("BM25_K1", "not_a_number")
        config = get_bm25_config()
        assert config.k1 == 1.5

    def test_is_bm25_enabled_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认未设置时返回 False。"""
        monkeypatch.delenv("BM25_ENABLED", raising=False)
        assert is_bm25_enabled() is False

    def test_is_bm25_enabled_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """BM25_ENABLED=true 时返回 True。"""
        monkeypatch.setenv("BM25_ENABLED", "true")
        assert is_bm25_enabled() is True

    def test_is_bm25_enabled_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """大小写不敏感。"""
        monkeypatch.setenv("BM25_ENABLED", "TRUE")
        assert is_bm25_enabled() is True

    def test_is_bm25_enabled_other_values_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 true 值返回 False。"""
        for value in ["false", "1", "yes", "0", ""]:
            monkeypatch.setenv("BM25_ENABLED", value)
            assert is_bm25_enabled() is False, f"BM25_ENABLED={value!r} 应返回 False"
