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
from typing import TYPE_CHECKING

from research_rag.api.schemas import CitationRead, QueryResponse
from research_rag.chunker import Chunk as ChunkerChunk
from research_rag.db.models import Document, DocumentNotFoundError, DocumentStatus
from research_rag.db.repositories import DocumentRepository
from research_rag.embedding import (
    DEFAULT_TOP_K,
    EmbeddingConfig,
    RetrievalResult,
    create_embeddings,
    index_chunks,
    retrieve,
)
from research_rag.qa_service import (
    AnswerWithCitations,
    ContextPiece,
    LlmConfig,
    answer_question,
    create_chat_model,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_qdrant import QdrantVectorStore
    from sqlalchemy.orm import Session


class NoAvailableDocumentsError(RuntimeError):
    """无可用文档异常。

    当全库无 ``status=ready`` 的文档、或指定的 ``document_ids`` 均非 READY
    时抛出。对应 PROJECT_PLAN.md 第 13.6 节异常清单的扩展（"无可用文档"场景）。

    API 层捕获后映射为 HTTP 404（语义：没有可问答的内容）。
    """


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
    ) -> None:
        self.session = session
        self.repo = DocumentRepository(session)
        self.llm_config = llm_config
        self.embedding_config = embedding_config or EmbeddingConfig()
        self._embeddings = embeddings
        self._chat_model = chat_model
        self.vector_store = vector_store

    # ------------------------------------------------------------------
    # 问答主流程
    # ------------------------------------------------------------------

    def answer(
        self,
        question: str,
        document_ids: Sequence[uuid.UUID] | None = None,
        top_k: int = DEFAULT_TOP_K,
    ) -> QueryResponse:
        """完整问答流程：查文档 → 索引检索 → LLM 问答 → 组装响应。

        流程（PROJECT_PLAN.md 第 6.2 节）：
        1. 查询符合条件的 READY 文档（全部或按 ``document_ids`` 过滤）
        2. 收集这些文档的 Chunk
        3. 每文档单独 ``index_chunks`` + ``retrieve``，合并后取全局 top_k
        4. 构造 ``ContextPiece`` 列表（含 document_name / page_number 等）
        5. 调 ``answer_question`` 让 LLM 基于上下文作答
        6. 用 ``citation_indices`` 映射到 ``CitationRead`` 列表
        7. 组装 ``QueryResponse``（含 request_id 和 elapsed_ms）

        Args:
            question: 用户问题。
            document_ids: 限定查询的文档 UUID 列表。``None`` 或空列表表示
                查询全库 READY 文档。列表中有不存在的 UUID 会抛
                ``DocumentNotFoundError``。
            top_k: 检索返回的最相关片段数。

        Returns:
            ``QueryResponse`` 实例。

        Raises:
            DocumentNotFoundError: ``document_ids`` 中有不存在的 UUID。
            NoAvailableDocumentsError: 无可用 READY 文档。
            LlmServiceError: LLM 调用失败。
            InsufficientEvidenceError: 上下文证据不足以回答。
            EmbeddingServiceError: Embedding 模型加载失败。
            VectorStoreError: 向量索引或检索失败。
        """

        request_id = uuid.uuid4()
        start = time.perf_counter()

        # 1. 查询 READY 文档
        docs = self._get_ready_documents(document_ids)
        if not docs:
            msg = "没有可用的 READY 文档可供问答。"
            raise NoAvailableDocumentsError(msg)

        # 2. 惰性创建 ChatModel（测试时已注入，跳过创建）
        # Qdrant 路径不需要 Embeddings（QdrantVectorStore 内部有），
        # InMemory 路径在 _retrieve_contexts 中按需创建
        if self._chat_model is None:
            self._chat_model = create_chat_model(self.llm_config)

        # 此时 chat_model 已确保非 None（惰性创建或测试注入）
        # 用 assert 帮助 mypy 收窄类型，本项目不使用 -O 优化，assert 不会被移除
        assert self._chat_model is not None
        chat_model = self._chat_model

        # 3. 向量检索：有 Qdrant 走单库检索，否则回退到 InMemory 多文档索引
        if self.vector_store is not None:
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

        # 4. 调 LLM 问答
        result = answer_question(question, contexts, chat_model)

        # 5. 映射引用
        citations = self._map_citations(result, contexts, context_doc_ids)

        elapsed_ms = int((time.perf_counter() - start) * 1000)

        return QueryResponse(
            answer=result.answer_text,
            citations=citations,
            request_id=request_id,
            elapsed_ms=elapsed_ms,
        )

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

        results = search(self.vector_store, question, doc_ids, top_k=top_k)

        contexts: list[ContextPiece] = []
        context_doc_ids: list[uuid.UUID] = []
        for r in results:
            contexts.append(
                ContextPiece(
                    document_name=r.document_name,
                    page_number=r.page_number,
                    chunk_index=r.chunk_index,
                    content=r.content,
                    score=r.score,
                )
            )
            context_doc_ids.append(r.document_id)

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
                    page_number=r.page_number,
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
                    page_number=ctx.page_number,
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
                page_number=c.page_number,
                chunk_index=c.chunk_index,
                content=c.content,
                char_count=c.char_count,
            )
            for c in doc.chunks
        ]
