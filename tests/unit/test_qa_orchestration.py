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
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
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
    INSUFFICIENT_EVIDENCE_MARKER,
    AnswerWithCitations,
    InsufficientEvidenceError,
    LlmConfig,
    LlmServiceError,
)
from research_rag.services.qa_service import (
    NoAvailableDocumentsError,
    QaService,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamTokenEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from langchain_core.callbacks import CallbackManagerForLLMRun


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
            ``page_number`` 同时作为 ``start_page`` 和 ``end_page``（单页 chunk）。

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
                start_page=page_number,
                end_page=page_number,
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
    assert response.citations[0].start_page == 1
    assert response.citations[0].end_page == 1
    assert response.citations[0].chunk_index == 0
    assert "深度学习" in response.citations[0].snippet
    assert response.citations[0].score > 0

    # contexts[1] 是分数第二的（含"学习"1个字符）
    assert response.citations[1].start_page == 2
    assert response.citations[1].end_page == 2
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


# ---------------------------------------------------------------------------
# Reranker 集成：注入后重排 contexts 和 context_doc_ids（阶段 8）
# ---------------------------------------------------------------------------


class _FixedScoreReranker:
    """按预设分数列表对 contents 评分的 Fake Reranker。

    构造时传入 ``scores`` 列表（与 contents 一一对应），``rerank`` 返回
    ``[(原始索引, 分数)]`` 按分数降序排列。记录调用参数便于断言。

    用于测试 reranker 注入后 ``QaService.answer`` 是否正确重排
    ``contexts`` 和 ``context_doc_ids`` 两个平行列表，以及是否更新 ``score``。
    """

    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    def rerank(
        self,
        query: str,
        contents: Sequence[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        self.calls.append((query, list(contents)))
        indexed = list(enumerate(self.scores[: len(contents)]))
        indexed.sort(key=lambda x: x[1], reverse=True)
        if top_k is not None and top_k > 0:
            indexed = indexed[:top_k]
        return [(idx, score) for idx, score in indexed]


def test_answer_with_reranker_reorders_contexts_and_syncs_doc_ids(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
    fake_chat_model: BaseChatModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注入 Reranker 后：contexts 和 context_doc_ids 按重排分数同步重排。

    场景：
    - 单文档，2 个 chunks：
      - chunk0 (page 1, idx 0)："深度学习是机器学习分支" —— ``_FakeEmbeddings``
        字符袋会让它向量检索 score 最高（含"深度学习"4 个字符）
      - chunk1 (page 2, idx 1)："神经网络可以用于图像识别" —— 向量检索 score 较低
    - 向量检索后 contexts 顺序：[chunk0, chunk1]
    - Fake Reranker 给 chunk0 分数 0.1，给 chunk1 分数 0.9（与向量检索顺序相反）
    - 重排后 contexts 顺序：[chunk1, chunk0]
    - ``citation_indices=[1]`` 映射到重排后的 contexts[0]（即原 chunk1）
    """

    doc = _make_doc(
        session,
        "论文A.pdf",
        chunks=[
            (1, 0, "深度学习是机器学习分支"),
            (2, 1, "神经网络可以用于图像识别"),
        ],
    )

    # Fake Reranker：给原 chunk0 分数 0.1，原 chunk1 分数 0.9
    reranker = _FixedScoreReranker(scores=[0.1, 0.9])

    service = QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=fake_chat_model,
        reranker=reranker,
    )

    mock_answer = MagicMock(
        return_value=AnswerWithCitations(
            answer_text="回答。",
            citation_indices=[1],  # 引用重排后的 contexts[0]
            citations=[],
        )
    )
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    response = service.answer("深度学习", top_k=2)

    # rerank 被调用一次，query 和 contents 正确
    assert len(reranker.calls) == 1
    called_query, called_contents = reranker.calls[0]
    assert called_query == "深度学习"
    assert len(called_contents) == 2
    assert called_contents[0] == "深度学习是机器学习分支"
    assert called_contents[1] == "神经网络可以用于图像识别"

    # 重排后 contexts[0] 是原 chunk1（page 2, idx 1），contexts[1] 是原 chunk0
    contexts = mock_answer.call_args[0][1]
    assert contexts[0].start_page == 2
    assert contexts[0].end_page == 2
    assert contexts[0].chunk_index == 1
    assert contexts[1].start_page == 1
    assert contexts[1].end_page == 1
    assert contexts[1].chunk_index == 0

    # score 字段被更新为重排分数
    assert contexts[0].score == 0.9
    assert contexts[1].score == 0.1

    # citation_indices=[1] 映射到重排后的 contexts[0]（原 chunk1）
    assert len(response.citations) == 1
    assert response.citations[0].document_id == doc.id
    assert response.citations[0].start_page == 2
    assert response.citations[0].end_page == 2
    assert response.citations[0].chunk_index == 1
    assert response.citations[0].score == 0.9


def test_answer_without_reranker_skips_rerank(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reranker=None（默认）：不调用 rerank，contexts 按向量检索原始顺序。

    ``service`` fixture 默认不注入 reranker，验证 rerank 步骤被跳过。
    虽然其他测试隐式覆盖此场景，但显式断言更清晰。
    """

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

    # service.reranker 应为 None
    assert service.reranker is None

    response = service.answer("深度学习")

    # 正常返回，未触发重排
    assert response.answer == "回答。"
    assert len(response.citations) == 1


# ---------------------------------------------------------------------------
# answer_stream 流式问答（阶段 9.1 SSE）
# ---------------------------------------------------------------------------


class _StreamFakeChatModel(BaseChatModel):  # type: ignore[misc]
    """按预设 token 列表异步流式产出的 Fake ChatModel。

    ``astream`` 逐 token 产出 ``AIMessageChunk``（经 ``_astream`` →
    ``ChatGenerationChunk``），用于测试 ``answer_stream`` 的流式 token 推送、
    ``[INSUFFICIENT_EVIDENCE]`` 缓冲检测、引用映射。``_generate`` 把所有
    token 拼接为一条消息（非流式回退，流式测试不依赖）。
    """

    tokens: list[str]

    @property
    def _llm_type(self) -> str:
        return "stream-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        content = "".join(self.tokens)
        message = AIMessageChunk(content=content)
        return ChatResult(generations=[ChatGenerationChunk(message=message)])  # type: ignore[list-item]

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        for token in self.tokens:
            yield ChatGenerationChunk(message=AIMessageChunk(content=token))


class _ErrorStreamChatModel(BaseChatModel):  # type: ignore[misc]
    """``astream`` 抛异常的 Fake ChatModel，测试流式 LLM 错误事件。"""

    error_message: str = "LLM 调用失败"

    @property
    def _llm_type(self) -> str:
        return "error-stream-fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise RuntimeError(self.error_message)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        raise RuntimeError(self.error_message)
        yield


async def _collect_events(service: QaService, question: str, **kwargs: Any) -> list[Any]:
    """收集 ``answer_stream`` 产出的所有事件。"""
    events: list[Any] = []
    async for event in service.answer_stream(question, **kwargs):
        events.append(event)
    return events


async def test_answer_stream_tokens_then_done(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
) -> None:
    """正常流式：token 事件逐个推送，done 事件携带引用映射。

    场景：单文档 2 chunks，chat_model 流式输出 "答案 [C1]。"（含引用标记），
    验证 token 拼接后等于完整答案，done 事件的 citations 映射到 contexts[0]。
    """

    doc = _make_doc(
        session,
        "论文A.pdf",
        chunks=[
            (1, 0, "深度学习是机器学习的重要分支。"),
            (2, 1, "神经网络用于图像识别。"),
        ],
    )

    chat_model = _StreamFakeChatModel(tokens=["答案", " [C1]", "。"])
    service = QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=chat_model,
    )

    events = await _collect_events(service, "深度学习", top_k=2)

    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]
    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]

    assert error_events == []
    assert len(done_events) == 1
    # token 拼接 = 完整答案
    answer_text = "".join(e.text for e in token_events)
    assert answer_text == "答案 [C1]。"

    done = done_events[0]
    assert len(done.citations) == 1
    assert done.citations[0].document_id == doc.id
    assert done.citations[0].document_name == "论文A.pdf"
    assert done.citations[0].chunk_index == 0
    assert "深度学习" in done.citations[0].snippet
    assert done.elapsed_ms >= 0


async def test_answer_stream_insufficient_evidence_single_chunk(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
) -> None:
    """证据不足（整块输出标记）：发 error 事件，不泄漏 token。"""

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "不相关内容。")],
    )

    chat_model = _StreamFakeChatModel(tokens=[INSUFFICIENT_EVIDENCE_MARKER])
    service = QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=chat_model,
    )

    events = await _collect_events(service, "问题")

    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]
    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]
    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]

    # 不应推送任何 token（标记被缓冲截获）
    assert token_events == []
    assert done_events == []
    assert len(error_events) == 1
    assert "证据不足" in error_events[0].detail


