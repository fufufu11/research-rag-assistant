"""文档存储与状态管理服务层。

依据 PROJECT_PLAN.md 第 6.1 节（文档处理流程）、US-001 / US-002、
第 11 节（安全：文件路径限制在上传目录内）、第 13.6 节（异常清单）。

设计取舍（初学者向说明）：
- **Service 编排业务流程**：``DocumentService`` 是文档管理的入口，编排
  repository（数据访问）、``parse_pdf`` / ``chunk_pages``（文档处理）、
  文件 IO（落盘）三类操作。不直接写 SQL，不实现 HTTP 路由。
- **sha256 去重**：上传前先查 ``sha256`` 是否已存在，存在则抛
  ``DuplicateDocumentError``（US-001"同一文件重复上传时不得产生重复数据"）。
  去重在创建记录之前，避免无效落盘和无效 DB 写入。
- **stored_name 安全**：用 ``sha256[:16] + 小写扩展名``，不直接用
  ``original_name``（避免路径遍历攻击，PROJECT_PLAN 第 11 节）。sha256 的
  hex 字符（0-9, a-f）和 ``Path.suffix`` 提取的扩展名都不含路径分隔符，
  从源头杜绝路径遍历。
- **状态机**：``pending → processing → ready``（成功）或 ``failed``（失败）。
  每个 状态转换都 ``commit``，即使进程崩溃，最后提交的状态也可见。失败时
  ``error_message`` 记录异常信息，方便排查（US-001"处理失败时记录失败状态
  和可理解的错误信息"）。
- **事务边界在 service**：repository 只 ``flush`` 不 ``commit``，service
  决定何时提交。失败时先 ``rollback`` 清除 pending 改动，再重新查询记录
  并标记 FAILED，避免 session 处于不可用状态。
- **删除顺序**：先删 DB 记录（事务原子，级联删 chunks），再删文件
  （best-effort，失败不阻断）。理由：DB 事务是一致性保障，文件删除失败
  只留孤儿文件（可后续清理），不会出现"DB 记录指向不存在文件"的不一致。
- **不实现向量库写入**：``vector_id`` 保留 ``None``（阶段 6 接 Qdrant）。
- **Mock 友好**：``parse_pdf`` / ``chunk_pages`` 在模块顶部直接导入，
  测试用 ``unittest.mock.patch`` 替换 ``document_service.parse_pdf``
  即可，不侵入业务接口。
"""

from __future__ import annotations

import hashlib
import os
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from research_rag.chunker import chunk_pages
from research_rag.db.models import (
    Chunk,
    Document,
    DocumentNotFoundError,
    DocumentStatus,
    DuplicateDocumentError,
)
from research_rag.db.repositories import DocumentRepository
from research_rag.pdf_parser import parse_pdf

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from research_rag.chunker import Chunk as ChunkerChunk

# sha256 前 16 字符用于 stored_name。
# 16 个 hex 字符 = 64 位熵，碰撞概率极低（同目录下 2^32 个文件才可能碰撞），
# 且文件名不过长，便于日志和排查。
SHA256_PREFIX_LENGTH = 16

# 默认上传目录（PROJECT_PLAN.md 第 11 节、.env.example）。
# 相对当前工作目录的 data/uploads（.gitignore 已忽略 data/）。
DEFAULT_UPLOAD_DIR = Path("./data/uploads")


def _get_default_upload_dir() -> Path:
    """从环境变量读取 ``UPLOAD_DIR``，未设置时返回默认值。

    与 ``db.session.get_database_url()`` 风格一致：默认值与 ``.env.example``
    一致，确保不配置 ``.env`` 也能本地运行。
    """

    return Path(os.environ.get("UPLOAD_DIR", str(DEFAULT_UPLOAD_DIR)))


