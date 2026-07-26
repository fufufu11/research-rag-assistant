"""SQLAlchemy 数据模型。

依据 PROJECT_PLAN.md 第 709 节（阶段 5 交付物）、第 7.1 节（Document）、
第 7.2 节（Chunk）、第 13.6 节（项目级异常清单）。

设计取舍（初学者向说明）：
- 用 SQLAlchemy 2.0 的 ``DeclarativeBase`` + ``Mapped`` + ``mapped_column``：
  相比 1.x 的 ``Column(...)``，2.0 风格在类型层面暴露字段类型（``Mapped[str]``
  而非 ``Column[str]``），mypy 能据此检查赋值，IDE 也能补全。``Mapped[str | None]``
  会让 SQLAlchemy 自动推断 ``nullable=True``，类型与约束保持一致。
- UUID 主键：用 SQLAlchemy 2.0 内置的 ``Uuid`` 类型，跨数据库兼容
  （SQLite 存为 32 字符字符串，PostgreSQL 用原生 UUID）。Python 端用
  ``uuid.UUID``，业务层生成（``default=uuid.uuid4``），避免依赖数据库函数。
- ``status`` 用 Python ``enum.Enum`` + ``SAEnum``：``values_callable`` 让
  数据库存 ``"pending"`` 而不是 ``"PENDING"``，便于 SQL 排查和 API 返回。
- ``created_at`` / ``updated_at`` 用 Python 端 ``default`` / ``onupdate``：
  不用 ``server_default`` 因为 SQLite 缺少标准时间函数（``CURRENT_TIMESTAMP``
  跨时区行为不一致）。``onupdate`` 是 ORM 层的，仅 ORM 操作触发；后续若有
  裸 SQL 更新需注意手动维护 ``updated_at``。
- ``Document`` → ``Chunk`` 用 ``relationship`` + ``cascade="all, delete-orphan"``：
  删除 ``Document`` 时自动删除其 ``Chunk``（PROJECT_PLAN US-002 要求"删除文档
  时同时删除元数据、文本分段"）。数据库层 ``ondelete="CASCADE"`` 作为兜底
  （裸 SQL 删除文档时也能级联）。
- 异常放本模块：与现有 ``pdf_parser`` / ``embedding`` / ``qa_service`` 风格一致
  （异常和数据结构同模块）。``DuplicateDocumentError`` / ``DocumentNotFoundError``
  对应 PROJECT_PLAN 第 13.6 节异常清单，后续 repository 与 API 层会捕获并映射。
- ``vector_id`` 暂为可空字符串：本 Issue 不写入向量库（阶段 6 才接 Qdrant），
  保留字段是为了避免后续加列的迁移成本。
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class DocumentStatus(enum.Enum):
    """文档处理状态（PROJECT_PLAN.md 第 7.1 节）。

    值用小写字符串，便于 SQL 排查和 API 返回。状态流转：
    ``pending`` → ``processing`` → ``ready``（成功）或 ``failed``（失败）。
    """

    PENDING = "pending"  # 已上传，待处理
    PROCESSING = "processing"  # 正在解析 / 切分 / Embedding
    READY = "ready"  # 处理完成，可问答
    FAILED = "failed"  # 处理失败，error_message 存原因


class MessageRole(enum.Enum):
    """会话消息角色（阶段 9.2 多轮对话）。

    值用小写字符串，与 OpenAI/LangChain 消息角色命名一致（``user`` / ``assistant``），
    便于历史消息直接映射为 LangChain ``HumanMessage`` / ``AIMessage``。
    """

    USER = "user"  # 用户提问
    ASSISTANT = "assistant"  # 模型回答


class FeedbackRating(enum.Enum):
    """用户反馈类型（阶段 10.2 用户反馈闭环）。

    值用小写字符串，与 ``DocumentStatus`` / ``MessageRole`` 风格一致，便于
    SQL 排查和 API 返回。二值对齐"点赞/点踩"语义。
    """

    LIKE = "like"  # 点赞
    DISLIKE = "dislike"  # 点踩


class DuplicateDocumentError(RuntimeError):
    """重复文档异常。

    同一 ``sha256`` 的文档已存在时抛出。对应 PROJECT_PLAN.md 第 13.6 节
    异常清单与 US-001"同一文件重复上传时不得产生重复数据"约束。
    """


class DocumentNotFoundError(RuntimeError):
    """文档未找到异常。

    按 ID 查询文档不存在时抛出。对应 PROJECT_PLAN.md 第 13.6 节异常清单
    与 US-002"文件不存在时返回规范的 404 响应"约束（API 层捕获后映射到
    HTTP 404）。
    """


class FeedbackNotFoundError(RuntimeError):
    """反馈未找到异常（阶段 10.2 用户反馈闭环）。

    按 ``request_id`` 查询/删除反馈不存在时抛出。API 层捕获后映射到 HTTP 404。
    """


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。

    Alembic 的 ``env.py`` 通过 ``Base.metadata`` 对比数据库与模型差异，
    自动生成迁移脚本。新增表只需继承 ``Base`` 并定义 ``__tablename__``。
    """


