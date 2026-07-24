"""反馈 API 路由单元测试（阶段 10.2 用户反馈闭环）。

测试覆盖：
- POST /api/v1/feedback：创建（201）/ 更新（200，rating 切换）/ 带 message_id+comment
  / 非法 rating（422）/ 缺少必填字段（422）
- GET /api/v1/feedback/{request_id}：命中（200）/ 不存在（404）
- GET /api/v1/feedback：列表全部 / 按 rating 筛选 / 按 conversation_id 筛选
  （join messages）/ limit / 非法 rating（422）
- DELETE /api/v1/feedback/{request_id}：成功（204）/ 不存在（404）/ 删除后 GET 应 404

测试策略（与 test_api_conversations.py 的 Mock 风格不同）：
- feedback 路由直接调 ``FeedbackRepository``（无 service 层），用真实文件 SQLite
  + 真实 ORM 端到端验证，覆盖路由编排 + 事务 commit + schema 转换 + 异常映射
  全链路。
- 用 ``tmp_path`` 下的文件 SQLite（非内存），避免内存 SQLite 跨连接隔离问题。
- ``Base.metadata.create_all`` 建表（不走 Alembic 迁移，迁移由 CI 单独验证）。
- ``monkeypatch.setenv("QDRANT_ENABLED", "false")`` / ``RERANKER_ENABLED=false``
  避免 lifespan 尝试加载真实组件。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from research_rag.api.app import create_app
from research_rag.db.models import Base, MessageRole
from research_rag.db.repositories import ConversationRepository

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy import Engine


# ---------------------------------------------------------------------------
# Fixtures：文件 SQLite + 建表 + 应用
# ---------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: pytest.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """创建文件 SQLite engine 并建表。

    用 ``tmp_path`` 隔离每个测试函数的数据库。``monkeypatch`` 关闭 Qdrant/Reranker
    避免 lifespan 尝试加载真实组件。
    """

    monkeypatch.setenv("QDRANT_ENABLED", "false")
    monkeypatch.setenv("RERANKER_ENABLED", "false")

    db_file = tmp_path / "test_feedback.db"
    eng = create_engine(f"sqlite:///{db_file}", future=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    """基于 ``engine`` 创建 session 工厂。"""

    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture
def app(session_factory: sessionmaker[Session]) -> FastAPI:
    """创建应用实例：注入文件 SQLite factory。

    不 override 任何依赖——feedback 路由直接用 ``get_db``（Session），走真实
    Repository 端到端验证。
    """

    return create_app(session_factory=session_factory)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """``TestClient``：基于 httpx 的 ASGI 测试客户端。用 ``with`` 触发 lifespan。"""

    with TestClient(app) as c:
        yield c


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """独立 Session，用于测试中直接写前置数据（如 Conversation + Message）。

    与路由请求用的 Session 不同（路由用 ``get_db`` 每请求一个 Session），但共享
    同一 engine/文件，因此数据可见。
    """

    sess: Session = session_factory()
    yield sess
    sess.close()


def _make_message_id(session: Session) -> tuple[uuid.UUID, uuid.UUID]:
    """直接用 Repository 创建一条会话 + assistant 消息，返回 (conversation_id, message_id)。

    供 feedback 的 message_id 关联与 conversation_id 筛选测试用。
    """

    conv_repo = ConversationRepository(session)
    conv = conv_repo.create(title="测试会话")
    msg = conv_repo.add_message(
        conv.id,
        role=MessageRole.ASSISTANT,
        content="测试答案 [C1]",
        citations=None,
    )
    session.commit()
    return conv.id, msg.id


# ---------------------------------------------------------------------------
# POST /api/v1/feedback —— 创建 / 更新
# ---------------------------------------------------------------------------


def test_post_creates_feedback_returns_201(client: TestClient) -> None:
    """POST：request_id 不存在 → 201 Created + FeedbackRead。"""

    request_id = uuid.uuid4()
    response = client.post(
        "/api/v1/feedback",
        json={"request_id": str(request_id), "rating": "like"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["request_id"] == str(request_id)
    assert body["rating"] == "like"
    assert body["message_id"] is None
    assert body["comment"] is None
    assert "id" in body
    assert "created_at" in body
    assert "updated_at" in body


def test_post_updates_feedback_returns_200(client: TestClient) -> None:
    """POST：同 request_id 再次提交 → 200 OK + rating/comment 更新，id 不变。

    这是 Upsert 语义：like↔dislike 切换通过 POST 同一 request_id 实现。
    """

    request_id = uuid.uuid4()
    # 第一次：创建 like
    r1 = client.post(
        "/api/v1/feedback",
        json={"request_id": str(request_id), "rating": "like", "comment": "好"},
    )
    assert r1.status_code == 201
    original_id = r1.json()["id"]

    # 第二次：更新为 dislike
    r2 = client.post(
        "/api/v1/feedback",
        json={
            "request_id": str(request_id),
            "rating": "dislike",
            "comment": "再想想不对",
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == original_id  # 同一记录
    assert body["rating"] == "dislike"
    assert body["comment"] == "再想想不对"


def test_post_with_message_id_and_comment(client: TestClient, session: Session) -> None:
    """POST：带 message_id（多轮场景）和 comment。"""

    _, message_id = _make_message_id(session)
    request_id = uuid.uuid4()
    response = client.post(
        "/api/v1/feedback",
        json={
            "request_id": str(request_id),
            "rating": "dislike",
            "message_id": str(message_id),
            "comment": "引用错位",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["message_id"] == str(message_id)
    assert body["comment"] == "引用错位"


def test_post_invalid_rating_returns_422(client: TestClient) -> None:
    """POST：rating 非 like/dislike → 422（Pydantic 枚举校验）。"""

    response = client.post(
        "/api/v1/feedback",
        json={"request_id": str(uuid.uuid4()), "rating": "meh"},
    )
    assert response.status_code == 422


def test_post_missing_required_field_returns_422(client: TestClient) -> None:
    """POST：缺少必填字段 request_id → 422。"""

    response = client.post(
        "/api/v1/feedback",
        json={"rating": "like"},
    )
    assert response.status_code == 422


def test_post_comment_too_long_returns_422(client: TestClient) -> None:
    """POST：comment 超过 2000 字符 → 422（max_length 校验）。"""

    response = client.post(
        "/api/v1/feedback",
        json={
            "request_id": str(uuid.uuid4()),
            "rating": "like",
            "comment": "x" * 2001,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/feedback/{request_id} —— 单条查询
# ---------------------------------------------------------------------------


def test_get_feedback_hit(client: TestClient) -> None:
    """GET 单条：命中返回 200 + FeedbackRead。"""

    request_id = uuid.uuid4()
    client.post(
        "/api/v1/feedback",
        json={"request_id": str(request_id), "rating": "like"},
    )

    response = client.get(f"/api/v1/feedback/{request_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == str(request_id)
    assert body["rating"] == "like"


def test_get_feedback_not_found_returns_404(client: TestClient) -> None:
    """GET 单条：不存在 → 404 + ErrorResponse。"""

    response = client.get(f"/api/v1/feedback/{uuid.uuid4()}")
    assert response.status_code == 404
    assert "detail" in response.json()


# ---------------------------------------------------------------------------
# GET /api/v1/feedback —— 列表筛选
# ---------------------------------------------------------------------------


def test_list_feedback_empty(client: TestClient) -> None:
    """GET 列表：空库返回 200 + items=[]。"""

    response = client.get("/api/v1/feedback")
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_list_feedback_all(client: TestClient) -> None:
    """GET 列表：返回全部，按 created_at 降序。"""

    r1 = uuid.uuid4()
    r2 = uuid.uuid4()
    client.post("/api/v1/feedback", json={"request_id": str(r1), "rating": "like"})
    client.post("/api/v1/feedback", json={"request_id": str(r2), "rating": "dislike"})

    response = client.get("/api/v1/feedback")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["request_id"] == str(r2)  # 最新（后创建）的在前


def test_list_feedback_filter_by_rating(client: TestClient) -> None:
    """GET 列表：?rating=like 只返回点赞。"""

    client.post("/api/v1/feedback", json={"request_id": str(uuid.uuid4()), "rating": "like"})
    client.post(
        "/api/v1/feedback",
        json={"request_id": str(uuid.uuid4()), "rating": "dislike"},
    )

    response = client.get("/api/v1/feedback?rating=like")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["rating"] == "like"


def test_list_feedback_filter_by_conversation_id(client: TestClient, session: Session) -> None:
    """GET 列表：?conversation_id=... 走 message_id → messages.conversation_id join。

    单轮问答反馈（message_id=None）不出现在按会话筛选结果中。
    """

    conv_id, message_id = _make_message_id(session)
    # 关联到会话的反馈
    client.post(
        "/api/v1/feedback",
        json={
            "request_id": str(uuid.uuid4()),
            "rating": "like",
            "message_id": str(message_id),
        },
    )
    # 单轮问答反馈（message_id=None）
    client.post("/api/v1/feedback", json={"request_id": str(uuid.uuid4()), "rating": "like"})

    response = client.get(f"/api/v1/feedback?conversation_id={conv_id}")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["message_id"] == str(message_id)


def test_list_feedback_limit(client: TestClient) -> None:
    """GET 列表：?limit=N 取最新 N 条。"""

    for _ in range(5):
        client.post(
            "/api/v1/feedback",
            json={"request_id": str(uuid.uuid4()), "rating": "like"},
        )

    response = client.get("/api/v1/feedback?limit=3")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3


def test_list_feedback_invalid_rating_returns_422(client: TestClient) -> None:
    """GET 列表：?rating=meh → 422（枚举校验）。"""

    response = client.get("/api/v1/feedback?rating=meh")
    assert response.status_code == 422


def test_list_feedback_filter_combination(client: TestClient, session: Session) -> None:
    """GET 列表：?rating=like&conversation_id=... 组合筛选。"""

    conv_id, message_id = _make_message_id(session)
    client.post(
        "/api/v1/feedback",
        json={
            "request_id": str(uuid.uuid4()),
            "rating": "like",
            "message_id": str(message_id),
        },
    )
    client.post(
        "/api/v1/feedback",
        json={
            "request_id": str(uuid.uuid4()),
            "rating": "dislike",
            "message_id": str(message_id),
        },
    )

    response = client.get(f"/api/v1/feedback?rating=like&conversation_id={conv_id}")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["rating"] == "like"


# ---------------------------------------------------------------------------
# DELETE /api/v1/feedback/{request_id} —— 撤销
# ---------------------------------------------------------------------------


def test_delete_feedback_success_returns_204(client: TestClient) -> None:
    """DELETE：成功 → 204 No Content。"""

    request_id = uuid.uuid4()
    client.post(
        "/api/v1/feedback",
        json={"request_id": str(request_id), "rating": "like"},
    )

    response = client.delete(f"/api/v1/feedback/{request_id}")
    assert response.status_code == 204

    # 删除后 GET 应 404
    assert client.get(f"/api/v1/feedback/{request_id}").status_code == 404


def test_delete_feedback_not_found_returns_404(client: TestClient) -> None:
    """DELETE：不存在 → 404。"""

    response = client.delete(f"/api/v1/feedback/{uuid.uuid4()}")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 路径参数校验
# ---------------------------------------------------------------------------


def test_get_feedback_invalid_uuid_returns_422(client: TestClient) -> None:
    """GET 单条：路径参数非合法 UUID → 422（FastAPI 自动校验，不调路由）。"""

    response = client.get("/api/v1/feedback/not-a-uuid")
    assert response.status_code == 422
