"""DocumentRepository 单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.1 节、阶段 5 验收）：
- create：创建 Document 并 flush，id 自动生成
- get_by_id / get_by_sha256：查询（命中 + 未命中）
- list_all：列表 + 排序（创建时间降序）
- delete：删除 + 级联删除 chunks
- update_status：状态 + error_message 联动更新
- update_page_count：页数更新
- add_chunks：批量持久化 chunks

测试用内存 SQLite（``sqlite:///:memory:``），不依赖外部数据库。
Repository 只 flush 不 commit，测试用独立 session 验证 flush 后数据可见。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from research_rag.db.models import (
    Base,
    Chunk,
    DocumentStatus,
)
from research_rag.db.repositories import DocumentRepository

# ---------------------------------------------------------------------------
# Fixtures：内存 SQLite + 建表
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
def repo(session: Session) -> DocumentRepository:
    """创建 DocumentRepository 实例。"""

    return DocumentRepository(session)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_chunk(
    document_id: uuid.UUID,
    *,
    page_number: int = 1,
    chunk_index: int = 0,
    content: str = "测试片段",
) -> Chunk:
    """构造一个 ORM Chunk 实例（未持久化）。"""

    return Chunk(
        document_id=document_id,
        page_number=page_number,
        chunk_index=chunk_index,
        content=content,
        char_count=len(content),
    )


# ---------------------------------------------------------------------------
# create + get_by_id
# ---------------------------------------------------------------------------


def test_create_and_get_by_id(repo: DocumentRepository, session: Session) -> None:
    """create 创建记录并 flush，get_by_id 能查到。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc123.pdf",
        sha256="a" * 64,
    )

    assert isinstance(doc.id, uuid.UUID)
    assert doc.status == DocumentStatus.PENDING
    assert doc.page_count == 0
    assert doc.error_message is None

    # flush 后同一 session 能查到（未 commit，但同 session 可见）
    found = repo.get_by_id(doc.id)
    assert found is not None
    assert found.original_name == "paper.pdf"
    assert found.sha256 == "a" * 64


def test_get_by_id_not_found(repo: DocumentRepository) -> None:
    """get_by_id 查不到返回 None。"""

    random_id = uuid.uuid4()
    assert repo.get_by_id(random_id) is None


# ---------------------------------------------------------------------------
# get_by_sha256
# ---------------------------------------------------------------------------


def test_get_by_sha256_found(repo: DocumentRepository) -> None:
    """get_by_sha256 命中。"""

    repo.create(
        original_name="paper.pdf",
        stored_name="abc123.pdf",
        sha256="b" * 64,
    )

    found = repo.get_by_sha256("b" * 64)
    assert found is not None
    assert found.original_name == "paper.pdf"


def test_get_by_sha256_not_found(repo: DocumentRepository) -> None:
    """get_by_sha256 未命中返回 None。"""

    assert repo.get_by_sha256("c" * 64) is None


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_empty(repo: DocumentRepository) -> None:
    """空表 list_all 返回空列表。"""

    assert repo.list_all() == []


