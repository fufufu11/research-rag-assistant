"""BGE Reranker 重排序模块单元测试。

测试覆盖（阶段 8 第一个 Issue 验收标准）：
- ``RerankerConfig``：默认值、自定义值
- ``get_reranker_config``：从环境变量读取
- ``CrossEncoderReranker``：构造、依赖缺失抛异常、推理失败抛异常
- ``CrossEncoderReranker.rerank``：正常评分排序、空输入、top_k 截断
- ``rerank_results`` 泛型函数：支持 ``RetrievalResult`` / ``QdrantSearchResult`` /
  ``ContextPiece``，score 更新、顺序变化、空输入、top_k 截断
- ``create_reranker_if_enabled``：环境变量开关、best-effort 失败返回 None

测试策略（与 ``test_embedding.py`` / ``test_vector_store.py`` 一致）：
- 用 ``FakeReranker``（确定性评分）避免加载真实 CrossEncoder 模型
- 用 ``monkeypatch`` 模拟 ``sentence_transformers`` 导入失败
- 用 ``MagicMock`` 模拟 CrossEncoder 的 ``predict`` 返回值
- CI 不依赖 torch / sentence-transformers
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from research_rag.embedding import RetrievalResult
from research_rag.qa_service import ContextPiece
from research_rag.reranker import (
    DEFAULT_RERANKER_MODEL,
    BaseReranker,
    CrossEncoderReranker,
    RerankerConfig,
    RerankerError,
    create_reranker,
    create_reranker_if_enabled,
    get_reranker_config,
    rerank_results,
)
from research_rag.vector_store import QdrantSearchResult

if TYPE_CHECKING:
    from collections.abc import Sequence


# ---------------------------------------------------------------------------
# FakeReranker：确定性评分（不依赖真实模型）
# ---------------------------------------------------------------------------


class FakeReranker:
    """确定性 Fake Reranker，用于测试。

    评分策略：content 中包含 query 的字符数越多分数越高。
    这样测试可以预测重排后的顺序，验证 ``rerank_results`` 的正确性。
    实现 ``BaseReranker`` Protocol（鸭子类型，不需要继承）。
    """

    def rerank(
        self,
        query: str,
        contents: Sequence[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        if not contents:
            return []

        # 评分：query 中每个字符在 content 中出现的次数
        def score(content: str) -> float:
            return float(sum(1 for c in query if c in content))

        indexed = [(i, score(c)) for i, c in enumerate(contents)]
        indexed.sort(key=lambda x: x[1], reverse=True)

        if top_k is not None and top_k > 0:
            indexed = indexed[:top_k]

        return indexed


# ---------------------------------------------------------------------------
# RerankerConfig 测试
# ---------------------------------------------------------------------------


def test_reranker_config_default_values() -> None:
    """默认配置应使用 BAAI/bge-reranker-base。"""

    config = RerankerConfig()
    assert config.model_name == DEFAULT_RERANKER_MODEL
    assert config.model_name == "BAAI/bge-reranker-base"


def test_reranker_config_custom_values() -> None:
    """自定义模型名应生效。"""

    config = RerankerConfig(model_name="BAAI/bge-reranker-v2-m3")
    assert config.model_name == "BAAI/bge-reranker-v2-m3"


def test_get_reranker_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_reranker_config`` 应从环境变量读取模型名。"""

    monkeypatch.setenv("RERANKER_MODEL", "BAAI/bge-reranker-large")
    config = get_reranker_config()
    assert config.model_name == "BAAI/bge-reranker-large"


def test_get_reranker_config_defaults_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """环境变量未设置时应返回默认值。"""

    monkeypatch.delenv("RERANKER_MODEL", raising=False)
    config = get_reranker_config()
    assert config.model_name == DEFAULT_RERANKER_MODEL


# ---------------------------------------------------------------------------
# BaseReranker Protocol 测试
# ---------------------------------------------------------------------------


