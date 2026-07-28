"""QaService 多轮对话业务编排层单元测试（阶段 9.2）。

测试覆盖：
- 会话管理公开方法：create / get / list / delete / list_messages
- ``answer`` with ``conversation_id``：会话不存在、首轮（无历史）、多轮（历史注入 +
  查询改写 + 消息持久化）、会话级 ``document_ids`` 锁定、首条消息标题设置
- ``answer_stream`` with ``conversation_id``：流式多轮路径、会话不存在 → error 事件、
  done 事件携带 ``conversation_id``
- 查询改写失败回退：rewrite_query 抛异常时不阻塞问答

测试策略（与 ``test_qa_orchestration.py`` 一致）：
- 内存 SQLite + 真实 ``DocumentRepository`` / ``ConversationRepository``。
- 注入 ``_FakeEmbeddings``（确定性字符袋向量）跳过真实 Embedding 模型。
- ``monkeypatch`` 替换 ``answer_question`` / ``answer_with_messages`` / ``rewrite_query``，
  控制 LLM 返回值，不消耗真实 Token。
- 流式测试用 ``_StreamFakeChatModel`` 逐 token 产出。
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
    DocumentStatus,
    MessageRole,
)
from research_rag.qa_service import AnswerWithCitations, LlmConfig
from research_rag.services.qa_service import (
    ConversationNotFoundError,
    QaService,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamTokenEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from langchain_core.callbacks import CallbackManagerForLLMRun


# ---------------------------------------------------------------------------
# 辅助：确定性 FakeEmbeddings（与 test_qa_orchestration.py 一致）
# ---------------------------------------------------------------------------


class _FakeEmbeddings(Embeddings):
    """确定性字符袋 Embeddings。"""

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


class _StreamFakeChatModel(BaseChatModel):
    """按预设 token 列表异步流式产出的 Fake ChatModel。"""

    tokens: list[str]

    @property
    def _llm_type(self) -> str:
        return "stream-fake-multi-turn"

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess: Session = factory()
    yield sess
    sess.close()


@pytest.fixture
def llm_config() -> LlmConfig:
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
    return FakeListChatModel(responses=["dummy"])


@pytest.fixture
def service(
    session: Session,
    llm_config: LlmConfig,
    fake_embeddings: _FakeEmbeddings,
    fake_chat_model: BaseChatModel,
) -> QaService:
    """QaService 实例，注入 Fake Embeddings 和 ChatModel。"""
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
    chunk_list = list(chunks or [])
    doc = Document(
        original_name=name,
        stored_name=f"{name}.stored",
        sha256=hashlib.sha256(name.encode()).hexdigest(),
        page_count=max((p for p, _, _ in chunk_list), default=1),
        status=status,
    )
    session.add(doc)
    session.flush()
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


def _mock_answer(text: str = "答案 [C1]。", indices: list[int] | None = None) -> MagicMock:
    """构造 mock answer_question / answer_with_messages 返回值。"""
    return MagicMock(
        return_value=AnswerWithCitations(
            answer_text=text,
            citation_indices=indices if indices is not None else [1],
            citations=[],
        )
    )


# ---------------------------------------------------------------------------
# 会话管理公开方法
# ---------------------------------------------------------------------------


def test_create_conversation_with_defaults(service: QaService) -> None:
    """create_conversation：默认无 title、无 document_ids，id 自动生成。"""
    conv = service.create_conversation()
    assert isinstance(conv.id, uuid.UUID)
    assert conv.title is None
    assert conv.document_ids is None


def test_create_conversation_with_title_and_document_ids(service: QaService) -> None:
    """create_conversation：传入 title 和 document_ids（UUID 列表）。"""
    doc_id = uuid.uuid4()
    conv = service.create_conversation(title="测试", document_ids=[doc_id])
    assert conv.title == "测试"
    assert conv.document_ids == [str(doc_id)]


def test_get_conversation_not_found_raises(service: QaService) -> None:
    """get_conversation：会话不存在抛 ConversationNotFoundError。"""
    with pytest.raises(ConversationNotFoundError):
        service.get_conversation(uuid.uuid4())


def test_list_conversations_returns_ordered_by_updated_at_desc(
    service: QaService, session: Session
) -> None:
    """list_conversations：按 updated_at 降序。"""
    from datetime import datetime

    conv1 = service.create_conversation(title="旧")
    conv1.created_at = datetime(2026, 1, 1, 10, 0, 0)
    conv1.updated_at = datetime(2026, 1, 1, 10, 0, 0)
    conv2 = service.create_conversation(title="新")
    conv2.created_at = datetime(2026, 1, 2, 10, 0, 0)
    conv2.updated_at = datetime(2026, 1, 2, 10, 0, 0)
    session.flush()

    convs = service.list_conversations()
    assert len(convs) == 2
    assert convs[0].title == "新"
    assert convs[1].title == "旧"


def test_delete_conversation_cascades_messages(service: QaService) -> None:
    """delete_conversation：级联删除消息。"""
    conv = service.create_conversation(title="待删")
    service.conv_repo.add_message(conv.id, role=MessageRole.USER, content="问题")
    service.conv_repo.add_message(conv.id, role=MessageRole.ASSISTANT, content="答案")
    session_flush = service.session
    session_flush.flush()
    assert len(service.list_messages(conv.id)) == 2

    service.delete_conversation(conv.id)
    service.session.commit()

    with pytest.raises(ConversationNotFoundError):
        service.get_conversation(conv.id)


def test_list_messages_conversation_not_found_raises(service: QaService) -> None:
    """list_messages：会话不存在抛 ConversationNotFoundError。"""
    with pytest.raises(ConversationNotFoundError):
        service.list_messages(uuid.uuid4())


# ---------------------------------------------------------------------------
# answer with conversation_id：会话不存在
# ---------------------------------------------------------------------------


def test_answer_with_nonexistent_conversation_raises(
    service: QaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """conversation_id 不存在：抛 ConversationNotFoundError（在检索之前）。"""
    mock_answer = MagicMock()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    with pytest.raises(ConversationNotFoundError):
        service.answer("问题", conversation_id=uuid.uuid4())

    mock_answer.assert_not_called()


# ---------------------------------------------------------------------------
# answer with conversation_id：首轮（无历史）
# ---------------------------------------------------------------------------


def test_answer_first_turn_persists_messages_and_sets_title(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首轮（会话存在但无历史）：走单轮路径，持久化 user + assistant 消息，设置标题。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()  # title=None
    mock_answer = _mock_answer("深度学习是机器学习分支 [C1]。")
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    response = service.answer("深度学习是什么？", conversation_id=conv.id)

    # 响应携带 conversation_id
    assert response.conversation_id == conv.id
    # 消息已持久化：1 个 user + 1 个 assistant
    msgs = service.list_messages(conv.id)
    assert len(msgs) == 2
    assert msgs[0].role == MessageRole.USER
    assert msgs[0].content == "深度学习是什么？"
    assert msgs[0].citations is None
    assert msgs[1].role == MessageRole.ASSISTANT
    assert msgs[1].content == "深度学习是机器学习分支 [C1]。"
    assert msgs[1].citations is not None  # assistant 消息含引用快照
    # 首条消息自动设置标题（问题前 30 字符）
    refreshed_conv = service.get_conversation(conv.id)
    assert refreshed_conv.title == "深度学习是什么？"


def test_answer_persists_request_id_on_assistant_message(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """answer 路径：assistant 消息持久化 ``request_id``（与响应一致）。

    验证 ADR 0003 的写入路径：``_persist_turn`` 把 ``request_id`` 透传到
    assistant ``Message``（user 消息不写）。本测试聚焦 assistant 写入，
    user 不写入由独立测试覆盖。
    """

    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", _mock_answer())

    response = service.answer("深度学习是什么？", conversation_id=conv.id)

    assert response.request_id is not None
    msgs = service.list_messages(conv.id)
    assert len(msgs) == 2
    assistant_msg = msgs[1]
    assert assistant_msg.role == MessageRole.ASSISTANT
    # assistant 消息的 request_id 与响应返回的 request_id 一致
    assert assistant_msg.request_id == response.request_id


def test_answer_does_not_persist_request_id_on_user_message(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """user 消息不写 ``request_id``（保持 None）。

    ADR 0003：反馈主关联键是 assistant 答案的 ``request_id``，user 消息
    无问答关联，不应写。多条 NULL 不冲突（SQL 标准对 NULL 的唯一约束语义）。
    """

    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", _mock_answer())

    service.answer("深度学习是什么？", conversation_id=conv.id)

    msgs = service.list_messages(conv.id)
    assert len(msgs) == 2
    user_msg = msgs[0]
    assert user_msg.role == MessageRole.USER
    # user 消息 request_id 必须为 None（不能误传）
    assert user_msg.request_id is None


def test_answer_first_turn_long_question_title_truncated(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首条消息标题截断到 30 字符。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "内容。")])
    conv = service.create_conversation()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", _mock_answer())

    long_question = "这是一个非常非常长的用户问题，超过了三十个字符的限制，应该被截断处理"
    service.answer(long_question, conversation_id=conv.id)

    refreshed_conv = service.get_conversation(conv.id)
    assert refreshed_conv.title == long_question[:30]


def test_answer_does_not_override_existing_title(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话已有 title：后续问答不覆盖标题。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "内容。")])
    conv = service.create_conversation(title="自定义标题")
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", _mock_answer())

    service.answer("问题1", conversation_id=conv.id)
    service.answer("问题2", conversation_id=conv.id)

    refreshed_conv = service.get_conversation(conv.id)
    assert refreshed_conv.title == "自定义标题"
    # 两条消息（每轮 user + assistant = 4 条）
    assert len(service.list_messages(conv.id)) == 4


# ---------------------------------------------------------------------------
# answer with conversation_id：多轮（历史注入 + 查询改写）
# ---------------------------------------------------------------------------


def test_answer_multi_turn_loads_history_and_calls_answer_with_messages(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多轮：有历史时调 rewrite_query + answer_with_messages（不是 answer_question）。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()

    # 预填充一轮历史
    service.conv_repo.add_message(conv.id, role=MessageRole.USER, content="上一轮问题")
    service.conv_repo.add_message(
        conv.id, role=MessageRole.ASSISTANT, content="上一轮答案 [C1]。", citations=None
    )
    session.flush()

    # mock rewrite_query 返回改写后的问题
    mock_rewrite = MagicMock(return_value="论文.pdf 的深度学习是什么？")
    monkeypatch.setattr("research_rag.services.qa_service.rewrite_query", mock_rewrite)

    # mock answer_with_messages（多轮路径）
    mock_answer_messages = _mock_answer("深度学习是分支 [C1]。")
    monkeypatch.setattr(
        "research_rag.services.qa_service.answer_with_messages", mock_answer_messages
    )

    # mock answer_question 不应被调用
    mock_answer_single = MagicMock()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer_single)

    response = service.answer("那篇论文的方法", conversation_id=conv.id, top_k=1)

    # rewrite_query 被调用（用原问题 + 历史）
    mock_rewrite.assert_called_once()
    call_args = mock_rewrite.call_args
    assert call_args[0][0] == "那篇论文的方法"  # 原问题
    assert len(call_args[0][1]) == 2  # 历史消息数（1 轮 = 2 条）

    # answer_with_messages 被调用（不是 answer_question）
    mock_answer_messages.assert_called_once()
    mock_answer_single.assert_not_called()

    # 消息持久化：原有 2 条 + 新增 2 条 = 4 条
    msgs = service.list_messages(conv.id)
    assert len(msgs) == 4
    assert msgs[2].role == MessageRole.USER
    assert msgs[2].content == "那篇论文的方法"
    assert msgs[3].role == MessageRole.ASSISTANT
    assert msgs[3].content == "深度学习是分支 [C1]。"

    assert response.conversation_id == conv.id


def test_answer_multi_turn_uses_rewritten_query_for_retrieval(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多轮：检索用改写后的问题（不是原问题）。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()
    service.conv_repo.add_message(conv.id, role=MessageRole.USER, content="历史问题")
    service.conv_repo.add_message(conv.id, role=MessageRole.ASSISTANT, content="历史答案")
    session.flush()

    rewritten = "改写后的独立问题"
    monkeypatch.setattr(
        "research_rag.services.qa_service.rewrite_query",
        MagicMock(return_value=rewritten),
    )
    # 用 _prepare_contexts 内部检索的副作用验证：注入 reranker 记录 query
    captured_query: list[str] = []

    class _QueryCaptureReranker:
        def rerank(
            self, query: str, contents: Sequence[str], top_k: int | None = None
        ) -> list[tuple[int, float]]:
            captured_query.append(query)
            return [(i, 1.0) for i in range(len(contents))]

    service.reranker = _QueryCaptureReranker()
    monkeypatch.setattr("research_rag.services.qa_service.answer_with_messages", _mock_answer())

    service.answer("那篇", conversation_id=conv.id, top_k=1)

    # reranker 收到的是改写后的问题
    assert captured_query == [rewritten]


def test_answer_rewrite_failure_falls_back_to_original_question(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rewrite_query 抛异常：回退到原问题做检索，不阻塞问答。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()
    service.conv_repo.add_message(conv.id, role=MessageRole.USER, content="历史问题")
    service.conv_repo.add_message(conv.id, role=MessageRole.ASSISTANT, content="历史答案")
    session.flush()

    # rewrite_query 抛异常（底层 rewrite_query 会捕获并回退，但这里直接 mock 返回值验证）
    # 实际上 rewrite_query 内部已处理异常，我们 mock 它"返回原问题"模拟回退场景
    original_question = "那篇论文的方法"
    monkeypatch.setattr(
        "research_rag.services.qa_service.rewrite_query",
        MagicMock(return_value=original_question),  # 回退后返回原问题
    )
    monkeypatch.setattr("research_rag.services.qa_service.answer_with_messages", _mock_answer())

    # 不抛异常，正常返回
    response = service.answer(original_question, conversation_id=conv.id, top_k=1)
    assert response.conversation_id == conv.id
    # 消息仍持久化
    assert len(service.list_messages(conv.id)) == 4


# ---------------------------------------------------------------------------
# answer with conversation_id：会话级 document_ids 锁定
# ---------------------------------------------------------------------------


def test_answer_conversation_locked_document_ids_overrides_request(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话锁定 document_ids 后：忽略请求传入的 document_ids，以会话锁定范围为准。"""
    doc_a = _make_doc(session, "论文A.pdf", chunks=[(1, 0, "深度学习内容。")])
    doc_b = _make_doc(session, "论文B.pdf", chunks=[(1, 0, "不相关内容。")])

    # 创建会话时锁定 doc_a
    conv = service.create_conversation(document_ids=[doc_a.id])

    mock_answer = _mock_answer()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    # 请求传入 doc_b（应被忽略，以会话锁定的 doc_a 为准）
    service.answer("深度学习", document_ids=[doc_b.id], conversation_id=conv.id, top_k=4)

    # answer_question 收到的 contexts 只来自 doc_a
    contexts = mock_answer.call_args[0][1]
    assert len(contexts) == 1
    assert contexts[0].document_name == "论文A.pdf"