def test_list_all_ordered_by_created_at_desc(repo: DocumentRepository, session: Session) -> None:
    """list_all 按创建时间降序（最新的在前）。"""

    doc1 = repo.create(
        original_name="first.pdf",
        stored_name="first.pdf",
        sha256="1" * 64,
    )
    session.commit()
    # 手动调整 created_at 确保顺序明确（避免同一秒创建导致顺序不定）
    doc1.created_at = datetime(2026, 1, 1, 10, 0, 0)

    doc2 = repo.create(
        original_name="second.pdf",
        stored_name="second.pdf",
        sha256="2" * 64,
    )
    doc2.created_at = datetime(2026, 1, 2, 10, 0, 0)
    session.commit()

    docs = repo.list_all()
    assert len(docs) == 2
    assert docs[0].original_name == "second.pdf"  # 新的在前
    assert docs[1].original_name == "first.pdf"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_document(repo: DocumentRepository, session: Session) -> None:
    """delete 删除记录，flush 后查不到。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="d" * 64,
    )

    repo.delete(doc)
    assert repo.get_by_id(doc.id) is None


def test_delete_cascades_chunks(repo: DocumentRepository, session: Session) -> None:
    """delete Document 时级联删除其 chunks（ORM cascade）。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="e" * 64,
    )
    repo.add_chunks(
        doc,
        [
            _make_chunk(doc.id, chunk_index=0, content="片段 0"),
            _make_chunk(doc.id, chunk_index=1, content="片段 1", page_number=2),
        ],
    )
    session.commit()

    # 确认 chunks 已持久化
    chunks_before = session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
    assert len(chunks_before) == 2

    repo.delete(doc)
    session.commit()

    # chunks 也被级联删除
    chunks_after = session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
    assert chunks_after == []


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------


def test_update_status_to_processing(repo: DocumentRepository) -> None:
    """update_status 切到 PROCESSING，error_message 清空。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="f" * 64,
    )

    repo.update_status(doc, DocumentStatus.PROCESSING, None)
    assert doc.status == DocumentStatus.PROCESSING
    assert doc.error_message is None


def test_update_status_to_failed_with_error(repo: DocumentRepository) -> None:
    """update_status 切到 FAILED，error_message 记录原因。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="1" * 64,
    )

    repo.update_status(doc, DocumentStatus.FAILED, "PDF 解析失败：文件损坏")
    assert doc.status == DocumentStatus.FAILED
    assert doc.error_message == "PDF 解析失败：文件损坏"


def test_update_status_to_ready_clears_error(repo: DocumentRepository) -> None:
    """update_status 切到 READY 时 error_message 被清空。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="2" * 64,
    )
    repo.update_status(doc, DocumentStatus.FAILED, "临时错误")
    assert doc.error_message == "临时错误"

    repo.update_status(doc, DocumentStatus.READY, None)
    assert doc.status == DocumentStatus.READY
    assert doc.error_message is None


# ---------------------------------------------------------------------------
# update_page_count
# ---------------------------------------------------------------------------


def test_update_page_count(repo: DocumentRepository) -> None:
    """update_page_count 更新页数。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="3" * 64,
    )
    assert doc.page_count == 0

    repo.update_page_count(doc, 12)
    assert doc.page_count == 12


# ---------------------------------------------------------------------------
# add_chunks
# ---------------------------------------------------------------------------


def test_add_chunks(repo: DocumentRepository, session: Session) -> None:
    """add_chunks 批量持久化 chunks，关联到 document。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="4" * 64,
    )

    chunks = [
        _make_chunk(doc.id, chunk_index=0, content="第一段"),
        _make_chunk(doc.id, chunk_index=1, content="第二段", page_number=2),
        _make_chunk(doc.id, chunk_index=2, content="第三段", page_number=2),
    ]
    repo.add_chunks(doc, chunks)
    session.commit()

    # 通过 relationship 访问
    session.refresh(doc)
    assert len(doc.chunks) == 3
    contents = sorted(c.content for c in doc.chunks)
    assert contents == ["第一段", "第三段", "第二段"]

    # 通过直接查询访问
    db_chunks = session.scalars(
        select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index)
    ).all()
    assert len(db_chunks) == 3
    assert db_chunks[0].chunk_index == 0
    assert db_chunks[2].chunk_index == 2
    assert all(c.vector_id is None for c in db_chunks)


def test_add_chunks_empty_list(repo: DocumentRepository, session: Session) -> None:
    """add_chunks 空列表不报错，document.chunks 仍为空。"""

    doc = repo.create(
        original_name="paper.pdf",
        stored_name="abc.pdf",
        sha256="5" * 64,
    )
    repo.add_chunks(doc, [])
    session.commit()

    session.refresh(doc)
    assert len(doc.chunks) == 0