async def test_answer_stream_insufficient_evidence_split_chunks(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
) -> None:
    """证据不足（标记跨多个 chunk）：缓冲累积后发 error 事件。

    验证 ``[INSUFFICIENT`` + ``_EVIDENCE]`` 两段 token 被缓冲（首段是标记
    前缀），拼接后命中完整标记 → error 事件，不泄漏 token。
    """

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "不相关内容。")],
    )

    marker = INSUFFICIENT_EVIDENCE_MARKER
    mid = len(marker) // 2
    chat_model = _StreamFakeChatModel(tokens=[marker[:mid], marker[mid:]])
    service = QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=chat_model,
    )

    events = await _collect_events(service, "问题")

    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]
    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]

    assert token_events == []
    assert len(error_events) == 1
    assert "证据不足" in error_events[0].detail


async def test_answer_stream_no_documents_error(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
) -> None:
    """无可用文档：检索异常 → error 事件（不抛出）。"""

    chat_model = _StreamFakeChatModel(tokens=["不应到达"])
    service = QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=chat_model,
    )

    events = await _collect_events(service, "问题")

    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]
    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]
    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]

    assert token_events == []
    assert done_events == []
    assert len(error_events) == 1
    assert "READY" in error_events[0].detail or "文档" in error_events[0].detail