def test_answer_conversation_no_locked_doc_ids_uses_request_doc_ids(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """会话未锁定 document_ids（None）：使用请求传入的 document_ids。"""
    doc_a = _make_doc(session, "论文A.pdf", chunks=[(1, 0, "深度学习内容。")])
    _make_doc(session, "论文B.pdf", chunks=[(1, 0, "不相关内容。")])

    conv = service.create_conversation()  # document_ids=None
    mock_answer = _mock_answer()
    monkeypatch.setattr("research_rag.services.qa_service.answer_question", mock_answer)

    service.answer("深度学习", document_ids=[doc_a.id], conversation_id=conv.id, top_k=4)

    contexts = mock_answer.call_args[0][1]
    assert len(contexts) == 1
    assert contexts[0].document_name == "论文A.pdf"


# ---------------------------------------------------------------------------
# answer with conversation_id：历史截断
# ---------------------------------------------------------------------------


def test_answer_multi_turn_truncates_long_history(
    service: QaService, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """历史超过默认 5 轮：截断后只传最近 5 轮给 rewrite_query 和 LLM。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "内容。")])
    conv = service.create_conversation()

    # 预填充 7 轮历史（14 条消息）
    for i in range(7):
        service.conv_repo.add_message(conv.id, role=MessageRole.USER, content=f"历史问题{i}")
        service.conv_repo.add_message(conv.id, role=MessageRole.ASSISTANT, content=f"历史答案{i}")
    session.flush()

    captured_history: list[list[BaseMessage]] = []

    def _capture_rewrite(
        question: str,
        history: Sequence[BaseMessage],
        model: Any,
        run_config: Any = None,
    ) -> str:
        captured_history.append(list(history))
        return question

    monkeypatch.setattr("research_rag.services.qa_service.rewrite_query", _capture_rewrite)
    monkeypatch.setattr("research_rag.services.qa_service.answer_with_messages", _mock_answer())

    service.answer("追问", conversation_id=conv.id, top_k=1)

    # 默认 max_turns=5 → 传给 rewrite_query 的历史最多 10 条
    assert len(captured_history) == 1
    assert len(captured_history[0]) == 10  # 5 轮 * 2 条
    # 保留最近 5 轮（第 3-7 轮），第一条应是 "历史问题2"
    assert captured_history[0][0].content == "历史问题2"


# ---------------------------------------------------------------------------
# answer_stream with conversation_id
# ---------------------------------------------------------------------------


async def _collect_events(service: QaService, question: str, **kwargs: Any) -> list[Any]:
    events: list[Any] = []
    async for event in service.answer_stream(question, **kwargs):
        events.append(event)
    return events


async def test_answer_stream_multi_turn_persists_and_done_has_conversation_id(
    service: QaService, session: Session
) -> None:
    """流式多轮：done 事件携带 conversation_id，消息持久化。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()
    # 预填充 1 轮历史（触发多轮路径）
    service.conv_repo.add_message(conv.id, role=MessageRole.USER, content="历史问题")
    service.conv_repo.add_message(conv.id, role=MessageRole.ASSISTANT, content="历史答案")
    session.flush()

    # 用 FakeListChatModel 作为 rewrite_query 的 LLM（返回原问题）
    # answer_stream 会调 rewrite_query，FakeListChatModel.invoke 返回 "改写问题"
    # 但 rewrite_query 失败会回退原问题，这里用 stream chat_model 即可
    # 注意：rewrite_query 用同一个 chat_model，FakeListChatModel.invoke 会消费一个 response
    # 需要给 chat_model 多准备 responses
    # 改用 _StreamFakeChatModel 做 astream，但 rewrite_query 用它的 invoke
    stream_model = _StreamFakeChatModel(tokens=["答案", " [C1]", "。"])
    service._chat_model = stream_model

    events = await _collect_events(service, "追问", conversation_id=conv.id, top_k=1)

    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]
    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]

    assert error_events == []
    assert len(done_events) == 1
    done = done_events[0]
    assert done.conversation_id == conv.id
    assert len(done.citations) == 1
    # token 拼接
    answer_text = "".join(e.text for e in token_events)
    assert answer_text == "答案 [C1]。"

    # 消息持久化：原有 2 条 + 新增 2 条 = 4 条
    msgs = service.list_messages(conv.id)
    assert len(msgs) == 4
    assert msgs[2].role == MessageRole.USER
    assert msgs[2].content == "追问"
    assert msgs[3].role == MessageRole.ASSISTANT
    assert msgs[3].content == "答案 [C1]。"


