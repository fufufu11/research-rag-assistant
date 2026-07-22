"""db.models 与 db.session 单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.1 节、阶段 5 验收）：
- DocumentStatus 枚举值
- Document / Chunk 字段默认值（status、page_count、created_at、updated_at）
- CRUD 基础操作（插入、查询、更新、删除）
- 级联删除（删除 Document 后 Chunk 也被删除）
- sha256 唯一约束、(document_id, chunk_index) 唯一约束
- relationship 双向访问
- DuplicateDocumentError / DocumentNotFoundError 可实例化
- get_database_url 从环境变量读取，有默认值
- create_session_factory 返回可用 sessionmaker

测试用内存 SQLite（``sqlite:///:memory:``），不依赖外部数据库，
CI 无需额外服务。SQLAlchemy 对 ``:memory:`` 默认用 ``StaticPool``，
确保同一 engine 内所有连接共享同一个内存数据库。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from research_rag.db.models import (
    Base,
    Chunk,
    Document,
    DocumentNotFoundError,
    DocumentStatus,
    DuplicateDocumentError,
)
from research_rag.db.session import (
    DEFAULT_DATABASE_URL,
    create_engine_for_url,
    create_session_factory,
    get_database_url,
)

# ---------------------------------------------------------------------------
# Fixtures：内存 SQLite + 建表
# ---------------------------------------------------------------------------


@pytest.fixture
def engine():
    """创建内存 SQLite engine 并建表。

    用 ``Base.metadata.create_all`` 直接建表，不通过 Alembic 迁移。
    模型测试只验证 ORM 行为，迁移可执行性在 ``test_alembic_migration.py``。
    """

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


# ---------------------------------------------------------------------------
# 辅助：构造测试数据
# ---------------------------------------------------------------------------


def _make_document(
    *,
    sha256: str = "a" * 64,
    original_name: str = "paper.pdf",
    stored_name: str = "paper-abc123.pdf",
    page_count: int = 12,
    status: DocumentStatus = DocumentStatus.PENDING,
) -> Document:
    """构造一个 Document 实例（未持久化）。"""

    return Document(
        sha256=sha256,
        original_name=original_name,
        stored_name=stored_name,
        page_count=page_count,
        status=status,
    )


def _make_chunk(
    document_id: uuid.UUID,
    *,
    page_number: int = 1,
    chunk_index: int = 0,
    content: str = "这是一段测试文本。",
    char_count: int | None = None,
) -> Chunk:
    """构造一个 Chunk 实例（未持久化）。"""

    return Chunk(
        document_id=document_id,
        page_number=page_number,
        chunk_index=chunk_index,
        content=content,
        char_count=char_count if char_count is not None else len(content),
    )


# ---------------------------------------------------------------------------
# DocumentStatus 枚举
# ---------------------------------------------------------------------------


def test_document_status_values() -> None:
    """DocumentStatus 的字符串值应为小写，与 PROJECT_PLAN 第 7.1 节一致。"""

    assert DocumentStatus.PENDING.value == "pending"
    assert DocumentStatus.PROCESSING.value == "processing"
    assert DocumentStatus.READY.value == "ready"
    assert DocumentStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# Document 默认值
# ---------------------------------------------------------------------------


def test_document_defaults(session: Session) -> None:
    """Document 默认值：status=PENDING、page_count=0、created_at/updated_at 自动填。"""

    before = datetime.utcnow()
    doc = Document(
        original_name="paper.pdf",
        stored_name="paper-abc.pdf",
        sha256="b" * 64,
    )
    session.add(doc)
    session.commit()

    assert doc.id is not None
    assert isinstance(doc.id, uuid.UUID)
    assert doc.status == DocumentStatus.PENDING
    assert doc.page_count == 0
    assert doc.error_message is None
    # created_at / updated_at 由 default 回调填充，应在 [before, now] 区间
    assert doc.created_at >= before
    assert doc.updated_at >= before
    assert doc.created_at <= datetime.utcnow()


def test_document_status_can_transition(session: Session) -> None:
    """Document status 可从 PENDING 更新到 PROCESSING 再到 READY。"""

    doc = _make_document()
    session.add(doc)
    session.commit()

    doc.status = DocumentStatus.PROCESSING
    session.commit()
    updated = session.get(Document, doc.id)
    assert updated is not None
    assert updated.status == DocumentStatus.PROCESSING

    updated.status = DocumentStatus.FAILED
    updated.error_message = "PDF 解析失败：文件损坏"
    session.commit()
    failed = session.get(Document, doc.id)
    assert failed is not None
    assert failed.status == DocumentStatus.FAILED
    assert failed.error_message == "PDF 解析失败：文件损坏"


# ---------------------------------------------------------------------------
# Chunk 默认值
# ---------------------------------------------------------------------------


def test_chunk_defaults(session: Session) -> None:
    """Chunk 默认值：created_at 自动填；id 自动生成。"""

    before = datetime.utcnow()
    doc = _make_document()
    session.add(doc)
    session.commit()

    chunk = _make_chunk(doc.id)
    session.add(chunk)
    session.commit()

    assert isinstance(chunk.id, uuid.UUID)
    assert chunk.created_at >= before
    assert chunk.created_at <= datetime.utcnow()
    assert chunk.vector_id is None  # 本 Issue 不写入向量库


# ---------------------------------------------------------------------------
# CRUD 基础操作
# ---------------------------------------------------------------------------


def test_document_crud(session: Session) -> None:
    """Document 增删改查：插入、按 sha256 查询、按 id 查询、删除。"""

    doc = _make_document(sha256="c" * 64, page_count=5)
    session.add(doc)
    session.commit()

    # 按 sha256 查询（去重场景会用）
    found = session.scalar(select(Document).where(Document.sha256 == "c" * 64))
    assert found is not None
    assert found.page_count == 5

    # 按 id 查询
    by_id = session.get(Document, doc.id)
    assert by_id is not None
    assert by_id.original_name == "paper.pdf"

    # 删除
    session.delete(by_id)
    session.commit()
    assert session.get(Document, doc.id) is None


def test_chunk_crud(session: Session) -> None:
    """Chunk 增删改查：插入、按 document_id 查询、删除。"""

    doc = _make_document()
    session.add(doc)
    session.commit()

    chunk1 = _make_chunk(doc.id, chunk_index=0, content="第一段")
    chunk2 = _make_chunk(doc.id, chunk_index=1, content="第二段", page_number=1)
    session.add_all([chunk1, chunk2])
    session.commit()

    chunks = session.scalars(
        select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.chunk_index)
    ).all()
    assert len(chunks) == 2
    assert chunks[0].content == "第一段"
    assert chunks[1].content == "第二段"

    # 删除其中一个
    session.delete(chunks[0])
    session.commit()
    remaining = session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()
    assert len(remaining) == 1


# ---------------------------------------------------------------------------
# 级联删除
# ---------------------------------------------------------------------------


def test_cascade_delete_document_removes_chunks(session: Session) -> None:
    """删除 Document 时级联删除其所有 Chunk（US-002 要求）。"""

    doc = _make_document()
    session.add(doc)
    session.commit()

    session.add_all(
        [
            _make_chunk(doc.id, chunk_index=0),
            _make_chunk(doc.id, chunk_index=1, page_number=2),
        ]
    )
    session.commit()

    assert len(session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all()) == 2

    session.delete(doc)
    session.commit()

    # Document 没了
    assert session.get(Document, doc.id) is None
    # Chunk 也级联没了
    assert session.scalars(select(Chunk).where(Chunk.document_id == doc.id)).all() == []


# ---------------------------------------------------------------------------
# 唯一约束
# ---------------------------------------------------------------------------


def test_sha256_unique_constraint(session: Session) -> None:
    """sha256 重复插入抛 IntegrityError（US-001 去重依赖此约束）。"""

    doc1 = _make_document(sha256="d" * 64)
    session.add(doc1)
    session.commit()

    doc2 = _make_document(sha256="d" * 64, original_name="other.pdf")
    session.add(doc2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_chunk_unique_doc_chunk_index(session: Session) -> None:
    """(document_id, chunk_index) 重复抛 IntegrityError。"""

    doc = _make_document()
    session.add(doc)
    session.commit()

    chunk1 = _make_chunk(doc.id, chunk_index=0)
    chunk2 = _make_chunk(doc.id, chunk_index=0, content="重复序号")
    session.add_all([chunk1, chunk2])
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# ---------------------------------------------------------------------------
# relationship
# ---------------------------------------------------------------------------


def test_document_chunks_relationship(session: Session) -> None:
    """Document.chunks 能访问到关联的 Chunk 列表。"""

    doc = _make_document()
    session.add(doc)
    session.commit()

    session.add_all(
        [
            _make_chunk(doc.id, chunk_index=0, content="片段 0"),
            _make_chunk(doc.id, chunk_index=1, content="片段 1", page_number=2),
        ]
    )
    session.commit()
    session.refresh(doc)

    assert len(doc.chunks) == 2
    contents = sorted(c.content for c in doc.chunks)
    assert contents == ["片段 0", "片段 1"]


def test_chunk_document_relationship(session: Session) -> None:
    """Chunk.document 能反向访问到所属 Document。"""

    doc = _make_document()
    session.add(doc)
    session.commit()

    chunk = _make_chunk(doc.id)
    session.add(chunk)
    session.commit()
    session.refresh(chunk)

    assert chunk.document is not None
    assert chunk.document.id == doc.id


# ---------------------------------------------------------------------------
# 异常可实例化
# ---------------------------------------------------------------------------


def test_duplicate_document_error_instantiable() -> None:
    """DuplicateDocumentError 可实例化且继承 RuntimeError。"""

    err = DuplicateDocumentError("sha256 already exists")
    assert isinstance(err, RuntimeError)
    assert "sha256 already exists" in str(err)


def test_document_not_found_error_instantiable() -> None:
    """DocumentNotFoundError 可实例化且继承 RuntimeError。"""

    err = DocumentNotFoundError("doc abc not found")
    assert isinstance(err, RuntimeError)
    assert "doc abc not found" in str(err)


# ---------------------------------------------------------------------------
# session 模块
# ---------------------------------------------------------------------------


def test_get_database_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置 DATABASE_URL 时返回默认值。"""

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_database_url() == DEFAULT_DATABASE_URL