class DocumentService:
    """文档管理服务：上传、查询、列表、删除。

    编排 repository（数据访问）、``parse_pdf`` / ``chunk_pages``（文档处理）、
    文件 IO（落盘）。事务边界由本类控制（repository 不 commit）。

    Attributes:
        session: SQLAlchemy Session，由调用方传入。
        upload_dir: 上传文件保存目录，默认从 ``UPLOAD_DIR`` 环境变量读取。
        repo: ``DocumentRepository`` 实例，封装 DB CRUD。
    """

    def __init__(
        self,
        session: Session,
        upload_dir: Path | None = None,
    ) -> None:
        self.session = session
        self.upload_dir = upload_dir or _get_default_upload_dir()
        self.repo = DocumentRepository(session)

    # ------------------------------------------------------------------
    # 上传文档（核心业务流程）
    # ------------------------------------------------------------------

    def upload_document(self, file_bytes: bytes, original_name: str) -> Document:
        """上传文档：sha256 去重 → 落盘 → 解析 → 切分 → 状态机流转。

        完整流程（PROJECT_PLAN.md 第 6.1 节）：
        1. 计算 sha256，查重（重复抛 ``DuplicateDocumentError``）
        2. 创建 ``PENDING`` 记录并 commit（即使后续崩溃也有记录）
        3. 落盘文件到 ``upload_dir / stored_name``
        4. 状态转 ``PROCESSING`` 并 commit
        5. 调用 ``parse_pdf`` 获取页数，更新 ``page_count``
        6. 调用 ``chunk_pages`` 切分，持久化 chunks（``vector_id=None``）
        7. 状态转 ``READY`` 并 commit
        8. 任何步骤失败：rollback → 标记 ``FAILED`` + ``error_message`` → commit

        Args:
            file_bytes: 文件内容（PDF 二进制）。
            original_name: 用户上传时的文件名（展示用，不作为磁盘路径）。

        Returns:
            ``Document`` 实例。成功时 ``status=READY``，失败时
            ``status=FAILED`` 且 ``error_message`` 有具体原因。

        Raises:
            DuplicateDocumentError: sha256 已存在（重复上传）。
        """

        # 1. 计算 sha256（文件内容哈希，用于去重）
        sha256 = hashlib.sha256(file_bytes).hexdigest()

        # 2. 查重：已存在则直接抛异常，不创建记录、不落盘
        existing = self.repo.get_by_sha256(sha256)
        if existing is not None:
            raise DuplicateDocumentError(
                f"文档已存在：{existing.original_name}（sha256={sha256[:8]}...）"
            )

        # 3. 生成安全文件名 + 创建 PENDING 记录
        stored_name = self._make_stored_name(sha256, original_name)
        doc = self.repo.create(
            original_name=original_name,
            stored_name=stored_name,
            sha256=sha256,
        )
        # commit 让 PENDING 记录持久化：即使后续处理崩溃，也能看到这条记录
        self.session.commit()

        # 4. 处理流程（失败时标记 FAILED）
        try:
            # 4a. 落盘文件（确保目录存在）
            self.upload_dir.mkdir(parents=True, exist_ok=True)
            upload_path = self.upload_dir / stored_name
            upload_path.write_bytes(file_bytes)

            # 4b. 状态转 PROCESSING
            self.repo.update_status(doc, DocumentStatus.PROCESSING, None)
            self.session.commit()

            # 4c. 解析 PDF 获取页数
            parse_result = parse_pdf(upload_path)
            self.repo.update_page_count(doc, parse_result.page_count)

            # 4d. 切分并持久化 chunks（vector_id 保留 None，阶段 6 才写向量库）
            chunker_chunks = chunk_pages(parse_result.pages)
            db_chunks = self._convert_chunks(chunker_chunks, doc.id)
            self.repo.add_chunks(doc, db_chunks)

            # 4e. 状态转 READY
            self.repo.update_status(doc, DocumentStatus.READY, None)
            self.session.commit()
            return doc
        except Exception as exc:
            # 处理失败：rollback 清除 pending 改动 → 重新查询 → 标记 FAILED
            # rollback 后 doc 对象属性可能过期，用 get_by_id 重新获取
            self.session.rollback()
            refreshed = self.repo.get_by_id(doc.id)
            if refreshed is None:
                # 极端情况：PENDING 记录在 commit 后被其他事务删除
                raise
            self.repo.update_status(
                refreshed,
                DocumentStatus.FAILED,
                f"{type(exc).__name__}: {exc}",
            )
            self.session.commit()
            return refreshed

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get_document(self, doc_id: uuid.UUID) -> Document:
        """按 ID 查询文档，不存在抛 ``DocumentNotFoundError``。"""

        doc = self.repo.get_by_id(doc_id)
        if doc is None:
            raise DocumentNotFoundError(f"文档不存在：{doc_id}")
        return doc

    def list_documents(self) -> list[Document]:
        """返回所有文档，按创建时间降序（最新的在前）。"""

        return self.repo.list_all()

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------

    def delete_document(self, doc_id: uuid.UUID) -> None:
        """删除文档：DB 记录（级联删 chunks）+ 磁盘文件。

        删除顺序（设计取舍见模块 docstring）：
        1. 查询文档（不存在抛 ``DocumentNotFoundError``）
        2. 保存 ``stored_name``（删除后对象属性不可访问）
        3. 删 DB 记录（事务，级联删 chunks）+ commit
        4. 删磁盘文件（best-effort，失败不阻断）

        Args:
            doc_id: 文档 ID。

        Raises:
            DocumentNotFoundError: 文档不存在。
        """

        doc = self.repo.get_by_id(doc_id)
        if doc is None:
            raise DocumentNotFoundError(f"文档不存在：{doc_id}")

        # 先保存 stored_name，因为删除后 doc 对象属性可能过期
        stored_name = doc.stored_name

        # 删 DB 记录（ORM cascade 会自动删除关联的 chunks）
        self.repo.delete(doc)
        self.session.commit()

        # 删磁盘文件（best-effort：失败不阻断，DB 已干净）
        # missing_ok=True：文件不存在时不报错（可能已被手动删除）
        # suppress(OSError)：文件删除失败（权限、被占用等）不抛异常，
        # DB 已干净，孤儿文件可后续清理。
        file_path = self.upload_dir / stored_name
        with suppress(OSError):
            file_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 私有辅助
    # ------------------------------------------------------------------

    def _make_stored_name(self, sha256: str, original_name: str) -> str:
        """生成安全的存储文件名：``sha256[:16] + 小写扩展名``。

        安全性：sha256 的 hex 字符（0-9, a-f）和 ``Path.suffix`` 提取的扩展名
        都不含路径分隔符（``/`` / ``\\``），从源头杜绝路径遍历攻击。

        示例：
        - ``paper.pdf`` + sha256=``abc123...`` → ``abc123def456abcd.pdf``
        - ``paper.PDF`` → ``abc123def456abcd.pdf``（扩展名小写化）
        - ``paper``（无扩展名）→ ``abc123def456abcd``
        """

        extension = Path(original_name).suffix.lower()
        return f"{sha256[:SHA256_PREFIX_LENGTH]}{extension}"

    def _convert_chunks(
        self,
        chunker_chunks: Sequence[ChunkerChunk],
        document_id: uuid.UUID,
    ) -> list[Chunk]:
        """把 chunker 的 dataclass ``Chunk`` 列表转成 ORM ``Chunk`` 列表。

        chunker 的 ``Chunk`` 是不可变 dataclass（``page_number`` / ``chunk_index``
        / ``content`` / ``char_count``），不含 ``document_id`` 和 ``vector_id``。
        ORM ``Chunk`` 需要关联到文档，``vector_id`` 保留 ``None``（阶段 6 才写
        向量库）。
        """

        return [
            Chunk(
                document_id=document_id,
                page_number=c.page_number,
                chunk_index=c.chunk_index,
                content=c.content,
                char_count=c.char_count,
            )
            for c in chunker_chunks
        ]