def _utcnow() -> datetime:
    """当前 UTC 时间（naive，去掉 tzinfo 以兼容 SQLite 的 DateTime 存储）。

    用 naive UTC 而非 timezone-aware：SQLAlchemy 的 ``DateTime`` 在 SQLite
    中存为 ISO 字符串，timezone-aware 会带 ``+00:00``，跨数据库迁移时与
    PostgreSQL 的 ``TIMESTAMP WITHOUT TIME ZONE`` 行为不一致。
    """

    return datetime.now(UTC).replace(tzinfo=None)


class Document(Base):
    """文档元数据模型（PROJECT_PLAN.md 第 7.1 节）。

    Attributes:
        id: UUID 主键，业务层生成（``default=uuid.uuid4``）。
        original_name: 用户上传时的文件名（用于展示）。
        stored_name: 服务端生成的安全文件名（避免路径遍历攻击）。
        sha256: 文件内容哈希，用于去重（US-001），建唯一索引。
        page_count: PDF 页数，解析后填入；默认 0 表示尚未解析。
        status: 处理状态枚举，默认 ``PENDING``。
        error_message: 处理失败原因，``status=FAILED`` 时填入；可空。
        created_at: 创建时间（UTC），ORM 层 ``default`` 自动填入。
        updated_at: 更新时间（UTC），ORM 层 ``onupdate`` 自动维护。
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    page_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(
            DocumentStatus,
            values_callable=lambda e: [x.value for x in e],
            length=20,
            native_enum=False,
        ),
        nullable=False,
        default=DocumentStatus.PENDING,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow, onupdate=_utcnow)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"Document(id={self.id!r}, original_name={self.original_name!r}, "
            f"status={self.status!r})"
        )


class Chunk(Base):
    """文档分段模型（PROJECT_PLAN.md 第 7.2 节，阶段 8.2 跨页切分扩展）。

    Attributes:
        id: UUID 主键。
        document_id: 所属文档的 UUID（外键，``ondelete="CASCADE"``）。
        start_page: chunk 内容起始页码，从 1 开始（与 ``PageInfo`` / ``Chunk`` 一致）。
        end_page: chunk 内容结束页码。不跨页时 ``end_page == start_page``。
        chunk_index: 文档内分段序号，从 0 开始。
        content: 分段文本。
        char_count: 字符数。
        vector_id: 向量库中的 ID（阶段 6 Qdrant 用），本 Issue 不写入；可空。
        created_at: 创建时间（UTC）。
    """

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_page: Mapped[int] = mapped_column(nullable=False)
    end_page: Mapped[int] = mapped_column(nullable=False)
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(nullable=False)
    vector_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        # 文档内分段序号唯一：避免同一文档出现两个 chunk_index=0。
        # 用 UniqueConstraint 而非普通索引，因为这是数据正确性约束。
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_doc_chunk_idx"),
    )

    def __repr__(self) -> str:
        return (
            f"Chunk(id={self.id!r}, document_id={self.document_id!r}, "
            f"start_page={self.start_page!r}, end_page={self.end_page!r}, "
            f"chunk_index={self.chunk_index!r})"
        )


class Conversation(Base):
    """会话模型（阶段 9.2 多轮对话）。

    一个会话对应一组连续的多轮问答。会话级文档范围（``document_ids``）在创建时
    锁定，避免中途切换导致历史上下文不一致。会话内消息按 ``created_at`` 升序
    构成对话历史。

    Attributes:
        id: UUID 主键，业务层生成（``default=uuid.uuid4``）。
        title: 会话标题（可空），用于 UI 展示。通常从首条用户问题截取。
        document_ids: 会话级文档范围（UUID 字符串列表）。``None`` 表示查询全库
            READY 文档。存 JSON 而非外键，因为这是会话创建时的快照，不随文档
            删除而级联（文档删除后会话历史仍保留，新问答会因文档不存在而报错）。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC），每次新增消息时由 ``onupdate`` 自动维护。
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow, onupdate=_utcnow)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    def __repr__(self) -> str:
        return f"Conversation(id={self.id!r}, title={self.title!r})"


