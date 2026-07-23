"""文档管理与问答 API 的 Pydantic schema。

依据 PROJECT_PLAN.md 第 8.2 节（上传文档响应）、第 8.4 节（文档问答）、
第 8.5 节（错误响应）、第 7.1 节（Document 字段）。

设计取舍（初学者向说明）：
- **Pydantic schema 与 ORM 模型分离**：``DocumentRead`` 不直接复用
  ``db.models.Document``，而是单独定义。理由：① API 响应字段可能与 ORM 字段
  不完全一致（未来可能加 ``chunk_count`` 等聚合字段）；② 避免把 ORM 内部
  实现（如 ``relationship``）暴露给 API；③ 符合 PROJECT_PLAN 第 10 节分层。
- **``from_attributes=True``**：Pydantic v2 配置，让
  ``DocumentRead.model_validate(orm_doc)`` 能直接从 ORM 对象的属性读值
  （``orm_doc.id`` / ``orm_doc.status`` 等），无需手写转换函数。
- **``status`` 用 ``DocumentStatus`` 枚举**：Pydantic v2 序列化时自动用枚举值
  （``"ready"`` 而非 ``"READY"``），与数据库存储和 API 草案一致，便于前端
  解析和 SQL 排查。
- **``ErrorResponse`` 统一错误格式**：所有异常处理器返回 ``{"detail": "..."}``，
  与 FastAPI 默认 ``HTTPException`` 格式一致，前端只需一种解析逻辑
  （PROJECT_PLAN 第 8.5 节示意 ``{"error": {...}}``，本 Issue 采用 FastAPI
  惯例的 ``{"detail": "..."}``，更贴合框架生态，减少自定义中间件）。
- **``QueryRequest.top_k`` 用 ``default_factory``**：每次实例化时从环境变量
  ``RETRIEVAL_TOP_K`` 读取，未设置时用 ``embedding.DEFAULT_TOP_K``（8）。
  这样部署时改环境变量即可调整，无需改代码；测试时可临时 ``monkeypatch``
  环境变量或直接传参覆盖。
- **``CitationRead`` 含 ``chunk_index``**：PROJECT_PLAN 第 8.4 节响应示例未列
  ``chunk_index``，但用户明确要求加入以便前端精确定位片段在文档中的位置
  （溯源到具体分段），对齐 US-003"返回可核查的文档名称、页码和原文片段"。
- **``QueryResponse.request_id`` / ``elapsed_ms``**：对齐第 8.4 节响应结构。
  ``request_id`` 由 service 层生成（``uuid.uuid4``），便于日志追踪单次问答；
  ``elapsed_ms`` 由 service 层在编排开始/结束时计时，前端可据此展示耗时。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from research_rag.db.models import DocumentStatus
from research_rag.embedding import DEFAULT_TOP_K


class DocumentRead(BaseModel):
    """文档详情响应（PROJECT_PLAN.md 第 8.2 节、第 7.1 节）。

    字段与 ``Document`` ORM 模型一一对应，``from_attributes=True`` 让
    ``model_validate(orm_doc)`` 直接读 ORM 属性。``id`` 用 ``uuid.UUID`` 类型，
    FastAPI 序列化时自动转为字符串（与第 8.2 节示例 ``"id": "document-uuid"``
    一致），同时保留类型安全。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    stored_name: str
    sha256: str
    page_count: int
    status: DocumentStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    """文档列表响应：包裹 ``items`` 数组，便于后续加分页字段。"""

    items: list[DocumentRead]


class ErrorResponse(BaseModel):
    """统一错误响应（PROJECT_PLAN.md 第 8.5 节）。"""

    detail: str


# ---------------------------------------------------------------------------
# 问答 API schema（PROJECT_PLAN.md 第 8.4 节）
# ---------------------------------------------------------------------------


def _get_default_top_k() -> int:
    """从环境变量 ``RETRIEVAL_TOP_K`` 读取默认 top_k，未设置时用 ``DEFAULT_TOP_K``。

    作为 ``QueryRequest.top_k`` 的 ``default_factory``：每次实例化
    ``QueryRequest`` 时调用，确保运行时改环境变量能生效。解析失败时回退到
    ``DEFAULT_TOP_K``，避免格式错误导致请求失败。
    """

    raw = os.environ.get("RETRIEVAL_TOP_K")
    if raw is None:
        return DEFAULT_TOP_K
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TOP_K
    return value if value > 0 else DEFAULT_TOP_K


class CitationRead(BaseModel):
    """单条引用响应（PROJECT_PLAN.md 第 8.4 节响应结构 + chunk_index 扩展）。

    对齐第 8.4 节 ``citations`` 数组元素，额外加 ``chunk_index`` 便于前端
    精确定位片段在文档中的位置。

    Attributes:
        document_id: 来源文档 UUID（序列化为字符串）。
        document_name: 来源文档名（``Document.original_name``）。
        start_page: chunk 内容起始页码（与 ``Chunk.start_page`` 一致）。
        end_page: chunk 内容结束页码（与 ``Chunk.end_page`` 一致）。跨页切分时
            ``end_page > start_page``，不跨页时 ``end_page == start_page``。
        chunk_index: 文档内分段序号（与 ``Chunk.chunk_index`` 一致）。
        snippet: 原文片段（``Chunk.content``）。
        score: 检索相似度分数，越高越相关。
    """

    document_id: uuid.UUID
    document_name: str
    start_page: int
    end_page: int
    chunk_index: int
    snippet: str
    score: float


