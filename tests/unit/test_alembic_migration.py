"""Alembic 迁移可执行性测试。

验证：
1. ``alembic upgrade head`` 能在干净 SQLite 上执行
2. 迁移后表结构正确（documents、chunks 表与列存在）
3. 关键索引和约束存在（sha256 唯一索引、(document_id, chunk_index) 唯一约束）
4. 外键带 ``ON DELETE CASCADE``（用 ``PRAGMA foreign_key_list`` 反射）
5. ``alembic downgrade base`` 能回滚（删除表）

这确保后续修改模型后生成的迁移在本机能跑通，CI 不会因迁移失败而中断。

测试用临时文件 SQLite（``tmp_path`` fixture 提供独立目录），
每个测试函数用独立数据库文件，互不干扰。不用 ``:memory:`` 是因为
alembic 的 env.py 用 ``NullPool``，连接关闭后内存数据库会丢失。

Note:
    SQLite 的 SQLAlchemy ``inspector.get_foreign_keys()`` 对 ``ondelete``
    反射不稳定（不同 SQLite/SQLAlchemy 版本可能不返回该字段），故外键
    级联用 ``PRAGMA foreign_key_list`` 直接查询。``unique`` 在 SQLite 中
    返回 0/1 整数而非 Python bool，用 ``bool()`` 转换。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# 项目根目录（tests/unit/test_alembic_migration.py → 项目根）
#   __file__ = .../tests/unit/test_alembic_migration.py
#   parents[0] = tests/unit
#   parents[1] = tests
#   parents[2] = 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def _make_config(database_url: str) -> Config:
    """构造 Alembic Config，显式覆盖 sqlalchemy.url 指向临时数据库。

    env.py 也会调用 ``get_database_url()`` 读环境变量，这里显式设置
    确保 Config 与 env.py 用同一个 URL。
    """

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture
def migrated_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """运行 ``alembic upgrade head`` 后返回 engine，测试结束自动 dispose。

    每个测试函数用独立的临时 SQLite 文件，互不干扰。返回 engine 而非
    inspector，是因为部分反射（如外键 ondelete）需要用 ``PRAGMA`` 直接查询。
    """

    db_file = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_file}"
    # env.py 调用 get_database_url()，需设置环境变量
    monkeypatch.setenv("DATABASE_URL", db_url)

    config = _make_config(db_url)
    command.upgrade(config, "head")

    engine = create_engine(db_url)
    try:
        yield engine
    finally:
        engine.dispose()


def test_upgrade_head_creates_all_tables(migrated_engine: Engine) -> None:
    """upgrade head 后 documents、chunks、alembic_version 表都存在。"""

    inspector = inspect(migrated_engine)
    table_names = set(inspector.get_table_names())
    assert "documents" in table_names
    assert "chunks" in table_names
    # alembic_version 表记录当前迁移版本，alembic 自动创建
    assert "alembic_version" in table_names


def test_documents_columns(migrated_engine: Engine) -> None:
    """documents 表包含 PROJECT_PLAN 第 7.1 节全部 9 个字段。"""

    inspector = inspect(migrated_engine)
    columns = {col["name"] for col in inspector.get_columns("documents")}
    expected = {
        "id",
        "original_name",
        "stored_name",
        "sha256",
        "page_count",
        "status",
        "error_message",
        "created_at",
        "updated_at",
    }
    assert columns == expected


def test_chunks_columns(migrated_engine: Engine) -> None:
    """chunks 表包含 PROJECT_PLAN 第 7.2 节全部 8 个字段。"""

    inspector = inspect(migrated_engine)
    columns = {col["name"] for col in inspector.get_columns("chunks")}
    expected = {
        "id",
        "document_id",
        "page_number",
        "chunk_index",
        "content",
        "char_count",
        "vector_id",
        "created_at",
    }
    assert columns == expected


def test_documents_sha256_unique_index(migrated_engine: Engine) -> None:
    """documents.sha256 有唯一索引（US-001 去重依赖）。"""

    inspector = inspect(migrated_engine)
    indexes = inspector.get_indexes("documents")
    sha256_idx = [i for i in indexes if "sha256" in i["column_names"]]
    assert len(sha256_idx) == 1
    # SQLite 返回 0/1 整数而非 Python bool，用 bool() 归一化
    assert bool(sha256_idx[0]["unique"]) is True


def test_chunks_unique_constraint(migrated_engine: Engine) -> None:
    """chunks 表有 (document_id, chunk_index) 唯一约束。"""

    inspector = inspect(migrated_engine)
    constraints = inspector.get_unique_constraints("chunks")
    doc_chunk_constraints = [
        c for c in constraints if set(c["column_names"]) == {"document_id", "chunk_index"}
    ]
    assert len(doc_chunk_constraints) == 1


def test_chunks_foreign_key_cascade(migrated_engine: Engine) -> None:
    """chunks.document_id 外键带 ON DELETE CASCADE（兜底级联删除）。

    用 ``PRAGMA foreign_key_list`` 直接查询，因为 SQLAlchemy 的
    ``inspector.get_foreign_keys()`` 对 SQLite 的 ``ondelete`` 反射不稳定。
    PRAGMA 返回列顺序: id, seq, table, from, to, on_update, on_delete, match。
    """

    with migrated_engine.connect() as conn:
        rows = conn.execute(text("PRAGMA foreign_key_list(chunks)")).fetchall()
    # 应至少有一条指向 documents 的外键
    assert len(rows) >= 1
    # 找到指向 documents 表的那条，验证 on_delete = CASCADE
    fk_to_documents = [r for r in rows if r[2] == "documents"]
    assert len(fk_to_documents) == 1
    # row[6] 是 on_delete 列
    assert fk_to_documents[0][6] == "CASCADE"


def test_downgrade_base_drops_tables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """downgrade base 后 documents 和 chunks 表都被删除。"""

    db_file = tmp_path / "downgrade_test.db"
    db_url = f"sqlite:///{db_file}"
    monkeypatch.setenv("DATABASE_URL", db_url)

    config = _make_config(db_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(db_url)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        # base 状态下业务表都不存在
        assert "documents" not in table_names
        assert "chunks" not in table_names
    finally:
        engine.dispose()
