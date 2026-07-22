"""文档与分段的数据访问层（Repository）。

依据 PROJECT_PLAN.md 第 10 节（仓库结构：``db/repositories.py``）、
第 6.1 节（文档处理流程）、US-001 / US-002。

设计取舍（初学者向说明）：
- **Repository 模式**：把所有数据库操作集中到一个类里，业务层（``services/``）
  只调用 Repository 的方法，不直接写 SQL 或 ORM 查询。好处是：① 数据访问逻辑
  可集中测试（用内存 SQLite）；② 未来换数据库（SQLite → PostgreSQL）或加缓存
  时，只改 Repository，业务层不动；③ 符合 PROJECT_PLAN 第 10 节分层结构。
- **不做泛型 BaseRepository**：当前只有 Document / Chunk 两张表，泛型基类
  会增加抽象层次而不带来实际收益。等表多了再重构。
- **Repository 不做业务编排**：不计算 sha256、不落盘文件、不调 parse_pdf。
  这些是 service 层的职责。Repository 只做"存到数据库 / 从数据库查 / 从数据库
  删"，是纯数据访问，无副作用（除数据库本身），易测试。
- **方法返回 ORM 对象而非 dict**：直接返回 ``Document`` / ``Chunk`` 实例，
  service 层可以访问属性（``doc.status``），也能传给 FastAPI 的 Pydantic
  schema 序列化。需要时 service 层自己转 dict。
- **``update_status`` 同时更新 ``error_message``**：状态和错误信息总是一起
  变化（FAILED 时记原因，READY 时清空），合并成一次调用避免漏改。
- **``add_chunks`` 接受 ORM ``Chunk`` 列表**：service 层负责把 chunker 的
  dataclass ``Chunk`` 转成 ORM ``Chunk``（业务转换），repository 只负责
  持久化（数据访问）。这样 repository 不依赖 chunker 模块。
- **不在 repository 里 commit**：事务边界由 service 层控制（service 决定
  何时提交整个业务流程）。repository 只 ``flush``（把改动推到数据库但不
  提交事务），让 service 可以在失败时回滚整个流程。这是 Repository 模式
  的常见做法：repository 不拥有事务，事务由上层管理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from research_rag.db.models import Chunk, Document, DocumentStatus

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.orm import Session


class DocumentRepository:
    """文档与分段的数据访问对象。

    封装 ``Document`` / ``Chunk`` 表的 CRUD 操作。所有方法都不 ``commit``，
    只 ``flush``，事务边界由调用方（service 层）控制。

    Attributes:
        session: SQLAlchemy Session，由调用方传入（service 层从
            ``create_session_factory`` 获取后传入）。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Document CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        original_name: str,
        stored_name: str,
        sha256: str,
        page_count: int = 0,
        status: DocumentStatus = DocumentStatus.PENDING,
    ) -> Document:
        """创建一条 Document 记录并 flush（不 commit）。

        Args:
            original_name: 用户上传时的文件名（展示用）。
            stored_name: 服务端生成的安全文件名（sha256 前缀 + 扩展名）。
            sha256: 文件内容哈希，用于去重（唯一索引）。
            page_count: PDF 页数，默认 0 表示尚未解析。
            status: 初始状态，默认 PENDING。

        Returns:
            已持久化（flush 后 id 已生成）的 Document 实例。
        """

        doc = Document(
            original_name=original_name,
            stored_name=stored_name,
            sha256=sha256,
            page_count=page_count,
            status=status,
        )
        self.session.add(doc)
        self.session.flush()
        return doc

    def get_by_id(self, doc_id: uuid.UUID) -> Document | None:
        """按主键查询 Document，不存在返回 None。"""

        return self.session.get(Document, doc_id)

    def get_by_sha256(self, sha256: str) -> Document | None:
        """按 sha256 查询 Document（去重场景用），不存在返回 None。"""

        return self.session.scalar(select(Document).where(Document.sha256 == sha256))

    def list_all(self) -> list[Document]:
        """返回所有 Document，按创建时间降序（最新的在前）。"""

        return list(
            self.session.scalars(select(Document).order_by(Document.created_at.desc())).all()
        )

    def delete(self, doc: Document) -> None:
        """删除 Document（级联删除其 Chunk，由 ORM cascade 保证）。

        注意：调用方需在之后 ``commit`` 才真正生效。
        """

        self.session.delete(doc)
        self.session.flush()

    def update_status(
        self,
        doc: Document,
        status: DocumentStatus,
        error_message: str | None = None,
    ) -> Document:
        """更新 Document 状态与错误信息，flush（不 commit）。

        Args:
            doc: 要更新的 Document 实例（已持久化）。
            status: 新状态。
            error_message: 错误信息。``status=FAILED`` 时填失败原因；
                ``status=READY`` / ``PROCESSING`` 时传 None 清空。

        Returns:
            更新后的 Document（同一实例，属性已修改）。
        """

        doc.status = status
        doc.error_message = error_message
        self.session.flush()
        return doc

    def update_page_count(self, doc: Document, page_count: int) -> Document:
        """更新 Document 的页数（parse_pdf 后调用），flush（不 commit）。"""

        doc.page_count = page_count
        self.session.flush()
        return doc

    # ------------------------------------------------------------------
    # Chunk 持久化
    # ------------------------------------------------------------------

    def add_chunks(self, doc: Document, chunks: Sequence[Chunk]) -> None:
        """把一批 Chunk 关联到 Document 并持久化（flush，不 commit）。

        Args:
            doc: 所属 Document（已持久化）。
            chunks: ORM Chunk 实例列表（service 层负责从 chunker 的 dataclass
                转换而来，含 document_id / start_page / end_page / chunk_index
                / content / char_count）。
        """

        # 用 relationship 的 extend：SQLAlchemy 会自动设置 chunk.document_id
        # 并在 flush 时 INSERT。比 session.add_all 更直观（明确归属关系）。
        doc.chunks.extend(chunks)
        self.session.flush()
