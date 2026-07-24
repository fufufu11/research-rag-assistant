"""FeedbackRepository 单元测试（阶段 10.2 用户反馈闭环）。

测试覆盖：
- upsert：新建（返回实例，id 自动生成，message_id 可空）+ 更新（同 request_id
  覆盖 rating/comment，updated_at 变化，id 不变）
- get_by_request_id：命中 + 未命中
- delete：删除成功
- list：全部 / 按 rating 筛选 / 按 conversation_id 筛选（join messages）/ limit

测试用内存 SQLite，与 ``test_conversation_repositories.py`` 一致的隔离方式。
Repository 只 flush 不 commit，测试用 ``session.commit()`` 验证持久化。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from research_rag.db.models import Base, FeedbackRating, MessageRole
from research_rag.db.repositories import ConversationRepository, FeedbackRepository

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
def repo(session: Session) -> FeedbackRepository:
    """创建 FeedbackRepository 实例。"""

    return FeedbackRepository(session)


@pytest.fixture
def conv_repo(session: Session) -> ConversationRepository:
    """创建 ConversationRepository 实例（构造 message_id 关联场景用）。"""

    return ConversationRepository(session)


def _make_message(conv_repo: ConversationRepository, session: Session) -> uuid.UUID:
    """创建一条 assistant 消息并 commit，返回 message_id（供 feedback 关联）。"""

    conv = conv_repo.create(title="测试会话")
    msg = conv_repo.add_message(
        conv.id,
        role=MessageRole.ASSISTANT,
        content="测试答案 [C1]",
        citations=None,
    )
    session.commit()
    return msg.id


# ---------------------------------------------------------------------------
# upsert —— 新建
# ---------------------------------------------------------------------------


def test_upsert_creates_new_feedback(repo: FeedbackRepository) -> None:
    """upsert：request_id 不存在时新建，id 自动生成，message_id 可空。"""

    request_id = uuid.uuid4()
    fb = repo.upsert(request_id=request_id, rating=FeedbackRating.LIKE)

    assert isinstance(fb.id, uuid.UUID)
    assert fb.request_id == request_id
    assert fb.rating == FeedbackRating.LIKE
    assert fb.message_id is None
    assert fb.comment is None
    assert fb.created_at is not None
    assert fb.updated_at is not None


def test_upsert_creates_with_message_id_and_comment(
    repo: FeedbackRepository, conv_repo: ConversationRepository, session: Session
) -> None:
    """upsert：新建时支持 message_id（多轮场景）与 comment。"""

    message_id = _make_message(conv_repo, session)
    request_id = uuid.uuid4()
    fb = repo.upsert(
        request_id=request_id,
        rating=FeedbackRating.DISLIKE,
        message_id=message_id,
        comment="答案引用错位",
    )

    assert fb.message_id == message_id
    assert fb.rating == FeedbackRating.DISLIKE
    assert fb.comment == "答案引用错位"


def test_upsert_updates_existing(repo: FeedbackRepository, session: Session) -> None:
    """upsert：同 request_id 再次调用应更新 rating/comment，id 不变。

    这是 Upsert 语义的核心：like↔dislike 切换通过 POST 同一 request_id 实现，
    无需 PATCH 端点。
    """

    request_id = uuid.uuid4()
    fb1 = repo.upsert(request_id=request_id, rating=FeedbackRating.LIKE, comment="好")
    session.commit()
    original_id = fb1.id
    original_created_at = fb1.created_at

    # 强制 updated_at 漂移：sleep 后再 upsert
    import time

    time.sleep(0.01)
    fb2 = repo.upsert(request_id=request_id, rating=FeedbackRating.DISLIKE, comment="再想想不对")
    session.commit()

    assert fb2.id == original_id  # 同一记录
    assert fb2.request_id == request_id
    assert fb2.rating == FeedbackRating.DISLIKE  # 已切换
    assert fb2.comment == "再想想不对"
    assert fb2.created_at == original_created_at  # 创建时间不变
    assert fb2.updated_at >= original_created_at  # 更新时间推进


# ---------------------------------------------------------------------------
# get_by_request_id
# ---------------------------------------------------------------------------


def test_get_by_request_id_hit(repo: FeedbackRepository, session: Session) -> None:
    """get_by_request_id：命中返回 Feedback 实例。"""

    request_id = uuid.uuid4()
    repo.upsert(request_id=request_id, rating=FeedbackRating.LIKE)
    session.commit()

    found = repo.get_by_request_id(request_id)
    assert found is not None
    assert found.request_id == request_id
    assert found.rating == FeedbackRating.LIKE


def test_get_by_request_id_miss(repo: FeedbackRepository) -> None:
    """get_by_request_id：未命中返回 None。"""

    assert repo.get_by_request_id(uuid.uuid4()) is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_feedback(repo: FeedbackRepository, session: Session) -> None:
    """delete：删除后 get_by_request_id 返回 None。"""

    request_id = uuid.uuid4()
    fb = repo.upsert(request_id=request_id, rating=FeedbackRating.LIKE)
    session.commit()

    repo.delete(fb)
    session.commit()

    assert repo.get_by_request_id(request_id) is None


# ---------------------------------------------------------------------------
# list —— 全部 / 按 rating 筛选 / 按 conversation_id 筛选 / limit
# ---------------------------------------------------------------------------


def test_list_returns_all_ordered_by_created_at_desc(
    repo: FeedbackRepository, session: Session
) -> None:
    """list：无筛选返回全部，按 created_at 降序（最新的在前）。"""

    r1 = uuid.uuid4()
    r2 = uuid.uuid4()
    repo.upsert(request_id=r1, rating=FeedbackRating.LIKE)
    session.commit()
    import time

    time.sleep(0.01)
    repo.upsert(request_id=r2, rating=FeedbackRating.DISLIKE)
    session.commit()

    items = repo.list()
    assert len(items) == 2
    assert items[0].request_id == r2  # 最新（后创建）的在前
    assert items[1].request_id == r1


def test_list_filter_by_rating(repo: FeedbackRepository, session: Session) -> None:
    """list：rating 筛选只返回该类型反馈。"""

    repo.upsert(request_id=uuid.uuid4(), rating=FeedbackRating.LIKE)
    repo.upsert(request_id=uuid.uuid4(), rating=FeedbackRating.LIKE)
    repo.upsert(request_id=uuid.uuid4(), rating=FeedbackRating.DISLIKE)
    session.commit()

    likes = repo.list(rating=FeedbackRating.LIKE)
    dislikes = repo.list(rating=FeedbackRating.DISLIKE)
    assert len(likes) == 2
    assert all(f.rating == FeedbackRating.LIKE for f in likes)
    assert len(dislikes) == 1
    assert dislikes[0].rating == FeedbackRating.DISLIKE


def test_list_filter_by_conversation_id(
    repo: FeedbackRepository, conv_repo: ConversationRepository, session: Session
) -> None:
    """list：conversation_id 筛选走 message_id → messages.conversation_id join。

    单轮问答反馈（message_id=None）不应出现在按会话筛选结果中。
    """

    msg_id = _make_message(conv_repo, session)
    conv = conv_repo.list_all()[0]

    # 关联到会话的反馈
    repo.upsert(
        request_id=uuid.uuid4(),
        rating=FeedbackRating.LIKE,
        message_id=msg_id,
    )
    # 单轮问答反馈（message_id=None）
    repo.upsert(request_id=uuid.uuid4(), rating=FeedbackRating.LIKE)
    session.commit()

    items = repo.list(conversation_id=conv.id)
    assert len(items) == 1
    assert items[0].message_id == msg_id


def test_list_limit(repo: FeedbackRepository, session: Session) -> None:
    """list：limit 限制返回条数（取最新 N 条）。"""

    for _ in range(5):
        repo.upsert(request_id=uuid.uuid4(), rating=FeedbackRating.LIKE)
        session.commit()
        import time

        time.sleep(0.005)

    items = repo.list(limit=3)
    assert len(items) == 3


def test_list_filter_combination(
    repo: FeedbackRepository, conv_repo: ConversationRepository, session: Session
) -> None:
    """list：rating + conversation_id 组合筛选。"""

    msg_id = _make_message(conv_repo, session)
    conv = conv_repo.list_all()[0]

    repo.upsert(
        request_id=uuid.uuid4(),
        rating=FeedbackRating.LIKE,
        message_id=msg_id,
    )
    repo.upsert(
        request_id=uuid.uuid4(),
        rating=FeedbackRating.DISLIKE,
        message_id=msg_id,
    )
    session.commit()

    only_likes = repo.list(rating=FeedbackRating.LIKE, conversation_id=conv.id)
    assert len(only_likes) == 1
    assert only_likes[0].rating == FeedbackRating.LIKE


# ---------------------------------------------------------------------------
# 唯一约束
# ---------------------------------------------------------------------------


def test_request_id_unique_constraint(repo: FeedbackRepository, session: Session) -> None:
    """request_id 唯一约束：直接 INSERT 两条同 request_id 应抛 IntegrityError。

    upsert 方法依赖此约束保证幂等，但约束本身是数据库层兜底。
    """

    from sqlalchemy.exc import IntegrityError

    from research_rag.db.models import Feedback

    request_id = uuid.uuid4()
    session.add(Feedback(request_id=request_id, rating=FeedbackRating.LIKE))
    session.commit()

    session.add(Feedback(request_id=request_id, rating=FeedbackRating.DISLIKE))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()  # 清理失败的事务状态
