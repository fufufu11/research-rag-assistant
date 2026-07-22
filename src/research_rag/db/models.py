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

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, Uuid
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
    """文档分段模型（PROJECT_PLAN.md 第 7.2 节）。

    Attributes:
        id: UUID 主键。
        document_id: 所属文档的 UUID（外键，``ondelete="CASCADE"``）。
        page_number: 来源页码，从 1 开始（与 ``PageInfo`` / ``Chunk`` 一致）。
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
    page_number: Mapped[int] = mapped_column(nullable=False)
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
            f"page_number={self.page_number!r}, chunk_index={self.chunk_index!r})"
        )