def test_fake_reranker_satisfies_protocol() -> None:
    """``FakeReranker`` 应满足 ``BaseReranker`` Protocol（鸭子类型）。"""

    reranker: BaseReranker = FakeReranker()
    assert isinstance(reranker, BaseReranker)


# ---------------------------------------------------------------------------
# CrossEncoderReranker 构造测试
# ---------------------------------------------------------------------------


def test_cross_encoder_raises_on_missing_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sentence_transformers`` 未安装时应抛 ``RerankerError``。"""

    import sys

    # 模拟 sentence_transformers 不可导入
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(RerankerError, match="无法导入 sentence_transformers"):
        CrossEncoderReranker()


def test_cross_encoder_raises_on_model_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型加载失败时应抛 ``RerankerError``。"""

    # 模拟 CrossEncoder 构造失败
    mock_module = MagicMock()
    mock_module.CrossEncoder.side_effect = RuntimeError("模型文件损坏")
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", mock_module)

    with pytest.raises(RerankerError, match="加载 CrossEncoder 模型失败"):
        CrossEncoderReranker()


# ---------------------------------------------------------------------------
# CrossEncoderReranker.rerank 测试（用 MagicMock 模拟 predict）
# ---------------------------------------------------------------------------


def _make_mock_cross_encoder(scores: list[float]) -> CrossEncoderReranker:
    """构造一个 ``CrossEncoderReranker``，其 ``predict`` 返回指定分数。

    跳过真实模型加载，直接设置 ``_model`` 为 MagicMock。
    """

    reranker = CrossEncoderReranker.__new__(CrossEncoderReranker)
    mock_model = MagicMock()
    mock_model.predict.return_value = scores
    reranker._model = mock_model
    return reranker


def test_cross_encoder_rerank_returns_sorted_results() -> None:
    """``rerank`` 应按分数降序返回 ``(索引, 分数)`` 列表。"""

    # 3 个候选，分数分别为 [0.1, 0.9, 0.5]
    # 期望排序：索引1(0.9) → 索引2(0.5) → 索引0(0.1)
    reranker = _make_mock_cross_encoder([0.1, 0.9, 0.5])

    result = reranker.rerank("query", ["doc0", "doc1", "doc2"])

    assert result == [(1, 0.9), (2, 0.5), (0, 0.1)]


def test_cross_encoder_rerank_empty_contents() -> None:
    """空输入应返回空列表，不调 predict。"""

    reranker = _make_mock_cross_encoder([])
    result = reranker.rerank("query", [])

    assert result == []
    reranker._model.predict.assert_not_called()


def test_cross_encoder_rerank_top_k_truncation() -> None:
    """``top_k`` 应截断结果数量。"""

    reranker = _make_mock_cross_encoder([0.3, 0.9, 0.5, 0.1])

    result = reranker.rerank("query", ["a", "b", "c", "d"], top_k=2)

    assert len(result) == 2
    assert result == [(1, 0.9), (2, 0.5)]


def test_cross_encoder_rerank_top_k_none_returns_all() -> None:
    """``top_k=None`` 应返回全部结果（仅重排序）。"""

    reranker = _make_mock_cross_encoder([0.3, 0.9])

    result = reranker.rerank("query", ["a", "b"], top_k=None)

    assert len(result) == 2


def test_cross_encoder_rerank_raises_on_predict_failure() -> None:
    """推理失败应抛 ``RerankerError``。"""

    reranker = _make_mock_cross_encoder([])
    reranker._model.predict.side_effect = RuntimeError("GPU OOM")

    with pytest.raises(RerankerError, match="CrossEncoder 推理失败"):
        reranker.rerank("query", ["doc"])


def test_cross_encoder_rerank_scores_converted_to_float() -> None:
    """返回的分数应为 Python float（而非 numpy 类型）。"""

    reranker = _make_mock_cross_encoder([0.1, 0.9])

    result = reranker.rerank("query", ["a", "b"])

    for _, score in result:
        assert isinstance(score, float)