def test_get_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """设置 DATABASE_URL 时从环境变量读取。"""

    monkeypatch.setenv("DATABASE_URL", "sqlite:///./custom/path.db")
    assert get_database_url() == "sqlite:///./custom/path.db"


def test_create_session_factory_returns_callable() -> None:
    """create_session_factory 返回的 sessionmaker 可调用并产生 Session。"""

    factory = create_session_factory("sqlite:///:memory:")
    sess = factory()
    try:
        assert isinstance(sess, Session)
    finally:
        sess.close()


def test_create_session_factory_uses_env_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """database_url=None 时用 get_database_url() 读环境变量。"""

    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    factory = create_session_factory()
    sess = factory()
    try:
        # 能执行简单查询即说明连接成功
        sess.execute(select(1))
    finally:
        sess.close()


def test_create_engine_for_url_sqlite_disables_check_same_thread() -> None:
    """SQLite engine 自动添加 check_same_thread=False（FastAPI 线程池场景必需）。

    用行为测试验证：SQLite 默认 ``check_same_thread=True`` 会禁止连接跨线程
    使用，在线程里用 engine 取连接会抛 ``ProgrammingError``。本函数应关闭此
    检查，使跨线程访问不报错（FastAPI 同步路由运行在线程池，必需此行为）。
    """

    engine = create_engine_for_url("sqlite:///:memory:")
    try:
        import threading

        result: list[str] = []

        def use_in_thread() -> None:
            # 若 check_same_thread 仍为 True，此处会抛 ProgrammingError
            with engine.connect() as conn:
                conn.execute(select(1))
                result.append("ok")

        t = threading.Thread(target=use_in_thread)
        t.start()
        t.join()
        assert result == ["ok"]
    finally:
        engine.dispose()


def test_create_engine_for_url_non_sqlite_no_check_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 SQLite URL 不添加 ``check_same_thread`` 参数。

    用 ``monkeypatch`` 替换 ``create_engine``，避免 ``create_engine`` 实际
    尝试导入 dialect 驱动（如 ``psycopg``），从而无需安装 PostgreSQL 驱动
    即可验证函数对非 SQLite URL 的处理逻辑。捕获调用参数，断言
    ``connect_args`` 为空 dict（即未注入 SQLite 专属参数）。
    """

    captured: dict[str, object] = {}

    def fake_create_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr("research_rag.db.session.create_engine", fake_create_engine)

    create_engine_for_url("postgresql+psycopg://u:p@localhost/db")

    assert captured["url"] == "postgresql+psycopg://u:p@localhost/db"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    connect_args = kwargs.get("connect_args")
    assert connect_args == {}
    assert "check_same_thread" not in (connect_args or {})