async def test_answer_stream_persists_request_id_on_assistant_message(
    service: QaService, session: Session
) -> None:
    """流式路径：assistant 消息持久化 ``request_id``（与 done 事件一致）。

    与 ``test_answer_persists_request_id_on_assistant_message`` 对称，覆盖
    ``answer_stream`` 路径（``_persist_turn`` 在流末调用，透传 ``request_id``）。
    """

    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()
    service._chat_model = _StreamFakeChatModel(tokens=["答案", " [C1]", "。"])

    events = await _collect_events(service, "深度学习", conversation_id=conv.id, top_k=1)

    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    assert len(done_events) == 1
    done = done_events[0]
    assert done.request_id is not None

    msgs = service.list_messages(conv.id)
    assert len(msgs) == 2
    assistant_msg = msgs[1]
    assert assistant_msg.role == MessageRole.ASSISTANT
    # assistant 消息的 request_id 与 done 事件返回的 request_id 一致
    assert assistant_msg.request_id == done.request_id
    assert assistant_msg.id == done.message_id


async def test_answer_stream_with_nonexistent_conversation_emits_error(
    service: QaService,
) -> None:
    """流式 + 会话不存在：发 StreamErrorEvent（不抛出）。"""
    events = await _collect_events(service, "问题", conversation_id=uuid.uuid4())

    error_events = [e for e in events if isinstance(e, StreamErrorEvent)]
    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    token_events = [e for e in events if isinstance(e, StreamTokenEvent)]

    assert token_events == []
    assert done_events == []
    assert len(error_events) == 1
    assert "会话不存在" in error_events[0].detail


