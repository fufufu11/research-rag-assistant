"""DocumentService 单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.1 节、阶段 5 验收）：
- upload_document 成功：status=READY、page_count 正确、chunks 持久化、文件落盘
- upload_document 重复：抛 DuplicateDocumentError，不创建记录、不落盘
- upload_document 失败（parse_pdf 抛异常）：status=FAILED、error_message 有原因
- upload_document 空 chunks：status=READY、0 chunks
- get_document：命中 + 不命中（抛 DocumentNotFoundError）
- list_documents：空 + 多条排序
- delete_document：DB 记录 + chunks 级联 + 磁盘文件
- delete_document 不存在：抛 DocumentNotFoundError
- delete_document 文件已缺失：不报错（missing_ok=True）

测试策略：
- 用 ``monkeypatch`` 替换 ``document_service.parse_pdf`` / ``chunk_pages``，
  避免真实 PDF 解析（PROJECT_PLAN 第 13.2 节"测试中应 Mock 模型 API 和
  Embedding 服务"）。
- 用内存 SQLite（``sqlite:///:memory:``）隔离数据库。
- 用 ``tmp_path`` 隔离文件 IO，不写真实磁盘。
- sha256 用 ``hashlib`` 算真实值，验证 service 计算正确。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from research_rag.chunker import Chunk as ChunkerChunk
from research_rag.db.models import (
    Base,
    Chunk,
    DocumentNotFoundError,
    DocumentStatus,
    DuplicateDocumentError,
)
from research_rag.pdf_parser import EmptyPdfError, InvalidPdfError, PageInfo, PdfParseResult
from research_rag.services.document_service import (
    DEFAULT_UPLOAD_DIR,
    SHA256_PREFIX_LENGTH,
    DocumentService,
    _get_default_upload_dir,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """创建内存 SQLite engine 并建表。"""

    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """基于 ``engine`` 创建 Session，测试结束关闭。"""

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess: Session = factory()
    yield sess
    sess.close()


@pytest.fixture
def upload_dir(tmp_path) -> Path:
    """用 tmp_path 作为上传目录，隔离文件 IO。"""

    return tmp_path / "uploads"


@pytest.fixture
def service(session: Session, upload_dir: Path) -> DocumentService:
    """创建 DocumentService 实例，用 tmp_path 隔离文件 IO。"""

    return DocumentService(session, upload_dir)


@pytest.fixture
def mock_parser(monkeypatch: pytest.MonkeyPatch):
    """用 monkeypatch 替换 parse_pdf 和 chunk_pages。

    返回 ``(parse_mock, chunk_mock)`` 元组，测试中设置 return_value 或 side_effect。
    默认返回值：2 页 PDF，2 个 chunks。
    """

    parse_mock = MagicMock()
    chunk_mock = MagicMock()

    # 默认返回值：2 页 PDF + 2 个 chunks
    parse_mock.return_value = _make_parse_result(page_count=2)
    chunk_mock.return_value = _make_chunker_chunks(count=2)

    monkeypatch.setattr("research_rag.services.document_service.parse_pdf", parse_mock)
    monkeypatch.setattr("research_rag.services.document_service.chunk_pages", chunk_mock)
    return parse_mock, chunk_mock


# ---------------------------------------------------------------------------
# 辅助：构造 mock 返回值
# ---------------------------------------------------------------------------


def _make_parse_result(page_count: int = 2) -> PdfParseResult:
    """构造一个 mock 的 PdfParseResult。

    每页 text 填充足够长的英文文本，确保 chunker 的 min_chunk_chars 过滤不掉
    （实际 chunker 被 mock，text 内容不影响测试，但保持真实结构）。
    """

    pages = [
        PageInfo(
            page_number=i + 1,
            char_count=100,
            text=f"Page {i + 1} content. " * 10,
            preview=f"Page {i + 1} preview",
        )
        for i in range(page_count)
    ]
    return PdfParseResult(pages=pages, page_count=page_count)


def _make_chunker_chunks(count: int = 2) -> list[ChunkerChunk]:
    """构造 mock 的 chunker.Chunk 列表。"""

    return [
        ChunkerChunk(
            start_page=(i // 2) + 1,
            end_page=(i // 2) + 1,
            chunk_index=i,
            content=f"Chunk {i} content text for testing purposes.",
            char_count=40,
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# upload_document 成功
# ---------------------------------------------------------------------------


def test_upload_success_status_ready(
    service: DocumentService, mock_parser, session: Session
) -> None:
    """成功上传：status=READY，page_count 来自 parse_pdf。"""

    parse_mock, chunk_mock = mock_parser
    parse_mock.return_value = _make_parse_result(page_count=3)
    chunk_mock.return_value = _make_chunker_chunks(count=4)

    file_bytes = b"fake pdf content for testing"
    doc = service.upload_document(file_bytes, "paper.pdf")

    assert doc.status == DocumentStatus.READY
    assert doc.page_count == 3
    assert doc.error_message is None
    assert doc.original_name == "paper.pdf"


def test_upload_success_sha256_correct(service: DocumentService, mock_parser) -> None:
    """sha256 由 service 用 hashlib 计算，与预期一致。"""

    file_bytes = b"fake pdf content"
    expected_sha = hashlib.sha256(file_bytes).hexdigest()

    doc = service.upload_document(file_bytes, "paper.pdf")

    assert doc.sha256 == expected_sha


def test_upload_success_stored_name_safe(
    service: DocumentService, mock_parser, upload_dir: Path
) -> None:
    """stored_name = sha256[:16] + 小写扩展名，文件落盘到 upload_dir。"""

    file_bytes = b"fake pdf content"
    expected_sha = hashlib.sha256(file_bytes).hexdigest()
    expected_stored = f"{expected_sha[:SHA256_PREFIX_LENGTH]}.pdf"

    doc = service.upload_document(file_bytes, "Paper.PDF")

    assert doc.stored_name == expected_stored
    # 文件确实落盘
    assert (upload_dir / expected_stored).exists()
    assert (upload_dir / expected_stored).read_bytes() == file_bytes


def test_upload_success_stored_name_no_extension(
    service: DocumentService, mock_parser, upload_dir: Path
) -> None:
    """无扩展名的文件名：stored_name = sha256[:16]（无扩展名）。"""

    file_bytes = b"fake pdf content"
    expected_sha = hashlib.sha256(file_bytes).hexdigest()
    expected_stored = expected_sha[:SHA256_PREFIX_LENGTH]

    doc = service.upload_document(file_bytes, "paper")

    assert doc.stored_name == expected_stored
    assert (upload_dir / expected_stored).exists()


def test_upload_success_chunks_persisted(
    service: DocumentService, mock_parser, session: Session
) -> None:
    """成功上传后 chunks 持久化到 DB，vector_id 为 None。"""

    _, chunk_mock = mock_parser
    chunk_mock.return_value = _make_chunker_chunks(count=3)

    doc = service.upload_document(b"fake pdf", "paper.pdf")

    # 通过 DB 直接查询 chunks
    db_chunks = session.scalars(
        select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index)
    ).all()
    assert len(db_chunks) == 3
    assert all(c.vector_id is None for c in db_chunks)
    # chunk_index 从 0 开始连续
    assert [c.chunk_index for c in db_chunks] == [0, 1, 2]


def test_upload_success_creates_upload_dir(
    service: DocumentService, mock_parser, upload_dir: Path
) -> None:
    """upload_dir 不存在时自动创建。"""

    assert not upload_dir.exists()

    service.upload_document(b"fake pdf", "paper.pdf")

    assert upload_dir.exists()


def test_upload_success_parse_pdf_called_with_path(
    service: DocumentService, mock_parser, upload_dir: Path
) -> None:
    """parse_pdf 被调用时传入落盘后的文件路径。"""

    parse_mock, _ = mock_parser
    file_bytes = b"fake pdf content"
    expected_sha = hashlib.sha256(file_bytes).hexdigest()
    expected_path = upload_dir / f"{expected_sha[:SHA256_PREFIX_LENGTH]}.pdf"

    service.upload_document(file_bytes, "paper.pdf")

    parse_mock.assert_called_once_with(expected_path)


# ---------------------------------------------------------------------------
# upload_document 重复
# ---------------------------------------------------------------------------


def test_upload_duplicate_raises(service: DocumentService, mock_parser, session: Session) -> None:
    """重复上传（相同 sha256）抛 DuplicateDocumentError。"""

    file_bytes = b"same content"
    # 第一次上传成功
    doc1 = service.upload_document(file_bytes, "paper.pdf")
    assert doc1.status == DocumentStatus.READY

    # 第二次上传相同内容
    with pytest.raises(DuplicateDocumentError) as exc_info:
        service.upload_document(file_bytes, "different_name.pdf")

    # 异常信息包含原文件名
    assert "paper.pdf" in str(exc_info.value)


def test_upload_duplicate_no_file_written(
    service: DocumentService, mock_parser, upload_dir: Path
) -> None:
    """重复上传时不写第二个文件（去重在落盘之前）。"""

    file_bytes = b"same content"
    service.upload_document(file_bytes, "paper.pdf")

    files_before = list(upload_dir.iterdir())
    assert len(files_before) == 1

    with pytest.raises(DuplicateDocumentError):
        service.upload_document(file_bytes, "different.pdf")

    files_after = list(upload_dir.iterdir())
    assert len(files_after) == 1  # 没有新文件


def test_upload_duplicate_no_second_record(
    service: DocumentService, mock_parser, session: Session
) -> None:
    """重复上传不创建第二条 DB 记录。"""

    file_bytes = b"same content"
    service.upload_document(file_bytes, "paper.pdf")

    with pytest.raises(DuplicateDocumentError):
        service.upload_document(file_bytes, "different.pdf")

    docs = service.list_documents()
    assert len(docs) == 1


# ---------------------------------------------------------------------------
# upload_document 处理失败
# ---------------------------------------------------------------------------


def test_upload_invalid_pdf_marks_failed(
    service: DocumentService, mock_parser, session: Session
) -> None:
    """parse_pdf 抛 InvalidPdfError：status=FAILED，error_message 有原因。"""

    parse_mock, _ = mock_parser
    parse_mock.side_effect = InvalidPdfError("文件损坏")

    doc = service.upload_document(b"corrupted pdf bytes", "bad.pdf")

    assert doc.status == DocumentStatus.FAILED
    assert doc.error_message is not None
    assert "InvalidPdfError" in doc.error_message
    assert "文件损坏" in doc.error_message


def test_upload_empty_pdf_marks_failed(
    service: DocumentService, mock_parser, session: Session
) -> None:
    """parse_pdf 抛 EmptyPdfError：status=FAILED。"""

    parse_mock, _ = mock_parser
    parse_mock.side_effect = EmptyPdfError("PDF 没有页面")

    doc = service.upload_document(b"empty pdf", "empty.pdf")

    assert doc.status == DocumentStatus.FAILED
    assert doc.error_message is not None
    assert "EmptyPdfError" in doc.error_message


def test_upload_failure_persists_record_and_file(
    service: DocumentService, mock_parser, session: Session, upload_dir: Path
) -> None:
    """处理失败时：PENDING 记录已持久化（转 FAILED），文件已落盘。"""

    parse_mock, _ = mock_parser
    parse_mock.side_effect = InvalidPdfError("损坏")

    file_bytes = b"corrupted pdf"
    doc = service.upload_document(file_bytes, "bad.pdf")

    # DB 记录存在且状态为 FAILED
    assert doc.status == DocumentStatus.FAILED
    found = service.get_document(doc.id)
    assert found.status == DocumentStatus.FAILED

    # 文件已落盘（落盘在 parse_pdf 之前）
    assert (upload_dir / doc.stored_name).exists()


def test_upload_failure_no_chunks(service: DocumentService, mock_parser, session: Session) -> None:
    """处理失败时不持久化 chunks（parse_pdf 失败，chunk_pages 未调用）。"""

    parse_mock, chunk_mock = mock_parser
    parse_mock.side_effect = InvalidPdfError("损坏")

    doc = service.upload_document(b"bad pdf", "bad.pdf")

    db_chunks = session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
    assert db_chunks == []
    # chunk_pages 未被调用（parse_pdf 先失败）
    chunk_mock.assert_not_called()


def test_upload_zero_chunks_status_ready(
    service: DocumentService, mock_parser, session: Session
) -> None:
    """chunker 返回空列表（PDF 有页但无文本）：status=READY，0 chunks。"""

    parse_mock, chunk_mock = mock_parser
    parse_mock.return_value = _make_parse_result(page_count=1)
    chunk_mock.return_value = []  # 空 chunks

    doc = service.upload_document(b"no text pdf", "scanned.pdf")

    assert doc.status == DocumentStatus.READY
    db_chunks = session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
    assert db_chunks == []


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


def test_get_document_found(service: DocumentService, mock_parser) -> None:
    """get_document 命中返回 Document。"""

    uploaded = service.upload_document(b"pdf content", "paper.pdf")
    found = service.get_document(uploaded.id)
    assert found.id == uploaded.id
    assert found.original_name == "paper.pdf"


def test_get_document_not_found(service: DocumentService) -> None:
    """get_document 不存在抛 DocumentNotFoundError。"""

    random_id = uuid.uuid4()
    with pytest.raises(DocumentNotFoundError) as exc_info:
        service.get_document(random_id)
    assert str(random_id) in str(exc_info.value)


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------


def test_list_documents_empty(service: DocumentService) -> None:
    """空库 list_documents 返回空列表。"""

    assert service.list_documents() == []


def test_list_documents_ordered(service: DocumentService, mock_parser, session: Session) -> None:
    """list_documents 按创建时间降序（最新的在前）。"""

    doc1 = service.upload_document(b"content A", "first.pdf")
    # 手动调整 created_at 确保顺序明确
    doc1.created_at = datetime(2026, 1, 1, 10, 0, 0)
    session.commit()

    doc2 = service.upload_document(b"content B", "second.pdf")
    doc2.created_at = datetime(2026, 1, 2, 10, 0, 0)
    session.commit()

    docs = service.list_documents()
    assert len(docs) == 2
    assert docs[0].original_name == "second.pdf"
    assert docs[1].original_name == "first.pdf"


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------


def test_delete_document_success(
    service: DocumentService, mock_parser, session: Session, upload_dir: Path
) -> None:
    """删除文档：DB 记录 + chunks + 磁盘文件全部清除。"""

    _, chunk_mock = mock_parser
    chunk_mock.return_value = _make_chunker_chunks(count=2)

    doc = service.upload_document(b"pdf content", "paper.pdf")
    doc_id = doc.id
    stored_name = doc.stored_name

    # 确认存在
    assert service.get_document(doc_id) is not None
    assert (upload_dir / stored_name).exists()
    chunks_before = session.scalars(select(Chunk).where(Chunk.document_id == doc_id)).all()
    assert len(chunks_before) == 2

    # 删除
    service.delete_document(doc_id)

    # DB 记录没了
    with pytest.raises(DocumentNotFoundError):
        service.get_document(doc_id)

    # chunks 级联删除
    chunks_after = session.scalars(select(Chunk).where(Chunk.document_id == doc_id)).all()
    assert chunks_after == []

    # 文件没了
    assert not (upload_dir / stored_name).exists()


def test_delete_document_not_found(service: DocumentService) -> None:
    """删除不存在的文档抛 DocumentNotFoundError。"""

    random_id = uuid.uuid4()
    with pytest.raises(DocumentNotFoundError):
        service.delete_document(random_id)


def test_delete_document_file_already_missing(
    service: DocumentService, mock_parser, upload_dir: Path
) -> None:
    """文件已被手动删除时，delete_document 不报错（missing_ok=True）。"""

    doc = service.upload_document(b"pdf content", "paper.pdf")
    stored_name = doc.stored_name

    # 手动删除文件
    (upload_dir / stored_name).unlink()
    assert not (upload_dir / stored_name).exists()

    # 删除文档不报错
    service.delete_document(doc.id)

    with pytest.raises(DocumentNotFoundError):
        service.get_document(doc.id)


# ---------------------------------------------------------------------------
# 辅助函数测试
# ---------------------------------------------------------------------------


def test_get_default_upload_dir_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_default_upload_dir 从 UPLOAD_DIR 环境变量读取。"""

    monkeypatch.setenv("UPLOAD_DIR", "/custom/uploads")
    assert _get_default_upload_dir() == Path("/custom/uploads")


def test_get_default_upload_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置 UPLOAD_DIR 时返回默认值。"""

    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    assert _get_default_upload_dir() == DEFAULT_UPLOAD_DIR


def test_make_stored_name_lowercase_extension(service: DocumentService) -> None:
    """扩展名小写化：.PDF → .pdf。"""

    stored = service._make_stored_name("a" * 64, "paper.PDF")
    assert stored == "a" * SHA256_PREFIX_LENGTH + ".pdf"


def test_make_stored_name_no_extension(service: DocumentService) -> None:
    """无扩展名：stored_name 只有 sha256 前缀。"""

    stored = service._make_stored_name("b" * 64, "paper")
    assert stored == "b" * SHA256_PREFIX_LENGTH


def test_make_stored_name_double_extension(service: DocumentService) -> None:
    """双扩展名只取最后一个：.tar.gz → .gz。"""

    stored = service._make_stored_name("c" * 64, "archive.tar.gz")
    assert stored == "c" * SHA256_PREFIX_LENGTH + ".gz"