# ---------------------------------------------------------------------------
# create_reranker 工厂函数测试
# ---------------------------------------------------------------------------


def test_create_reranker_returns_cross_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """``create_reranker`` 应返回 ``CrossEncoderReranker`` 实例。"""

    # Mock sentence_transformers
    mock_module = MagicMock()
    mock_module.CrossEncoder.return_value = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", mock_module)

    reranker = create_reranker(RerankerConfig(model_name="test-model"))

    assert isinstance(reranker, CrossEncoderReranker)
    mock_module.CrossEncoder.assert_called_once_with("test-model")


def test_create_reranker_default_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """``config=None`` 时应使用默认配置。"""

    mock_module = MagicMock()
    mock_module.CrossEncoder.return_value = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", mock_module)

    create_reranker()

    mock_module.CrossEncoder.assert_called_once_with(DEFAULT_RERANKER_MODEL)


# ---------------------------------------------------------------------------
# create_reranker_if_enabled 测试
# ---------------------------------------------------------------------------


def test_create_reranker_if_enabled_false_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RERANKER_ENABLED`` 非 true 时应返回 None。"""

    monkeypatch.setenv("RERANKER_ENABLED", "false")
    assert create_reranker_if_enabled() is None

    monkeypatch.setenv("RERANKER_ENABLED", "0")
    assert create_reranker_if_enabled() is None

    monkeypatch.delenv("RERANKER_ENABLED", raising=False)
    assert create_reranker_if_enabled() is None


def test_create_reranker_if_enabled_true_creates_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``RERANKER_ENABLED=true`` 时应创建 Reranker。"""

    mock_module = MagicMock()
    mock_module.CrossEncoder.return_value = MagicMock()
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", mock_module)
    monkeypatch.setenv("RERANKER_ENABLED", "true")

    reranker = create_reranker_if_enabled()

    assert reranker is not None
    assert isinstance(reranker, CrossEncoderReranker)


def test_create_reranker_if_enabled_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """创建失败时应返回 None（best-effort，不抛异常）。"""

    monkeypatch.setenv("RERANKER_ENABLED", "true")
    # 模拟依赖缺失
    import sys

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    assert create_reranker_if_enabled() is None


# ---------------------------------------------------------------------------
# rerank_results 泛型函数测试
# ---------------------------------------------------------------------------


def test_rerank_results_empty_input() -> None:
    """空输入应返回空列表。"""

    reranker = FakeReranker()
    result = rerank_results(reranker, "query", [])

    assert result == []


def test_rerank_results_with_retrieval_result() -> None:
    """``rerank_results`` 应支持 ``RetrievalResult`` 列表。"""

    results = [
        RetrievalResult(page_number=1, chunk_index=0, content="abc", score=0.5),
        RetrievalResult(page_number=1, chunk_index=1, content="xyz", score=0.9),
    ]

    # FakeReranker 评分：query="abc" → content "abc" 得 3 分，"xyz" 得 0 分
    reranker = FakeReranker()
    reranked = rerank_results(reranker, "abc", results)

    assert len(reranked) == 2
    # "abc" 得分更高，应排第一
    assert reranked[0].content == "abc"
    assert reranked[1].content == "xyz"
    # score 应更新为 FakeReranker 的评分
    assert reranked[0].score == 3.0
    assert reranked[1].score == 0.0
    # 其他属性应保持不变
    assert reranked[0].page_number == 1
    assert reranked[0].chunk_index == 0