async def test_answer_stream_llm_error(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
) -> None:
    """LLM 流式调用抛异常 → error 事件（detail 含 "调用大模型失败"）。"""

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "深度学习内容。")],
    )

    chat_model = _ErrorStreamChatModel(error_message="连接超时")
    service = QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=chat_model,
    )

    events = await _collect_events(service, "深度学习")

    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]
    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]

    assert token_events == []
    assert done_events == []
    assert len(error_events) == 1
    assert "调用大模型失败" in error_events[0].detail
    assert "连接超时" in error_events[0].detail


async def test_answer_stream_short_answer_prefix_of_marker(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
) -> None:
    """短答案是标记前缀（如 ``[``）但非完整标记：正常 flush 为 token。

    验证缓冲逻辑：``buffer == "["`` 时，marker.startswith("[") 为 True，
    继续缓冲；流结束后 buffer 仍短于 marker 且非完整标记 → flush 为 token。
    """

    _make_doc(
        session,
        "论文.pdf",
        chunks=[(1, 0, "深度学习内容。")],
    )

    chat_model = _StreamFakeChatModel(tokens=["["])
    service = QaService(
        session,
        llm_config,
        embeddings=fake_embeddings,
        chat_model=chat_model,
    )

    events = await _collect_events(service, "深度学习", top_k=1)

    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]
    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]

    assert error_events == []
    assert len(token_events) == 1
    assert token_events[0].text == "["
    assert len(done_events) == 1