class Message(Base):
    """会话消息模型（阶段 9.2 多轮对话）。

    一条消息对应一轮对话中的一方（用户或模型）。``assistant`` 消息的
    ``citations`` 字段存引用元数据快照（JSON），便于前端历史回看，不随文档
    删除而级联（快照语义）。

    Attributes:
        id: UUID 主键。
        conversation_id: 所属会话的 UUID（外键，``ondelete="CASCADE"``）。
        role: 消息角色（``user`` / ``assistant``）。
        content: 消息文本内容。``assistant`` 消息含 ``[C1]`` 等引用标记原文。
        citations: ``assistant`` 消息的引用元数据快照（list[dict]），结构对齐
            ``CitationRead`` schema。``user`` 消息为 ``None``。
        created_at: 创建时间（UTC），用于消息排序。
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(
            MessageRole,
            values_callable=lambda e: [x.value for x in e],
            length=20,
            native_enum=False,
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, object]] | None] = mapped_column(JSON, nullable=True)
    # request_id 持久化到 Message（ADR 0003）：仅 assistant 消息写入，
    # user 消息与旧消息（迁移前）保持 None。加唯一约束供历史消息反馈反查。
    # 多条 NULL 不冲突（SQL 标准对 NULL 的唯一约束语义）。
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return (
            f"Message(id={self.id!r}, conversation_id={self.conversation_id!r}, role={self.role!r})"
        )


class Feedback(Base):
    """用户反馈模型（阶段 10.2 用户反馈闭环）。

    一条反馈对应一次问答答案的评价（点赞/点踩 + 可空评论）。以 ``request_id``
    为主关联键（唯一约束，Upsert 语义），额外保留可空 ``message_id`` 外键供
    多轮场景 join 消息内容。详见 ADR 0001。

    Attributes:
        id: UUID 主键，业务层生成（``default=uuid.uuid4``）。
        request_id: 关联的问答 request_id（``QaService`` 生成并返回前端）。
            加唯一约束：同一答案只有一条反馈，POST 走 Upsert（创建或更新），
            兼作匿名防刷。
        message_id: 关联的 assistant 消息 UUID（可空，FK→messages.id，
            ``ondelete=SET NULL``）。单轮问答（无会话）不持久化 Message，
            此时为 ``None``，仅靠 ``request_id`` 关联。消息删除时反馈记录
            保留（``SET NULL`` 而非 ``CASCADE``），用于事后分析。
        rating: 反馈类型（``like`` / ``dislike``）。
        comment: 文字评论（可空）。点踩时收集原因，为持续优化提供信号。
        created_at: 创建时间（UTC）。
        updated_at: 更新时间（UTC），Upsert 更新 rating/comment 时由
            ``onupdate`` 自动维护。
    """

    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True, index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rating: Mapped[FeedbackRating] = mapped_column(
        SAEnum(
            FeedbackRating,
            values_callable=lambda e: [x.value for x in e],
            length=20,
            native_enum=False,
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow, onupdate=_utcnow)

    def __repr__(self) -> str:
        return f"Feedback(id={self.id!r}, request_id={self.request_id!r}, rating={self.rating!r})"
