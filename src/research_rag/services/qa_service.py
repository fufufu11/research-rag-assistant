"""问答业务编排服务层。

依据 PROJECT_PLAN.md 第 6.2 节（问答流程）、第 8.4 节（问答 API）、
US-003、第 13.6 节（异常清单）、第 716-722 行（阶段 6：Qdrant 检索）。

设计取舍（初学者向说明）：
- **Service 编排完整问答流程**：``QaService.answer`` 是问答 API 的业务入口，
  编排 repository（数据访问）、向量检索（Qdrant 或 InMemory）、
  ``qa_service``（LLM 调用与引用映射）三类操作。不直接写 SQL，不实现 HTTP 路由。
- **与底层 ``qa_service.py`` 区分**：本模块是业务编排层（编排 DB + 向量检索 +
  LLM），底层 ``qa_service.py`` 是"LLM 调用 + 引用映射"工具模块。两者命名相同
  但职责不同：本模块在 ``services/`` 子包，底层在顶层 ``research_rag/``。
- **双检索路径**：注入 ``vector_store`` 时用 Qdrant 单库检索（支持
  ``document_ids`` payload 过滤，不再每文档单独索引）；未注入时回退到
  InMemoryVectorStore（每文档单独索引，阶段 5 行为，测试用）。
- **Qdrant 路径更高效**：向量在上传时就写入 Qdrant，问答时直接 similarity
  search，不需要每次重建索引。``QdrantSearchResult`` 含 ``document_id``，
  不再需要外部维护 ``context_doc_ids`` 平行列表。
- **Citation 映射用 ``citation_indices`` 直接索引**：``answer_question`` 返回的
  ``citation_indices`` 是上下文编号列表（从 1 开始），与 ``contexts`` 列表顺序
  一致。通过 ``contexts[idx - 1]`` 直接获取 ``ContextPiece``。
- **惰性创建 Embedding 和 ChatModel**：``embeddings`` / ``chat_model`` 作为
  构造函数可选参数，未传时在 ``answer`` 中惰性创建。生产环境由
  ``get_qa_service`` 依赖注入（不传，让 QaService 自己创建）；测试时直接注入
  ``FakeEmbeddings`` / ``FakeListChatModel``，完全跳过真实模型加载。
- **无可用文档抛 ``NoAvailableDocumentsError``**：全库无 READY 文档、或指定
  的 ``document_ids`` 均非 READY 时抛出。与 ``DocumentNotFoundError``（指定
  UUID 不存在）区分：前者是"没有可问答的内容"，后者是"ID 不存在"。
  API 层将两者都映射为 404（资源不存在语义）。
- **``request_id`` / ``elapsed_ms`` 在 service 层生成**：``request_id`` 用
  ``uuid.uuid4`` 生成，便于日志追踪单次问答；``elapsed_ms`` 用
  ``time.perf_counter`` 计时，从编排开始到结束。API 层不需要感知这些字段。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from research_rag.api.schemas import CitationRead, QueryResponse
from research_rag.chunker import Chunk as ChunkerChunk
from research_rag.db.models import Document, DocumentNotFoundError, DocumentStatus, MessageRole
from research_rag.db.repositories import ConversationRepository, DocumentRepository
from research_rag.embedding import (
    DEFAULT_TOP_K,
    EmbeddingConfig,
    RetrievalResult,
    create_embeddings,
    index_chunks,
    retrieve,
)
from research_rag.qa_service import (
    INSUFFICIENT_EVIDENCE_MARKER,
    AnswerWithCitations,
    ContextPiece,
    LlmConfig,
    answer_question,
    answer_with_messages,
    build_prompt,
    build_prompt_with_history,
    create_chat_model,
    parse_citation_indices,
    rewrite_query,
    truncate_history_messages,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_qdrant import QdrantVectorStore
    from sqlalchemy.orm import Session

    from research_rag.db.models import Conversation, Message
    from research_rag.reranker import BaseReranker


class NoAvailableDocumentsError(RuntimeError):
    """无可用文档异常。

    当全库无 ``status=ready`` 的文档、或指定的 ``document_ids`` 均非 READY
    时抛出。对应 PROJECT_PLAN.md 第 13.6 节异常清单的扩展（"无可用文档"场景）。

    API 层捕获后映射为 HTTP 404（语义：没有可问答的内容）。
    """


class ConversationNotFoundError(RuntimeError):
    """会话未找到异常（阶段 9.2 多轮对话）。

    当请求传入的 ``conversation_id`` 在 DB 中不存在时抛出。API 层捕获后
    映射为 HTTP 404（与 ``DocumentNotFoundError`` 语义一致：资源不存在）。
    """


# ---------------------------------------------------------------------------
# 流式事件（阶段 9.1 SSE）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamTokenEvent:
    """流式 token 事件：携带一段 LLM 生成的文本片段。

    answer 文本含 ``[C1]`` 等引用标记原文，前端逐字渲染；引用元数据由
    ``StreamDoneEvent`` 在流结束后统一下发。
    """

    text: str


@dataclass(frozen=True)
class StreamDoneEvent:
    """流式完成事件：携带服务端映射后的引用与耗时元数据。

    Attributes:
        citations: 服务端根据答案中的 ``[C1]`` 编号映射的真实引用列表。
        request_id: 本次问答的唯一 ID。
        elapsed_ms: 本次问答总耗时（毫秒）。
        conversation_id: 本次问答所属会话 ID（阶段 9.2）。``None`` 表示单轮
            问答未关联会话。前端据此维护会话状态。
    """

    citations: list[CitationRead]
    request_id: uuid.UUID
    elapsed_ms: int
    conversation_id: uuid.UUID | None = None


@dataclass(frozen=True)
class StreamErrorEvent:
    """流式错误事件：检索/LLM/证据不足等异常的 detail。

    流式路径下业务异常不再抛出到 API 层全局处理器（SSE 已开始则无法改 HTTP
    状态码），改为在流中发 ``error`` 事件，前端据此展示错误。
    """

    detail: str


StreamEvent = StreamTokenEvent | StreamDoneEvent | StreamErrorEvent
"""流式事件联合类型，``QaService.answer_stream`` 的产出单元。"""


class QaService:
    """问答业务编排服务：DB 查询 → 向量检索 → LLM 问答 → 组装响应。

    编排 ``DocumentRepository``（数据访问）、``embedding``（向量索引与检索）、
    ``qa_service``（LLM 调用与引用映射）。事务边界由调用方（API 层 ``get_db``）
    管理 Session 生命周期；本服务只读不写（问答不持久化）。

    Attributes:
        session: SQLAlchemy Session，由调用方传入。
        repo: ``DocumentRepository`` 实例，封装 DB 查询。
        llm_config: 大模型客户端配置（从环境变量读取）。
        embedding_config: Embedding 配置，默认 ``EmbeddingConfig()``。
    """

    def __init__(
        self,
        session: Session,
        llm_config: LlmConfig,
        embedding_config: EmbeddingConfig | None = None,
        embeddings: Embeddings | None = None,
        chat_model: BaseChatModel | None = None,
        vector_store: QdrantVectorStore | None = None,
        reranker: BaseReranker | None = None,
        bm25_enabled: bool = False,
    ) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.conv_repo = ConversationRepository(session)
        self.llm_config = llm_config
        self.embedding_config = embedding_config or EmbeddingConfig()
        self._embeddings = embeddings
        self._chat_model = chat_model
        self.vector_store = vector_store
        self.reranker = reranker
        self.bm25_enabled = bm25_enabled

    # ------------------------------------------------------------------
    # 问答主流程
    # ------------------------------------------------------------------

    def answer(
        self,
        question: str,
        document_ids: Sequence[uuid.UUID] | None = None,
        top_k: int = DEFAULT_TOP_K,
        conversation_id: uuid.UUID | None = None,
    ) -> QueryResponse:
        """完整问答流程：查文档 → 索引检索 → LLM 问答 → 组装响应（阶段 9.2 多轮）。

        流程（PROJECT_PLAN.md 第 6.2 节 + 阶段 9.2 多轮扩展）：
        1. 若 ``conversation_id`` 非 None：加载会话历史 + 用会话锁定的文档范围
        2. 查询符合条件的 READY 文档（全部或按 ``document_ids`` 过滤）
        3. 收集这些文档的 Chunk，检索 + 重排（``_prepare_contexts``）
        4. 若有历史：查询改写（解析指代）→ 用改写后的问题检索
        5. 构造 messages（带历史时用 ``build_prompt_with_history``）
        6. 调 ``answer_question`` / ``answer_with_messages`` 让 LLM 作答
        7. 用 ``citation_indices`` 映射到 ``CitationRead`` 列表
        8. 若关联会话：持久化 user + assistant 消息到 DB
        9. 组装 ``QueryResponse``（含 request_id / elapsed_ms / conversation_id）

        多轮设计要点：
        - 会话级 ``document_ids`` 锁定后优先于请求传入的 ``document_ids``，
          保证多轮上下文一致性。
        - 查询改写用历史把"那篇"等指代解析为独立问题再做检索（历史参与检索）。
        - 每轮引用编号独立（``[C1]`` 只指代当前轮 contexts）。
        - 历史截断在 ``_load_history_messages`` 内完成（轮数 + token 双重保护）。

        Args:
            question: 用户问题。
            document_ids: 限定查询的文档 UUID 列表。``None`` 或空列表表示
                查询全库 READY 文档。``conversation_id`` 非 None 且会话已锁定
                ``document_ids`` 时，以此会话锁定范围为准（忽略请求传入值）。
            top_k: 检索返回的最相关片段数。
            conversation_id: 会话 ID（阶段 9.2）。``None`` 表示单轮问答；
                传入已存在会话 ID 时加载历史并持久化本轮消息。

        Returns:
            ``QueryResponse`` 实例（含 ``conversation_id`` 字段）。

        Raises:
            ConversationNotFoundError: ``conversation_id`` 不存在。
            DocumentNotFoundError: ``document_ids`` 中有不存在的 UUID。
            NoAvailableDocumentsError: 无可用 READY 文档。
            LlmServiceError: LLM 调用失败。
            InsufficientEvidenceError: 上下文证据不足以回答。
            EmbeddingServiceError: Embedding 模型加载失败。
            VectorStoreError: 向量索引或检索失败。
        """

        request_id = uuid.uuid4()
        start = time.perf_counter()

        # 1. 解析会话：加载历史 + 确定文档范围（阶段 9.2）
        history_messages: list[BaseMessage] = []
        effective_doc_ids = document_ids
        conv: Conversation | None = None
        if conversation_id is not None:
            conv = self.conv_repo.get_by_id(conversation_id)
            if conv is None:
                msg = f"会话不存在：{conversation_id}"
                raise ConversationNotFoundError(msg)
            # 会话级 document_ids 优先（若已锁定），保证多轮上下文一致性
            if conv.document_ids is not None:
                effective_doc_ids = [uuid.UUID(d) for d in conv.document_ids]
            history_messages = self._load_history_messages(conversation_id)

        # 2. 确保 chat_model 就绪（查询改写和 LLM 调用都需要）
        chat_model = self._ensure_chat_model()

        # 3. 查询改写（若有历史）：用改写后的问题检索，提升多轮检索质量
        search_query = question
        if history_messages:
            search_query = rewrite_query(question, history_messages, chat_model)

        # 4. 检索 + 重排（用改写后的问题；与非流式共享 ``_prepare_contexts``）
        contexts, context_doc_ids, _ = self._prepare_contexts(
            search_query, effective_doc_ids, top_k
        )

        # 5. 调 LLM 问答（带历史用 ``answer_with_messages``，否则单轮）
        if history_messages:
            messages = build_prompt_with_history(question, contexts, history_messages)
            result = answer_with_messages(messages, contexts, chat_model)
        else:
            result = answer_question(question, contexts, chat_model)

        # 6. 映射引用
        citations = self._map_citations(result, contexts, context_doc_ids)

        # 7. 持久化消息（若关联会话）
        if conv is not None:
            self._persist_turn(conv, question, result.answer_text, citations)

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return QueryResponse(
            answer=result.answer_text,
            citations=citations,
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            conversation_id=conversation_id,
        )

    async def answer_stream(
        self,
        question: str,
        document_ids: Sequence[uuid.UUID] | None = None,
        top_k: int = DEFAULT_TOP_K,
        conversation_id: uuid.UUID | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """流式问答流程：检索 + 重排 → LLM 逐 token 流式生成 → 引用映射（阶段 9.2 多轮）。

        与 ``answer`` 共享检索与重排逻辑（``_prepare_contexts``）和多轮逻辑
        （会话解析、查询改写、历史注入），区别仅在 LLM 生成阶段用
        ``chat_model.astream`` 逐 token 推送 ``StreamTokenEvent``，流结束后
        用 ``parse_citation_indices`` + ``_map_citations`` 映射引用，并发
        ``StreamDoneEvent`` 携带元数据（含 ``conversation_id``）。

        事件序列（正常）：
            ``StreamTokenEvent`` * N → ``StreamDoneEvent``

        事件序列（异常）：会话不存在/检索/LLM 异常或证据不足时发
        ``StreamErrorEvent`` 并终止。流式路径下业务异常不再抛出到 API 层全局
        处理器（SSE 已开始则无法改 HTTP 状态码），改为在流中发 ``error`` 事件。

        证据不足处理：模型被要求证据不足时仅输出 ``[INSUFFICIENT_EVIDENCE]``。
        为避免把标记原文推给用户，在确认输出非该标记前缀前缓冲首段 token；
        一旦缓冲可判定不会变成完整标记即 flush 并停止缓冲，后续直接推送。

        Args:
            question: 用户问题。
            document_ids: 限定查询的文档 UUID 列表。``conversation_id`` 非 None
                且会话已锁定 ``document_ids`` 时，以会话锁定范围为准。
            top_k: 检索返回的最相关片段数。
            conversation_id: 会话 ID（阶段 9.2）。``None`` 表示单轮问答。

        Yields:
            ``StreamEvent``：``StreamTokenEvent`` / ``StreamDoneEvent`` /
            ``StreamErrorEvent``。
        """

        request_id = uuid.uuid4()
        start = time.perf_counter()

        # 1. 解析会话 + 确保 chat_model + 查询改写 + 检索（异常映射为 error 事件）
        history_messages: list[BaseMessage] = []
        effective_doc_ids = document_ids
        conv: Conversation | None = None
        try:
            if conversation_id is not None:
                conv = self.conv_repo.get_by_id(conversation_id)
                if conv is None:
                    msg = f"会话不存在：{conversation_id}"
                    raise ConversationNotFoundError(msg)
                if conv.document_ids is not None:
                    effective_doc_ids = [uuid.UUID(d) for d in conv.document_ids]
                history_messages = self._load_history_messages(conversation_id)

            chat_model = self._ensure_chat_model()

            search_query = question
            if history_messages:
                search_query = rewrite_query(question, history_messages, chat_model)

            contexts, context_doc_ids, _ = self._prepare_contexts(
                search_query, effective_doc_ids, top_k
            )
        except Exception as exc:  # 流式契约需把所有异常转为事件
            yield StreamErrorEvent(detail=str(exc))
            return

        # 2. 构造 messages（带历史用 ``build_prompt_with_history``）
        if history_messages:
            messages = build_prompt_with_history(question, contexts, history_messages)
        else:
            messages = build_prompt(question, contexts)

        parts: list[str] = []
        # 证据不足标记缓冲：见上方 docstring 说明
        buffer = ""
        buffering = True

        try:
            async for chunk in chat_model.astream(messages):
                token = getattr(chunk, "content", "")
                if not isinstance(token, str) or not token:
                    continue
                parts.append(token)
                if buffering:
                    buffer += token
                    if INSUFFICIENT_EVIDENCE_MARKER in buffer:
                        yield StreamErrorEvent(detail="上下文证据不足以回答该问题。")
                        return
                    if not INSUFFICIENT_EVIDENCE_MARKER.startswith(buffer):
                        # 缓冲不可能再变成标记，flush 并停止缓冲
                        buffering = False
                        yield StreamTokenEvent(text=buffer)
                        buffer = ""
                else:
                    yield StreamTokenEvent(text=token)
        except Exception as exc:
            yield StreamErrorEvent(detail=f"调用大模型失败：{exc}")
            return

        # flush 残留缓冲（流结束时仍缓冲，说明输出短于标记且是其前缀；
        # 已由上方 in 检查排除完整标记，故为正常短答案）
        if buffering and buffer:
            yield StreamTokenEvent(text=buffer)
            buffer = ""

        answer_text = "".join(parts)
        # 安全兜底：标记出现在已 flush 的文本中（设计上不应发生）→ error
        if INSUFFICIENT_EVIDENCE_MARKER in answer_text:
            yield StreamErrorEvent(detail="上下文证据不足以回答该问题。")
            return

        citation_indices = parse_citation_indices(answer_text)
        result = AnswerWithCitations(
            answer_text=answer_text,
            citation_indices=citation_indices,
            citations=[],
        )
        citations = self._map_citations(result, contexts, context_doc_ids)

        # 3. 持久化消息（若关联会话）
        if conv is not None:
            self._persist_turn(conv, question, answer_text, citations)

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        yield StreamDoneEvent(
            citations=citations,
            request_id=request_id,
            elapsed_ms=elapsed_ms,
            conversation_id=conversation_id,
        )

    # ------------------------------------------------------------------
    # 会话管理（阶段 9.2，供 API 路由层调用）
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        *,
        title: str | None = None,
        document_ids: Sequence[uuid.UUID] | None = None,
    ) -> Conversation:
        """创建会话（阶段 9.2）。

        Args:
            title: 会话标题（可空）。未提供时由 ``_persist_turn`` 在首条消息
                后用问题截取自动设置。
            document_ids: 会话级文档范围锁定。``None`` 或空列表表示查询全库
                READY 文档。锁定后后续问答以此范围为准，避免多轮上下文不一致。

        Returns:
            已持久化（flush 后 id 已生成）的 ``Conversation`` 实例。
            事务提交由调用方（API 层 ``get_db``）控制。
        """

        # document_ids 存为字符串列表（JSON 序列化），读取时再转回 UUID
        doc_ids_str = [str(d) for d in document_ids] if document_ids else None
        return self.conv_repo.create(title=title, document_ids=doc_ids_str)

    def get_conversation(self, conversation_id: uuid.UUID) -> Conversation:
        """按 ID 查询会话，不存在抛 ``ConversationNotFoundError``。

        Args:
            conversation_id: 会话 UUID。

        Returns:
            ``Conversation`` 实例。

        Raises:
            ConversationNotFoundError: 会话不存在。
        """

        conv = self.conv_repo.get_by_id(conversation_id)
        if conv is None:
            msg = f"会话不存在：{conversation_id}"
            raise ConversationNotFoundError(msg)
        return conv

    def list_conversations(self) -> list[Conversation]:
        """列出所有会话，按 ``updated_at`` 降序（最近活跃在前）。"""

        return self.conv_repo.list_all()

    def delete_conversation(self, conversation_id: uuid.UUID) -> None:
        """删除会话（级联删除其消息）。

        Args:
            conversation_id: 会话 UUID。

        Raises:
            ConversationNotFoundError: 会话不存在。
        """

        conv = self.get_conversation(conversation_id)
        self.conv_repo.delete(conv)

    def list_messages(self, conversation_id: uuid.UUID) -> list[Message]:
        """列出会话内消息，按 ``created_at`` 升序（对话时间顺序）。

        供 API 路由层在会话详情接口调用，返回 ORM ``Message`` 列表，由路由层
        用 ``MessageRead.model_validate`` 转 schema。

        Args:
            conversation_id: 会话 UUID。

        Returns:
            ORM ``Message`` 列表（升序）。

        Raises:
            ConversationNotFoundError: 会话不存在。
        """

        # 先校验会话存在（避免返回空列表误导调用方认为会话存在但无消息）
        self.get_conversation(conversation_id)
        return self.conv_repo.list_messages(conversation_id)

    # ------------------------------------------------------------------
    # 私有：会话与多轮辅助（阶段 9.2）
    # ------------------------------------------------------------------

    def _ensure_chat_model(self) -> BaseChatModel:
        """惰性创建 ``chat_model``（测试时已注入，跳过创建）。

        抽取自原 ``_prepare_contexts`` 的惰性创建逻辑，供 ``answer`` /
        ``answer_stream`` / 查询改写 ``rewrite_query`` 复用，避免在多个
        入口重复写 ``if self._chat_model is None: ...``。

        Returns:
            ``BaseChatModel`` 实例（已确保非 None）。
        """

        if self._chat_model is None:
            self._chat_model = create_chat_model(self.llm_config)
        assert self._chat_model is not None
        return self._chat_model

    def _load_history_messages(self, conversation_id: uuid.UUID) -> list[BaseMessage]:
        """加载会话历史并截断为 LangChain ``BaseMessage`` 列表（阶段 9.2）。

        流程：从 DB 取所有消息（升序）→ 转 ``HumanMessage`` / ``AIMessage`` →
        用 ``truncate_history_messages`` 做轮数 + token 双重保护截断。

        Args:
            conversation_id: 会话 UUID。

        Returns:
            截断后的历史消息列表（按时间升序，``HumanMessage`` / ``AIMessage``
            交替），可能为空。``assistant`` 消息保留 ``[C1]`` 等标记原文（
            历史轮引用编号不参与当前轮映射，仅作上下文让模型理解指代）。
        """

        msgs = self.conv_repo.list_messages(conversation_id)
        history: list[BaseMessage] = []
        for m in msgs:
            if m.role == MessageRole.USER:
                history.append(HumanMessage(content=m.content))
            elif m.role == MessageRole.ASSISTANT:
                history.append(AIMessage(content=m.content))
        return truncate_history_messages(history)

    def _persist_turn(
        self,
        conv: Conversation,
        question: str,
        answer_text: str,
        citations: list[CitationRead],
    ) -> None:
        """持久化一轮问答到 DB（阶段 9.2）。

        写入两条消息（user + assistant），assistant 消息的 ``citations``
        字段存引用元数据快照（JSON）。首条消息时（会话标题为空）用问题
        截取设置标题，便于 UI 列表展示。

        事务提交由调用方（API 层 ``get_db``）控制，本方法只 flush。

        Args:
            conv: 所属会话 ORM 实例（已持久化）。
            question: 用户问题原文。
            answer_text: 模型生成的答案文本（含 ``[C1]`` 等标记原文）。
            citations: 服务端映射后的引用列表（结构对齐 ``CitationRead``）。
        """

        # 首条消息时设置标题（截取前 30 字符，避免过长）
        if conv.title is None:
            title = question[:30].strip() or "新会话"
            self.conv_repo.update_title(conv, title)

        # user 消息（无 citations）
        self.conv_repo.add_message(
            conv.id,
            role=MessageRole.USER,
            content=question,
            citations=None,
        )
        # assistant 消息（含 citations 快照）
        # CitationRead 是 Pydantic BaseModel，转 dict 存 JSON
        citations_snapshot = [c.model_dump(mode="json") for c in citations] if citations else None
        self.conv_repo.add_message(
            conv.id,
            role=MessageRole.ASSISTANT,
            content=answer_text,
            citations=citations_snapshot,
        )

    # ------------------------------------------------------------------
    # 私有：检索 + 重排（非流式与流式共享）
    # ------------------------------------------------------------------

    def _prepare_contexts(
        self,
        question: str,
        document_ids: Sequence[uuid.UUID] | None,
        top_k: int,
    ) -> tuple[list[ContextPiece], list[uuid.UUID], BaseChatModel]:
        """检索 + 重排，返回 ``(contexts, context_doc_ids, chat_model)``。

        抽取自 ``answer`` 的步骤 1-3.5（查 READY 文档 → 惰性创建 ChatModel →
        检索 → 重排），供非流式 ``answer`` 与流式 ``answer_stream`` 共享，
        保证两条路径的检索/重排行为完全一致。

        Raises:
            DocumentNotFoundError: ``document_ids`` 中有不存在的 UUID。
            NoAvailableDocumentsError: 无可用 READY 文档或检索上下文为空。
            EmbeddingServiceError: Embedding 模型加载失败。
            VectorStoreError: 向量索引或检索失败。
        """

        # 1. 查询 READY 文档
        docs = self._get_ready_documents(document_ids)
        if not docs:
            msg = "没有可用的 READY 文档可供问答。"
            raise NoAvailableDocumentsError(msg)

        # 2. 惰性创建 ChatModel（测试时已注入，跳过创建）
        # 复用 _ensure_chat_model（与 answer / answer_stream / rewrite_query 共用）
        chat_model = self._ensure_chat_model()

        # 3. 检索：根据配置选择路径
        # - ``bm25_enabled=True``：混合检索（BM25 + 向量 + RRF 融合），阶段 8.3
        # - ``bm25_enabled=False``：纯向量检索（原有行为）
        if self.bm25_enabled:
            contexts, context_doc_ids = self._retrieve_hybrid(docs, question, top_k)
        elif self.vector_store is not None:
            contexts, context_doc_ids = self._retrieve_with_qdrant(docs, question, top_k)
        else:
            # InMemory 路径需要 Embeddings（惰性创建或测试注入）
            if self._embeddings is None:
                self._embeddings = create_embeddings(self.embedding_config)
            assert self._embeddings is not None
            contexts, context_doc_ids = self._retrieve_contexts(
                docs, question, top_k, self._embeddings
            )

        if not contexts:
            # 所有文档都没有 Chunk（理论上不应发生，READY 文档应有 Chunk）
            msg = "检索到的上下文为空：READY 文档均无 Chunk。"
            raise NoAvailableDocumentsError(msg)

        # 3.5. 重排序（如果配置了 reranker）
        # Cross-Encoder 对 Top-K 结果精排，提升 Hit@1 和引用质量。
        # contexts 和 context_doc_ids 是平行列表，重排时需同步更新两者顺序。
        if self.reranker is not None:
            contents = [ctx.content for ctx in contexts]
            scored = self.reranker.rerank(question, contents)
            # scored = [(original_index, rerank_score), ...] 按分数降序
            contexts = [dataclass_replace(contexts[idx], score=score) for idx, score in scored]
            context_doc_ids = [context_doc_ids[idx] for idx, _ in scored]

        return contexts, context_doc_ids, chat_model

    # ------------------------------------------------------------------
    # 私有：文档查询
    # ------------------------------------------------------------------

    def _get_ready_documents(
        self,
        document_ids: Sequence[uuid.UUID] | None,
    ) -> list[Document]:
        """查询符合条件的 READY 文档。

        - ``document_ids`` 为空或 None：返回全库 READY 文档。
        - ``document_ids`` 非空：逐个查询，不存在的抛 ``DocumentNotFoundError``，
          存在但非 READY 的跳过。

        Args:
            document_ids: 限定查询的文档 UUID 列表。

        Returns:
            READY 文档列表。

        Raises:
            DocumentNotFoundError: ``document_ids`` 中有不存在的 UUID。
        """

        if not document_ids:
            # 全库查询：list_all 后过滤 READY
            all_docs = self.repo.list_all()
            return [d for d in all_docs if d.status == DocumentStatus.READY]

        # 指定文档：逐个查询，不存在抛异常
        ready_docs: list[Document] = []
        for doc_id in document_ids:
            doc = self.repo.get_by_id(doc_id)
            if doc is None:
                raise DocumentNotFoundError(f"文档不存在：{doc_id}")
            if doc.status == DocumentStatus.READY:
                ready_docs.append(doc)
        return ready_docs

    # ------------------------------------------------------------------
    # 私有：Qdrant 检索（阶段 6）
    # ------------------------------------------------------------------

    def _retrieve_with_qdrant(
        self,
        docs: Sequence[Document],
        question: str,
        top_k: int,
    ) -> tuple[list[ContextPiece], list[uuid.UUID]]:
        """Qdrant 单库检索：直接 similarity search，支持 document_ids 过滤。

        与 ``_retrieve_contexts``（InMemory 多文档索引）的区别：
        - 不需要每文档单独 index_chunks（向量在上传时已写入 Qdrant）
        - ``QdrantSearchResult`` 含 ``document_id``，直接映射到 ``context_doc_ids``
        - 按 ``document_ids`` payload 过滤，而不是遍历每个文档

        Args:
            docs: READY 文档列表（用于提取 document_ids，即使 Qdrant 有
                残留向量也能限定检索范围到当前 READY 文档）。
            question: 查询问题。
            top_k: 返回的最相关片段数。

        Returns:
            ``(contexts, context_doc_ids)`` 两个平行列表。
        """

        from research_rag.vector_store import search

        # 用 READY 文档的 id 列表过滤检索范围（避免检索到已删除但 Qdrant
        # 未清理的残留向量）
        doc_ids = [doc.id for doc in docs]

        # 本方法只在 self.vector_store is not None 时被 answer 调用，
        # 用 assert 帮助 mypy 收窄类型（本项目不用 -O 优化，assert 不会被移除）
        assert self.vector_store is not None
        results = search(self.vector_store, question, doc_ids, top_k=top_k)

        contexts: list[ContextPiece] = []
        context_doc_ids: list[uuid.UUID] = []
        for r in results:
            contexts.append(
                ContextPiece(
                    document_name=r.document_name,
                    start_page=r.start_page,
                    end_page=r.end_page,
                    chunk_index=r.chunk_index,
                    content=r.content,
                    score=r.score,
                )
            )
            context_doc_ids.append(r.document_id)

        return contexts, context_doc_ids

    # ------------------------------------------------------------------
    # 私有：混合检索（BM25 + 向量 + RRF 融合，阶段 8.3）
    # ------------------------------------------------------------------

    def _retrieve_hybrid(
        self,
        docs: Sequence[Document],
        question: str,
        top_k: int,
    ) -> tuple[list[ContextPiece], list[uuid.UUID]]:
        """混合检索：BM25 + 向量并行召回 + RRF 融合（阶段 8.3）。

        构建全局 BM25 索引（覆盖所有 READY 文档的 chunks），与向量检索
        （Qdrant 或 InMemory）并行召回，取并集后用 RRF 融合排序，最终
        截断到 ``top_k``。融合后用 ``content`` 字段映射回 ``document_id``
        和 ``document_name``。

        与 ``_retrieve_with_qdrant`` / ``_retrieve_contexts`` 的区别：
        - 多路召回（BM25 + 向量），覆盖关键词/数值类问题（向量检索弱项）
        - RRF 融合两路排名，不依赖原始分数分布（BM25 与余弦尺度差异大）
        - 每次问答重建 BM25 索引（当前规模 < 100ms，可接受）

        Args:
            docs: READY 文档列表。
            question: 查询问题。
            top_k: 最终返回的最相关片段数。

        Returns:
            ``(contexts, context_doc_ids)`` 两个平行列表，按 RRF 分数降序。
            ``ContextPiece.score`` 为 RRF 分数（与原余弦/BM25 分数尺度不同）。

        Raises:
            NoAvailableDocumentsError: 文档均无 chunk，无法构建 BM25 索引。
            HybridRetrievalError: BM25 索引构建或检索失败。
            VectorStoreError: 向量检索失败。
        """
        from research_rag.hybrid_retriever import (
            DEFAULT_BM25_WEIGHT,
            DEFAULT_RECALL_MULTIPLIER,
            DEFAULT_VECTOR_WEIGHT,
            BM25Retriever,
            rrf_fusion,
        )

        # 1. 预收集所有 chunker chunks + content → (doc_id, doc_name) 映射
        # 一次遍历避免重复调用 _orm_chunks_to_chunker
        all_chunks: list[ChunkerChunk] = []
        doc_chunks_map: list[tuple[Document, list[ChunkerChunk]]] = []
        content_to_meta: dict[str, tuple[uuid.UUID, str]] = {}
        for doc in docs:
            chunks = self._orm_chunks_to_chunker(doc)
            doc_chunks_map.append((doc, chunks))
            all_chunks.extend(chunks)
            for c in chunks:
                # content 作为唯一键；不同文档有相同 content 时后写入覆盖
                # （实际场景极少，可接受）
                content_to_meta[c.content] = (doc.id, doc.original_name)

        if not all_chunks:
            msg = "BM25 索引构建失败：READY 文档均无 chunk。"
            raise NoAvailableDocumentsError(msg)

        # 2. 构建 BM25 索引
        bm25_retriever = BM25Retriever(all_chunks)

        # 3. 向量检索 + BM25 检索（多召回以提高融合效果）
        recall_k = max(top_k * DEFAULT_RECALL_MULTIPLIER, top_k)
        bm25_results = bm25_retriever.retrieve(question, top_k=recall_k)

        if self.vector_store is not None:
            # Qdrant 路径：单库检索，结果含 document_id
            from research_rag.vector_store import search as qdrant_search

            doc_ids = [doc.id for doc in docs]
            assert self.vector_store is not None
            qdrant_results = qdrant_search(self.vector_store, question, doc_ids, top_k=recall_k)
            # 转 RetrievalResult 做 RRF（统一接口）
            vector_results: list[RetrievalResult] = [
                RetrievalResult(
                    start_page=r.start_page,
                    end_page=r.end_page,
                    chunk_index=r.chunk_index,
                    content=r.content,
                    score=r.score,
                )
                for r in qdrant_results
            ]
            # Qdrant 结果的 document_id 是权威来源，补充到映射（覆盖可能的推断）
            for r in qdrant_results:
                content_to_meta[r.content] = (r.document_id, r.document_name)
        else:
            # InMemory 路径：每文档独立索引 + 检索，合并为全局向量结果
            if self._embeddings is None:
                self._embeddings = create_embeddings(self.embedding_config)
            assert self._embeddings is not None
            vector_results = []
            for _doc, chunks in doc_chunks_map:
                if not chunks:
                    continue
                store = index_chunks(chunks, self._embeddings)
                results = retrieve(store, question, top_k=recall_k)
                vector_results.extend(results)

        # 4. 加权 RRF 融合 + 截断到 top_k（向量 2 倍权重，减少 BM25 噪声干扰）
        fused = rrf_fusion(
            vector_results,
            bm25_results,
            top_k=top_k,
            vector_weight=DEFAULT_VECTOR_WEIGHT,
            bm25_weight=DEFAULT_BM25_WEIGHT,
        )

        # 5. 用 content 映射回 doc_id 和 doc_name，构造 ContextPiece
        contexts: list[ContextPiece] = []
        context_doc_ids: list[uuid.UUID] = []
        for fused_result in fused:
            # BM25 召回的 content 一定在 all_chunks 中，因此映射必命中
            doc_id, doc_name = content_to_meta.get(fused_result.content, (uuid.UUID(int=0), ""))
            contexts.append(
                ContextPiece(
                    document_name=doc_name,
                    start_page=fused_result.start_page,
                    end_page=fused_result.end_page,
                    chunk_index=fused_result.chunk_index,
                    content=fused_result.content,
                    score=fused_result.score,
                )
            )
            context_doc_ids.append(doc_id)

        return contexts, context_doc_ids

    # ------------------------------------------------------------------
    # 私有：多文档检索（InMemory 回退路径）
    # ------------------------------------------------------------------

    def _retrieve_contexts(
        self,
        docs: Sequence[Document],
        question: str,
        top_k: int,
        embeddings: Embeddings,
    ) -> tuple[list[ContextPiece], list[uuid.UUID]]:
        """多文档检索：每文档单独索引+检索，合并后取全局 top_k。

        ``embedding.retrieve`` 返回的 ``RetrievalResult`` 不含 ``document_id``，
        单文档索引能明确知道检索结果属于哪个文档。合并后按 ``score`` 降序排序
        取全局 top_k，保证最相关的片段入选。

        Args:
            docs: READY 文档列表。
            question: 查询问题。
            top_k: 全局返回的最相关片段数。
            embeddings: LangChain ``Embeddings`` 实例（由 ``answer`` 方法
                惰性创建或测试注入后传入）。

        Returns:
            ``(contexts, context_doc_ids)`` 两个平行列表：
            - ``contexts``: ``ContextPiece`` 列表（按相关度降序）。
            - ``context_doc_ids``: 与 ``contexts`` 一一对应的 ``document_id`` 列表。
        """

        # 收集所有检索结果，附带 document_id 和 document_name
        # 结构：(RetrievalResult, document_id, document_name)
        all_retrieval: list[tuple[RetrievalResult, uuid.UUID, str]] = []

        for doc in docs:
            # ORM Chunk → chunker Chunk（index_chunks 接受 chunker.Chunk）
            chunker_chunks = self._orm_chunks_to_chunker(doc)
            if not chunker_chunks:
                continue

            # 每文档单独索引+检索
            store = index_chunks(chunker_chunks, embeddings)
            results = retrieve(store, question, top_k=top_k)

            for r in results:
                all_retrieval.append((r, doc.id, doc.original_name))

        # 按 score 降序排序取全局 top_k
        all_retrieval.sort(key=lambda x: x[0].score, reverse=True)
        top_retrieval = all_retrieval[:top_k]

        # 构造平行列表：contexts 和 context_doc_ids
        contexts: list[ContextPiece] = []
        context_doc_ids: list[uuid.UUID] = []
        for r, doc_id, doc_name in top_retrieval:
            contexts.append(
                ContextPiece(
                    document_name=doc_name,
                    start_page=r.start_page,
                    end_page=r.end_page,
                    chunk_index=r.chunk_index,
                    content=r.content,
                    score=r.score,
                )
            )
            context_doc_ids.append(doc_id)

        return contexts, context_doc_ids

    # ------------------------------------------------------------------
    # 私有：引用映射
    # ------------------------------------------------------------------

    @staticmethod
    def _map_citations(
        result: AnswerWithCitations,
        contexts: Sequence[ContextPiece],
        context_doc_ids: Sequence[uuid.UUID],
    ) -> list[CitationRead]:
        """把 ``answer_question`` 的结果映射为 ``CitationRead`` 列表。

        ``citation_indices`` 是上下文编号（从 1 开始），与 ``contexts`` 列表
        顺序一致。通过 ``contexts[idx - 1]`` 直接获取 ``ContextPiece``，
        配合 ``context_doc_ids[idx - 1]`` 获取 ``document_id``。

        越界编号（模型偶尔输出）静默跳过，与 ``qa_service.map_citations`` 策略一致。
        """

        citations: list[CitationRead] = []
        for idx in result.citation_indices:
            if idx <= 0 or idx > len(contexts):
                continue
            ctx = contexts[idx - 1]
            citations.append(
                CitationRead(
                    document_id=context_doc_ids[idx - 1],
                    document_name=ctx.document_name,
                    start_page=ctx.start_page,
                    end_page=ctx.end_page,
                    chunk_index=ctx.chunk_index,
                    snippet=ctx.content,
                    score=ctx.score,
                )
            )
        return citations

    # ------------------------------------------------------------------
    # 私有：ORM Chunk → chunker Chunk 转换
    # ------------------------------------------------------------------

    @staticmethod
    def _orm_chunks_to_chunker(doc: Document) -> list[ChunkerChunk]:
        """把 ORM ``Chunk`` 列表转为 chunker ``Chunk`` dataclass 列表。

        ``embedding.index_chunks`` 接受 chunker 的 ``Chunk``（dataclass），
        不接受 ORM ``Chunk``。本函数做字段映射，``vector_id`` / ``created_at``
        等 ORM 专属字段丢弃。
        """

        return [
            ChunkerChunk(
                start_page=c.start_page,
                end_page=c.end_page,
                chunk_index=c.chunk_index,
                content=c.content,
                char_count=c.char_count,
            )
            for c in doc.chunks
        ]
