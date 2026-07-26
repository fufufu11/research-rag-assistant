"""Message.request_id 字段单元测试（Issue #89 prefactor 切片）。

测试覆盖：
- request_id 默认 None（可空列）
- request_id 可持久化非 None UUID（round-trip）
- 多条 request_id=None 不破坏唯一约束（SQLite NULL 语义）
- 两条相同非 None request_id 报 IntegrityError（唯一约束生效）

依据 ADR 0003（新写）：Message 表新增可空 request_id 列，供历史消息反馈
反查 feedback 状态。旧消息（迁移前）保持 NULL，不回填。

测试用内存 SQLite，与 test_conversation_repositories.py 一致的隔离方式。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from research_rag.db.models import Base, Conversation, Message, MessageRole

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
    """基于 engine 创建 Session，测试结束关闭。"""

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    sess: Session = factory()
    yield sess
    sess.close()


# ---------------------------------------------------------------------------
# Tracer Bullet：request_id 默认 None
# ---------------------------------------------------------------------------


def test_message_request_id_defaults_to_none(session: Session) -> None:
    """Message.request_id 默认为 None（可空列）。

    新建 Message 不传 request_id 时，字段值应为 None。这是 ADR 0003 的
    基础行为：旧消息（迁移前）和 user 消息（#90 决策：仅 assistant 写入）
    都保持 None。
    """

    conv = Conversation()
    session.add(conv)
    session.commit()

    msg = Message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="问题",
    )
    session.add(msg)
    session.commit()

    assert msg.request_id is None

    # round-trip：从 DB 重新查询，验证持久化
    fetched = session.get(Message, msg.id)
    assert fetched is not None
    assert fetched.request_id is None


# ---------------------------------------------------------------------------
# request_id 可持久化非 None UUID（round-trip）
# ---------------------------------------------------------------------------


def test_message_request_id_persists_uuid(session: Session) -> None:
    """Message.request_id 可持久化非 None UUID，并从 DB 查回一致。

    assistant 消息（#90 实现）会写入 request_id，本测试验证列支持 UUID
    round-trip：写入 → commit → 重新查询 → 值一致且类型为 uuid.UUID。
    """

    conv = Conversation()
    session.add(conv)
    session.commit()

    request_id = uuid.uuid4()
    msg = Message(
        conversation_id=conv.id,
        role=MessageRole.ASSISTANT,
        content="答案 [C1]。",
        request_id=request_id,
    )
    session.add(msg)
    session.commit()

    assert msg.request_id == request_id

    # round-trip：从 DB 重新查询
    fetched = session.get(Message, msg.id)
    assert fetched is not None
    assert fetched.request_id == request_id
    assert isinstance(fetched.request_id, uuid.UUID)


# ---------------------------------------------------------------------------
# 多条 request_id=None 不破坏唯一约束（SQLite NULL 语义）
# ---------------------------------------------------------------------------


def test_multiple_null_request_ids_allowed(session: Session) -> None:
    """多条 request_id=None 的 Message 可共存，不触发唯一约束冲突。

    SQL 标准对 NULL 的唯一约束语义：NULL != NULL，因此多条 NULL 不冲突。
    这是 ADR 0003 决策的核心依据——旧消息（迁移前）和 user 消息都为 NULL，
    不会因唯一约束而无法持久化。
    """

    conv = Conversation()
    session.add(conv)
    session.commit()

    # 两条 user 消息（request_id 默认 None）
    msg1 = Message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="问题1",
    )
    msg2 = Message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="问题2",
    )
    session.add_all([msg1, msg2])
    session.commit()  # 不应抛 IntegrityError

    assert msg1.request_id is None
    assert msg2.request_id is None
    assert msg1.id != msg2.id


# ---------------------------------------------------------------------------
# 两条相同非 None request_id 报 IntegrityError（唯一约束生效）
# ---------------------------------------------------------------------------


def test_duplicate_non_null_request_id_raises_integrity_error(
    session: Session,
) -> None:
    """两条相同非 None request_id 的 Message 触发 IntegrityError。

    唯一约束对非 NULL 值生效：同一 request_id 不能对应两条 Message。
    这是历史消息反馈反查的完整性保证——一个 request_id 唯一映射到一条
    assistant 消息，反馈状态不会歧义。
    """

    conv = Conversation()
    session.add(conv)
    session.commit()

    request_id = uuid.uuid4()
    msg1 = Message(
        conversation_id=conv.id,
        role=MessageRole.ASSISTANT,
        content="答案1",
        request_id=request_id,
    )
    session.add(msg1)
    session.commit()

    # 第二条用相同 request_id，应触发唯一约束
    msg2 = Message(
        conversation_id=conv.id,
        role=MessageRole.ASSISTANT,
        content="答案2",
        request_id=request_id,
    )
    session.add(msg2)

    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()  # 回滚避免污染后续测试