class QueryRequest(BaseModel):
    """问答请求（PROJECT_PLAN.md 第 8.4 节请求结构 + 阶段 9.1 流式 + 9.2 多轮）。

    Attributes:
        question: 用户问题，必填，非空字符串。
        document_ids: 限定查询的文档 UUID 列表。空列表（默认）表示查询全库
            ``status=ready`` 的文档。``conversation_id`` 非 None 时，若会话已
            锁定 ``document_ids``，则忽略请求传入的 ``document_ids``，以会话
            锁定范围为准（保证多轮上下文一致性）。
        top_k: 检索返回的最相关片段数。默认从环境变量 ``RETRIEVAL_TOP_K``
            读取，未设置或非法时回退到 ``DEFAULT_TOP_K``（8）。
        stream: 是否以 SSE 流式返回 LLM 生成内容（阶段 9.1）。``False``（默认）
            返回完整 ``QueryResponse`` JSON；``True`` 返回
            ``text/event-stream``，事件类型 ``token`` / ``done`` / ``error``。
        conversation_id: 会话 ID（阶段 9.2 多轮对话）。``None``（默认）表示
            单轮问答（不注入历史）；传入已存在的会话 ID 时，从 DB 加载历史
            对话注入 prompt，并把本轮问答持久化到该会话。
    """

    question: str = Field(min_length=1)
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    top_k: int = Field(default_factory=_get_default_top_k)
    stream: bool = False
    conversation_id: uuid.UUID | None = None


class QueryResponse(BaseModel):
    """问答响应（PROJECT_PLAN.md 第 8.4 节响应结构 + 阶段 9.2 多轮）。

    Attributes:
        answer: 模型生成的答案文本（含 ``[C1]`` 等引用标记）。
        citations: 引用列表，按模型引用顺序排列。
        request_id: 本次问答的唯一 ID（``uuid.uuid4`` 生成），便于日志追踪。
        elapsed_ms: 本次问答总耗时（毫秒），从 service 编排开始到结束。
        conversation_id: 本次问答所属会话 ID（阶段 9.2）。``None`` 表示单轮
            问答未关联会话；非 None 时前端可据此维护会话状态。
    """

    answer: str
    citations: list[CitationRead]
    request_id: uuid.UUID
    elapsed_ms: int
    conversation_id: uuid.UUID | None = None


# ---------------------------------------------------------------------------
# 会话 API schema（阶段 9.2 多轮对话）
# ---------------------------------------------------------------------------


class ConversationCreate(BaseModel):
    """创建会话请求（阶段 9.2）。

    Attributes:
        title: 会话标题（可空）。未提供时，service 层可在首条消息后用问题
            截取自动设置。
        document_ids: 会话级文档范围（UUID 列表）。``None`` 或空列表表示查询
            全库 READY 文档。在会话创建时锁定，后续问答以此范围为准。
    """

    title: str | None = None
    document_ids: list[uuid.UUID] | None = None


class MessageRead(BaseModel):
    """会话消息响应（阶段 9.2）。

    ``from_attributes=True`` 让 ``model_validate(orm_message)`` 直接读 ORM
    ``Message`` 属性。``citations`` 字段在 ORM 中是 JSON ``list[dict]``，
    Pydantic v2 自动把每个 ``dict`` 构造为 ``CitationRead``。

    Attributes:
        id: 消息 UUID。
        role: 消息角色（``user`` / ``assistant``）。
        content: 消息文本。``assistant`` 消息含 ``[C1]`` 等引用标记原文。
        citations: ``assistant`` 消息的引用元数据快照；``user`` 消息为 ``None``。
        created_at: 创建时间（UTC）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    citations: list[CitationRead] | None = None
    created_at: datetime


class ConversationRead(BaseModel):
    """会话响应（阶段 9.2）。

    Attributes:
        id: 会话 UUID。
        title: 会话标题（可空）。
        document_ids: 会话级文档范围（UUID 字符串列表快照）；``None`` 表示全库。
        created_at: 创建时间（UTC）。
        updated_at: 最后更新时间（UTC），每次新增消息时自动维护。
        messages: 会话内消息列表（按 ``created_at`` 升序）。``None`` 表示未加载
            （列表接口为节省体积不返回消息，详情接口返回完整消息）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    document_ids: list[str] | None
    created_at: datetime
    updated_at: datetime
    messages: list[MessageRead] | None = None


class ConversationList(BaseModel):
    """会话列表响应：包裹 ``items`` 数组（不含消息，节省体积）。"""

    items: list[ConversationRead]