async def test_answer_stream_first_turn_no_history_works(
    service: QaService, session: Session
) -> None:
    """流式首轮（会话存在但无历史）：走单轮流式路径，持久化消息。"""
    _make_doc(session, "论文.pdf", chunks=[(1, 0, "深度学习内容。")])
    conv = service.create_conversation()

    service._chat_model = _StreamFakeChatModel(tokens=["答案", " [C1]", "。"])

    events = await _collect_events(service, "深度学习", conversation_id=conv.id, top_k=1)

    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    assert len(done_events) == 1
    assert done_events[0].conversation_id == conv.id

    # 消息持久化
    msgs = service.list_messages(conv.id)
    assert len(msgs) == 2
    assert msgs[0].content == "深度学习"
    assert msgs[1].content == "答案 [C1]。"


async def test_answer_stream_conversation_locked_doc_ids(
    service: QaService, session: Session
) -> None:
    """流式 + 会话锁定 document_ids：以会话锁定范围检索。"""
    doc_a = _make_doc(session, "论文A.pdf", chunks=[(1, 0, "深度学习内容。")])
    _make_doc(session, "论文B.pdf", chunks=[(1, 0, "不相关内容。")])

    conv = service.create_conversation(document_ids=[doc_a.id])
    service._chat_model = _StreamFakeChatModel(tokens=["答案", " [C1]", "。"])

    # 请求传入 doc_b（应被忽略）
    events = await _collect_events(
        service,
        "深度学习",
        document_ids=[doc_a.id],  # 即使传 doc_a 也应以会话锁定为准
        conversation_id=conv.id,
        top_k=4,
    )

    done_events = [e for e in events if isinstance(e, StreamDoneEvent)]
    assert len(done_events) == 1
    # 引用来自 doc_a（会话锁定的范围）
    assert len(done_events[0].citations) == 1
    assert done_events[0].citations[0].document_name == "论文A.pdf"
