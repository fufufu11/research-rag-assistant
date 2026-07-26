"""ConversationRepository 单元测试（阶段 9.2 多轮对话）。

测试覆盖：
- create：创建 Conversation 并 flush，id 自动生成，document_ids 存 JSON
- get_by_id：查询（命中 + 未命中）
- list_all：列表 + 排序（updated_at 降序）
- delete：删除 + 级联删除 messages
- update_title：标题更新
- add_message：追加 user / assistant 消息（含 citations JSON）
- list_messages：消息列表 + 升序 + limit 截断（取最近 N 条翻转）

测试用内存 SQLite，与 ``test_repositories.py`` 一致的隔离方式。
Repository 只 flush 不 commit，测试用 ``session.commit()`` 验证持久化。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from research_rag.db.models import Base, MessageRole
from research_rag.db.repositories import ConversationRepository

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
def repo(session: Session) -> ConversationRepository:
    """创建 ConversationRepository 实例。"""

    return ConversationRepository(session)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def test_create_generates_id_and_defaults(repo: ConversationRepository) -> None:
    """create：id 自动生成，title/document_ids 默认为 None，时间戳填充。"""

    conv = repo.create()

    assert isinstance(conv.id, uuid.UUID)
    assert conv.title is None
    assert conv.document_ids is None
    assert conv.created_at is not None
    assert conv.updated_at is not None


def test_create_with_title_and_document_ids(repo: ConversationRepository) -> None:
    """create：传入 title 和 document_ids（UUID 字符串列表）。"""

    doc_id = str(uuid.uuid4())
    conv = repo.create(title="测试会话", document_ids=[doc_id])

    assert conv.title == "测试会话"
    assert conv.document_ids == [doc_id]


def test_create_persists_to_db(repo: ConversationRepository, session: Session) -> None:
    """create：flush 后数据在 DB 中可见（同 session 查询）。"""

    conv = repo.create(title="持久化测试")
    session.commit()

    fetched = repo.get_by_id(conv.id)
    assert fetched is not None
    assert fetched.title == "持久化测试"


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


def test_get_by_id_hit(repo: ConversationRepository) -> None:
    """get_by_id：命中返回 Conversation 实例。"""

    conv = repo.create(title="命中测试")
    fetched = repo.get_by_id(conv.id)

    assert fetched is not None
    assert fetched.id == conv.id
    assert fetched.title == "命中测试"


def test_get_by_id_miss(repo: ConversationRepository) -> None:
    """get_by_id：未命中返回 None。"""

    missing_id = uuid.uuid4()
    assert repo.get_by_id(missing_id) is None


# ---------------------------------------------------------------------------
# list_all
# ---------------------------------------------------------------------------


def test_list_all_returns_all_ordered_by_updated_at_desc(
    repo: ConversationRepository, session: Session
) -> None:
    """list_all：返回所有会话，按 updated_at 降序（最近活跃在前）。"""

    # 显式设置 created_at/updated_at 避免同秒歧义
    conv1 = repo.create(title="旧会话")
    conv1.created_at = datetime(2026, 1, 1, 10, 0, 0)
    conv1.updated_at = datetime(2026, 1, 1, 10, 0, 0)

    conv2 = repo.create(title="新会话")
    conv2.created_at = datetime(2026, 1, 2, 10, 0, 0)
    conv2.updated_at = datetime(2026, 1, 2, 10, 0, 0)

    session.flush()

    convs = repo.list_all()
    assert len(convs) == 2
    # 最近活跃（updated_at 更大）的在前
    assert convs[0].title == "新会话"
    assert convs[1].title == "旧会话"


def test_list_all_empty(repo: ConversationRepository) -> None:
    """list_all：空库返回空列表。"""

    assert repo.list_all() == []


# ---------------------------------------------------------------------------
# delete + 级联删除 messages
# ---------------------------------------------------------------------------


def test_delete_cascades_to_messages(repo: ConversationRepository, session: Session) -> None:
    """delete：删除会话后级联删除其消息。"""

    conv = repo.create(title="待删除")
    repo.add_message(conv.id, role=MessageRole.USER, content="问题1")
    repo.add_message(conv.id, role=MessageRole.ASSISTANT, content="答案1")
    session.flush()

    # 删除前：2 条消息
    assert len(repo.list_messages(conv.id)) == 2

    repo.delete(conv)
    session.commit()

    # 删除后：会话和消息都不存在
    assert repo.get_by_id(conv.id) is None
    assert repo.list_messages(conv.id) == []


# ---------------------------------------------------------------------------
# update_title
# ---------------------------------------------------------------------------


def test_update_title(repo: ConversationRepository) -> None:
    """update_title：更新标题并 flush。"""

    conv = repo.create()  # title=None
    assert conv.title is None

    updated = repo.update_title(conv, "新标题")
    assert updated.title == "新标题"
    # 同一实例（flush 不重建对象）
    assert updated is conv

    # 持久化检查
    fetched = repo.get_by_id(conv.id)
    assert fetched is not None
    assert fetched.title == "新标题"


# ---------------------------------------------------------------------------
# add_message
# ---------------------------------------------------------------------------


def test_add_message_user(repo: ConversationRepository) -> None:
    """add_message：追加 user 消息，citations 为 None。"""

    conv = repo.create()
    msg = repo.add_message(conv.id, role=MessageRole.USER, content="用户问题")

    assert msg.id is not None
    assert msg.conversation_id == conv.id
    assert msg.role == MessageRole.USER
    assert msg.content == "用户问题"
    assert msg.citations is None
    assert msg.created_at is not None


def test_add_message_assistant_with_citations(repo: ConversationRepository) -> None:
    """add_message：追加 assistant 消息，citations 存 JSON 快照。"""

    conv = repo.create()
    citations_snapshot = [
        {"document_id": "doc-1", "document_name": "论文A", "start_page": 1, "end_page": 1}
    ]
    msg = repo.add_message(
        conv.id,
        role=MessageRole.ASSISTANT,
        content="答案 [C1]。",
        citations=citations_snapshot,
    )

    assert msg.role == MessageRole.ASSISTANT
    assert msg.content == "答案 [C1]。"
    assert msg.citations == citations_snapshot


def test_add_message_with_request_id_persists_and_defaults_to_none(
    repo: ConversationRepository,
) -> None:
    """add_message：``request_id`` 可选参数，传入则持久化，未传默认 None（ADR 0003）。

    验证 repository 层签名扩展：``request_id`` 作为可选参数加到 ``add_message``，
    避免在 service 层手填 ORM 属性（保持 repository 作为 ORM 写入唯一入口）。
    """

    conv = repo.create()
    request_id = uuid.uuid4()

    # assistant 消息传 request_id → 持久化
    assistant_msg = repo.add_message(
        conv.id,
        role=MessageRole.ASSISTANT,
        content="答案 [C1]。",
        request_id=request_id,
    )
    assert assistant_msg.request_id == request_id

    # user 消息不传 request_id → 默认 None（兼容现有调用点）
    user_msg = repo.add_message(
        conv.id,
        role=MessageRole.USER,
        content="问题",
    )
    assert user_msg.request_id is None


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


def test_list_messages_ordered_by_created_at_asc(
    repo: ConversationRepository, session: Session
) -> None:
    """list_messages：按 created_at 升序（对话时间顺序）。"""

    conv = repo.create()

    # 显式设置 created_at 保证顺序
    msg1 = repo.add_message(conv.id, role=MessageRole.USER, content="第1条")
    msg1.created_at = datetime(2026, 1, 1, 10, 0, 0)

    msg2 = repo.add_message(conv.id, role=MessageRole.ASSISTANT, content="第2条")
    msg2.created_at = datetime(2026, 1, 1, 10, 0, 1)

    msg3 = repo.add_message(conv.id, role=MessageRole.USER, content="第3条")
    msg3.created_at = datetime(2026, 1, 1, 10, 0, 2)

    session.flush()

    msgs = repo.list_messages(conv.id)
    assert len(msgs) == 3
    assert msgs[0].content == "第1条"
    assert msgs[1].content == "第2条"
    assert msgs[2].content == "第3条"


def test_list_messages_empty(repo: ConversationRepository) -> None:
    """list_messages：会话无消息返回空列表。"""

    conv = repo.create()
    assert repo.list_messages(conv.id) == []


def test_list_messages_with_limit_returns_recent_n(
    repo: ConversationRepository, session: Session
) -> None:
    """list_messages：limit=N 返回最近 N 条（降序取后翻转，保持升序）。"""

    conv = repo.create()
    for i in range(5):
        msg = repo.add_message(conv.id, role=MessageRole.USER, content=f"消息{i}")
        msg.created_at = datetime(2026, 1, 1, 10, 0, i)
    session.flush()

    # limit=2 → 最近 2 条（消息3、消息4），按时间升序返回
    msgs = repo.list_messages(conv.id, limit=2)
    assert len(msgs) == 2
    assert msgs[0].content == "消息3"
    assert msgs[1].content == "消息4"


def test_list_messages_limit_zero_or_negative_returns_all(
    repo: ConversationRepository, session: Session
) -> None:
    """list_messages：limit=0 或负数返回全部（降级处理）。"""

    conv = repo.create()
    repo.add_message(conv.id, role=MessageRole.USER, content="消息1")
    repo.add_message(conv.id, role=MessageRole.ASSISTANT, content="消息2")
    session.flush()

    assert len(repo.list_messages(conv.id, limit=0)) == 2
    assert len(repo.list_messages(conv.id, limit=-1)) == 2
