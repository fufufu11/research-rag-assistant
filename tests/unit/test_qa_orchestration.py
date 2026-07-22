"""QaService 业务编排层单元测试。

测试覆盖（PROJECT_PLAN.md 第 6.2 节问答流程、阶段 5 第四个 Issue 验收）：
- answer 成功：多文档检索 + citation 映射 + QueryResponse 组装
- 无可用文档：NoAvailableDocumentsError
- document_ids 含不存在的 UUID：DocumentNotFoundError
- 指定文档非 READY：跳过 → NoAvailableDocumentsError
- 证据不足：InsufficientEvidenceError（由 Mock answer_question 抛出）
- LLM 异常：LlmServiceError（由 Mock answer_question 抛出）

测试策略（PROJECT_PLAN.md 第 13.2 节"测试中应 Mock 模型 API 和 Embedding 服务"）：
- 用内存 SQLite + 真实 ``DocumentRepository``，测试 DB 查询逻辑（与
  ``test_document_service.py`` 一致的隔离方式）。
- 注入 ``_FakeEmbeddings``（确定性字符袋向量），跳过真实 Embedding 模型加载，
  CI 无需安装 torch/sentence-transformers。
- 用 ``monkeypatch`` 替换 ``research_rag.services.qa_service.answer_question``
  函数，控制 LLM 返回值（``AnswerWithCitations`` 或抛异常），不消耗真实 Token。
- 注入 ``FakeListChatModel`` 作为 ``chat_model``，避免 ``create_chat_model``
  真实创建 OpenAI 客户端（虽然 answer_question 被 Mock，但 QaService.answer
  仍会惰性创建 chat_model，需注入跳过）。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from research_rag.db.models import (
    Base,
    Chunk,
    Document,
    DocumentNotFoundError,
    DocumentStatus,
)
from research_rag.qa_service import (
    AnswerWithCitations,
    InsufficientEvidenceError,
    LlmConfig,
    LlmServiceError,
)
from research_rag.services.qa_service import NoAvailableDocumentsError, QaService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.language_models.chat_models import BaseChatModel


# ---------------------------------------------------------------------------
# 辅助：确定性 FakeEmbeddings（与 test_embedding.py 一致的字符袋实现）
# ---------------------------------------------------------------------------


class _FakeEmbeddings(Embeddings):
    """确定性字符袋 Embeddings，用于编排测试。

    与 ``test_embedding.py`` 中的 ``FakeEmbeddings`` 实现一致：对文本中每个
    字符在对应维度（``ord(char) % dim``）+1，最后 L2 归一化。相同文本生成
    相同向量；共享字符的文本余弦相似度更高。不依赖任何外部模型。
    """

    def __init__(self, dim: int = 64) -> None:
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """内存 SQLite engine，建表后 yield，测试结束 drop + dispose。"""

    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """基于 ``engine`` 的 Session，测试结束关闭。"""

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess: Session = factory()
    yield sess
    sess.close()


@pytest.fixture
def llm_config() -> LlmConfig:
    """测试用 LlmConfig（不需要真实可达的 endpoint，因为 chat_model 被注入）。"""

    return LlmConfig(
        base_url="http://localhost:1234/v1",
        api_key="test-key",
        model="test-model",
    )


@pytest.fixture
def fake_embeddings() -> _FakeEmbeddings:
    return _FakeEmbeddings()


@pytest.fixture
def fake_chat_model() -> BaseChatModel:
    """``FakeListChatModel``：answer_question 被 Mock 后不会被真正调用，
    但 QaService.answer 仍会惰性创建 chat_model，注入避免真实创建。"""

    return FakeListChatModel(responses=["dummy"])


@pytest.fixture
def service(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
    fake_chat_model: BaseChatModel,
) -> QaService:
    """构造 QaService 实例，注入 Fake Embeddings 和 ChatModel。

    测试中不调 ``create_embeddings`` / ``create_chat_model``，完全跳过
    真实模型加载。
    """

    return QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=fake_chat_model,
    )


# ---------------------------------------------------------------------------
# 辅助：构造并持久化 Document + Chunks
# ---------------------------------------------------------------------------


def _make_doc(
    session: Session,
    name: str,
    status: DocumentStatus = DocumentStatus.READY,
    chunks: Sequence[tuple[int, int, str]] | None = None,
) -> Document:
    """构造并持久化一个 Document 及其 Chunks。

    Args:
        session: SQLAlchemy Session。
        name: 文档名（original_name），stored_name 加 ``.stored`` 后缀。
        status: 文档状态，默认 READY。
        chunks: ``[(page_number, chunk_index, content)]`` 列表，默认空。

    Returns:
        持久化后的 ``Document``（``id`` / ``created_at`` 已由 ORM default 填充）。
    """

    chunk_list = list(chunks or [])
    doc = Document(
        original_name=name,
        stored_name=f"{name}.stored",
        sha256=hashlib.sha256(name.encode()).hexdigest(),
        page_count=max((p for p, _, _ in chunk_list), default=1),
        status=status,
    )
    session.add(doc)
    session.flush()  # 先 flush Document，让 doc.id 由 ORM default 生成
    for page_number, chunk_index, content in chunk_list:
        session.add(
            Chunk(
                document_id=doc.id,
                page_number=page_number,
                chunk_index=chunk_index,
                content=content,
                char_count=len(content),
            )
        )
    session.flush()
    return doc


# ---------------------------------------------------------------------------
# answer 成功：多文档检索 + citation 映射
# ---------------------------------------------------------------------------


def test_answer_success_maps_citations(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常问答：检索 + citation_indices 映射到正确的 document_id / page_number。

    场景：
    - 单文档，2 个 chunks，查询"深度学习"与 chunk0 共享字符最多（score 最高）
    - top_k=2，检索后 contexts 按 score 降序排列
    - Mock answer_question 返回 citation_indices=[1, 2]
    - 验证 2 个 citation 的 document_id / page_number / chunk_index / snippet
    """

    doc = _make_doc(
        session,
        "论文A.pdf",
        chunks=[
            (1, 0, "深度学习是机器学习的重要分支，使用多层神经网络。"),
            (2, 1, "机器学习神经网络可以用于图像识别和自然语言处理。"),
        ],
    )

    # Mock answer_question：返回固定 AnswerWithCitations
    mock_answer = MagicMock(
        return_value=AnswerWithCitations(
            answer_text="深度学习是机器学习的一个分支。",
            citation_indices=[1, 2],
            citations=[],  # 底层 citations 不使用，由 QaService 重新映射
        )
    )
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    response = service.answer("深度学习", top_k=2)

    # 验证 answer_text 和结构字段
    assert response.answer == "深度学习是机器学习的一个分支。"
    assert response.request_id is not None
    assert isinstance(response.request_id, uuid.UUID)
    assert response.elapsed_ms >= 0

    # citation_indices=[1, 2] → 2 个 citation，都来自同一文档
    assert len(response.citations) == 2
    for citation in response.citations:
        assert citation.document_id == doc.id
        assert citation.document_name == "论文A.pdf"

    # contexts[0] 是分数最高的（含"深度学习"4个字符）
    assert response.citations[0].page_number == 1
    assert response.citations[0].chunk_index == 0
    assert "深度学习" in response.citations[0].snippet
    assert response.citations[0].score > 0

    # contexts[1] 是分数第二的（含"学习"1个字符）
    assert response.citations[1].page_number == 2
    assert response.citations[1].chunk_index == 1

    # 验证 answer_question 被正确调用
    mock_answer.assert_called_once()
    call_args = mock_answer.call_args
    assert call_args[0][0] == "深度学习"  # question
    assert len(call_args[0][1]) == 2  # contexts 列表长度 == top_k