def test_rerank_results_with_qdrant_search_result() -> None:
    """``rerank_results`` 应支持 ``QdrantSearchResult`` 列表。"""

    doc_id = uuid.uuid4()
    results = [
        QdrantSearchResult(
            document_id=doc_id,
            document_name="paper.pdf",
            page_number=1,
            chunk_index=0,
            content="deep learning",
            score=0.3,
        ),
        QdrantSearchResult(
            document_id=doc_id,
            document_name="paper.pdf",
            page_number=2,
            chunk_index=1,
            content="cooking recipe",
            score=0.8,
        ),
    ]

    # FakeReranker 评分：query="deep" → "deep learning" 得 4 分，"cooking recipe" 得 0 分
    reranker = FakeReranker()
    reranked = rerank_results(reranker, "deep", results)

    assert len(reranked) == 2
    assert reranked[0].content == "deep learning"
    assert reranked[1].content == "cooking recipe"
    assert reranked[0].score == 4.0
    # document_id 等元数据应保持不变
    assert reranked[0].document_id == doc_id
    assert reranked[0].document_name == "paper.pdf"
    assert reranked[0].page_number == 1


def test_rerank_results_with_context_piece() -> None:
    """``rerank_results`` 应支持 ``ContextPiece`` 列表。"""

    results = [
        ContextPiece(
            document_name="a.pdf",
            page_number=1,
            chunk_index=0,
            content="机器学习",
            score=0.2,
        ),
        ContextPiece(
            document_name="b.pdf",
            page_number=2,
            chunk_index=1,
            content="深度学习网络",
            score=0.7,
        ),
    ]

    # FakeReranker 评分：query="学习" → "机器学习" 得 2 分，"深度学习网络" 得 2 分
    # 同分时 Python sort 稳定排序，保持原始顺序
    reranker = FakeReranker()
    reranked = rerank_results(reranker, "学习", results)

    assert len(reranked) == 2
    assert reranked[0].score == 2.0
    assert reranked[1].score == 2.0
    # document_name 等元数据应保持不变
    assert reranked[0].document_name == "a.pdf"
    assert reranked[1].document_name == "b.pdf"


def test_rerank_results_top_k_truncation() -> None:
    """``top_k`` 应截断结果数量。"""

    results = [
        RetrievalResult(page_number=1, chunk_index=0, content="aaa", score=0.1),
        RetrievalResult(page_number=1, chunk_index=1, content="aab", score=0.2),
        RetrievalResult(page_number=1, chunk_index=2, content="abc", score=0.3),
        RetrievalResult(page_number=1, chunk_index=3, content="xyz", score=0.9),
    ]

    # query="abc" → "abc" 得 3 分，"aab" 得 2 分，"aaa" 得 1 分，"xyz" 得 0 分
    reranker = FakeReranker()
    reranked = rerank_results(reranker, "abc", results, top_k=2)

    assert len(reranked) == 2
    assert reranked[0].content == "abc"
    assert reranked[1].content == "aab"


def test_rerank_results_preserves_all_fields() -> None:
    """重排后所有非 score 字段应保持不变（验证 dataclasses.replace 正确性）。"""

    results = [
        RetrievalResult(page_number=3, chunk_index=5, content="hello", score=0.1),
        RetrievalResult(page_number=7, chunk_index=2, content="world", score=0.9),
    ]

    reranker = FakeReranker()
    reranked = rerank_results(reranker, "hello", results)

    # "hello" 得分更高，排第一
    assert reranked[0].content == "hello"
    assert reranked[0].page_number == 3
    assert reranked[0].chunk_index == 5

    assert reranked[1].content == "world"
    assert reranked[1].page_number == 7
    assert reranked[1].chunk_index == 2


def test_rerank_results_original_list_not_modified() -> None:
    """重排不应修改原始列表（frozen dataclass 不可变，但列表本身可变）。"""

    original = [
        RetrievalResult(page_number=1, chunk_index=0, content="b", score=0.9),
        RetrievalResult(page_number=1, chunk_index=1, content="a", score=0.1),
    ]
    original_order = [r.content for r in original]

    reranker = FakeReranker()
    rerank_results(reranker, "a", original)

    # 原始列表顺序不变
    assert [r.content for r in original] == original_order
    # 原始 score 不变
    assert original[0].score == 0.9
    assert original[1].score == 0.1