def test_answer_filters_to_document_ids(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指定 document_ids：只检索指定文档，不检索其他 READY 文档。"""

    doc_a = _make_doc(
        session,
        "论文A.pdf",
        chunks=[(1, 0, "深度学习相关内容。")],
    )
    _make_doc(  # doc_b 不应被检索
        session,
        "论文B.pdf",
        chunks=[(1, 0, "不相关内容。")],
    )

    mock_answer = MagicMock(
        return_value=AnswerWithCitations(
            answer_text="回答。",
            citation_indices=[1],
            citations=[],
        )
    )
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    response = service.answer("深度学习", document_ids=[doc_a.id], top_k=4)

    # 只检索了文档 A，contexts 只有 1 个
    assert len(response.citations) == 1
    assert response.citations[0].document_id == doc_a.id

    # answer_question 收到的 contexts 只来自文档 A
    contexts = mock_answer.call_args[0][1]
    assert len(contexts) == 1
    assert contexts[0].document_name == "论文A.pdf"


def test_answer_default_top_k(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """top_k 未指定时使用 DEFAULT_TOP_K（8）。"""

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "深度学习内容。")],
    )

    mock_answer = MagicMock(
        return_value=AnswerWithCitations(
            answer_text="回答。",
            citation_indices=[1],
            citations=[],
        )
    )
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    service.answer("深度学习")

    # answer_question 被调用，contexts 长度 <= DEFAULT_TOP_K
    contexts = mock_answer.call_args[0][1]
    assert len(contexts) <= 8


# ---------------------------------------------------------------------------
# 无可用文档
# ---------------------------------------------------------------------------


def test_answer_no_documents_raises(service: QaService, monkeypatch: pytest.MonkeyPatch) -> None:
    """空库（无 READY 文档）：抛 NoAvailableDocumentsError。"""

    mock_answer = MagicMock()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    with pytest.raises(NoAvailableDocumentsError):
        service.answer("任何问题")

    # answer_question 不应被调用
    mock_answer.assert_not_called()


def test_answer_document_not_ready_raises(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """指定文档非 READY（如 PENDING）：被跳过 → NoAvailableDocumentsError。"""

    doc = _make_doc(
        session,
        "论文.pdf",
        status=DocumentStatus.PENDING,
        chunks=[(1, 0, "内容。")],
    )

    mock_answer = MagicMock()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    with pytest.raises(NoAvailableDocumentsError):
        service.answer("问题", document_ids=[doc.id])

    mock_answer.assert_not_called()


# ---------------------------------------------------------------------------
# document_ids 含不存在的 UUID
# ---------------------------------------------------------------------------


def test_answer_document_not_found_raises(
    service: QaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """document_ids 含不存在的 UUID：抛 DocumentNotFoundError。"""

    mock_answer = MagicMock()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    missing_id = uuid.uuid4()
    with pytest.raises(DocumentNotFoundError):
        service.answer("问题", document_ids=[missing_id])

    mock_answer.assert_not_called()


# ---------------------------------------------------------------------------
# LLM 异常透传
# ---------------------------------------------------------------------------


def test_answer_insufficient_evidence_propagates(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """证据不足：answer_question 抛 InsufficientEvidenceError → 透传。"""

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "深度学习内容。")],
    )

    mock_answer = MagicMock(side_effect=InsufficientEvidenceError("证据不足"))
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    with pytest.raises(InsufficientEvidenceError):
        service.answer("深度学习")

    mock_answer.assert_called_once()


def test_answer_llm_error_propagates(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 异常：answer_question 抛 LlmServiceError → 透传。"""

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "深度学习内容。")],
    )

    mock_answer = MagicMock(side_effect=LlmServiceError("LLM 调用失败"))
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    with pytest.raises(LlmServiceError):
        service.answer("深度学习")

    mock_answer.assert_called_once()


# ---------------------------------------------------------------------------
# citation_indices 越界跳过
# ---------------------------------------------------------------------------


def test_answer_citation_indices_out_of_range_skipped(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """citation_indices 越界（如 [0, 99]）：静默跳过，citations 为空。"""

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "深度学习内容。")],
    )

    mock_answer = MagicMock(
        return_value=AnswerWithCitations(
            answer_text="回答。",
            citation_indices=[0, 99],  # 0 和 99 都越界
            citations=[],
        )
    )
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    response = service.answer("深度学习")

    assert response.answer == "回答。"
    assert response.citations == []
