"""会话管理 API 路由单元测试（阶段 9.2 多轮对话）。

测试覆盖：
- POST /api/v1/conversations：创建成功（201，默认值/带 title+document_ids）
- GET /api/v1/conversations：列表（200，items 数组，messages 为 None）
- GET /api/v1/conversations/{id}：详情（200，含完整 messages）、不存在（404）
- DELETE /api/v1/conversations/{id}：删除成功（204）、不存在（404）
- GET /api/v1/conversations/{id}/messages：消息列表（200）、不存在（404）
- 路径参数非合法 UUID：FastAPI 自动返回 422（不调 service）

测试策略（与 test_api_documents.py / test_api_queries.py 一致）：
- 用 ``fastapi.testclient.TestClient``（基于 httpx，不起真实 uvicorn）。
- 用 ``app.dependency_overrides[get_qa_service]`` 把 service 换成
  ``MagicMock(spec=QaService)``，完全跳过真实数据库与业务逻辑。
- Mock service 方法返回预构造的 ``Conversation`` / ``Message`` ORM 实例
  （直接构造，不持久化），验证路由层正确调 service、正确转 schema、
  正确映射异常到 HTTP 状态码。
- 用内存 SQLite ``session_factory`` 传入 ``create_app``，避免 ``lifespan``
  创建真实文件数据库。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from research_rag.api.app import create_app
from research_rag.api.dependencies import get_qa_service
from research_rag.db.models import MessageRole
from research_rag.db.session import create_session_factory
from research_rag.services.qa_service import ConversationNotFoundError, QaService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI

    from research_rag.db.models import Conversation, Message


# ---------------------------------------------------------------------------
# 辅助：构造测试用 ORM 实例（不持久化）
# ---------------------------------------------------------------------------


def make_conversation(**overrides: object) -> Conversation:
    """构造一个 ``Conversation`` ORM 实例，不落库。

    ``id`` / ``created_at`` / ``updated_at`` 显式提供，因为 ORM 的
    ``default`` / ``onupdate`` 回调只在 ``flush`` 时触发，未持久化的实例
    这些属性为 ``None``，会导致 ``ConversationRead.model_validate`` 时
    Pydantic 校验失败。
    """

    from research_rag.db.models import Conversation

    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "title": None,
        "document_ids": None,
        "created_at": datetime.now(UTC).replace(tzinfo=None),
        "updated_at": datetime.now(UTC).replace(tzinfo=None),
    }
    defaults.update(overrides)
    return Conversation(**defaults)  # type: ignore[arg-type]


def make_message(**overrides: object) -> Message:
    """构造一个 ``Message`` ORM 实例，不落库。"""

    from research_rag.db.models import Message

    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "role": MessageRole.USER,
        "content": "用户问题。",
        "citations": None,
        "created_at": datetime.now(UTC).replace(tzinfo=None),
    }
    defaults.update(overrides)
    return Message(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_service() -> MagicMock:
    """``MagicMock(spec=QaService)``：限定只能调 QaService 的方法。"""

    return MagicMock(spec=QaService)


@pytest.fixture
def app(mock_service: MagicMock) -> FastAPI:
    """创建应用实例：注入内存 SQLite factory，override service 依赖。"""

    app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
    app.dependency_overrides[get_qa_service] = lambda: mock_service
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """``TestClient``：基于 httpx 的 ASGI 测试客户端。用 ``with`` 触发 lifespan。"""

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/conversations —— 创建
# ---------------------------------------------------------------------------


def test_create_conversation_with_defaults(client: TestClient, mock_service: MagicMock) -> None:
    """默认创建：无 title、无 document_ids → 201 + ConversationRead。"""

    conv = make_conversation()
    mock_service.create_conversation.return_value = conv

    response = client.post("/api/v1/conversations", json={})

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == str(conv.id)
    assert body["title"] is None
    assert body["document_ids"] is None
    # 新建会话无消息：路由返回 ORM Conversation，Pydantic 从 relationship 读取，
    # 未持久化实例的懒加载返回空列表（而非 None）。
    assert body["messages"] in (None, [])
    # 验证 service 调用参数：title 和 document_ids 均为 None
    mock_service.create_conversation.assert_called_once_with(title=None, document_ids=None)


def test_create_conversation_with_title_and_document_ids(
    client: TestClient, mock_service: MagicMock
) -> None:
    """带 title 和 document_ids 创建：参数正确传递到 service。"""

    doc_id1 = uuid.uuid4()
    doc_id2 = uuid.uuid4()
    conv = make_conversation(
        title="论文方法追问",
        document_ids=[str(doc_id1), str(doc_id2)],
    )
    mock_service.create_conversation.return_value = conv

    response = client.post(
        "/api/v1/conversations",
        json={"title": "论文方法追问", "document_ids": [str(doc_id1), str(doc_id2)]},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "论文方法追问"
    assert body["document_ids"] == [str(doc_id1), str(doc_id2)]
    # messages 字段：未持久化实例的懒加载返回空列表（与默认创建测试一致）
    assert body["messages"] in (None, [])
    mock_service.create_conversation.assert_called_once_with(
        title="论文方法追问",
        document_ids=[doc_id1, doc_id2],
    )


# ---------------------------------------------------------------------------
# GET /api/v1/conversations —— 列表
# ---------------------------------------------------------------------------


def test_list_conversations_empty(client: TestClient, mock_service: MagicMock) -> None:
    """空列表：返回 200 + {"items": []}。"""

    mock_service.list_conversations.return_value = []

    response = client.get("/api/v1/conversations")

    assert response.status_code == 200
    assert response.json() == {"items": []}
    mock_service.list_conversations.assert_called_once_with()


def test_list_conversations_returns_all_without_messages(
    client: TestClient, mock_service: MagicMock
) -> None:
    """多条列表：返回 200 + items 数组，messages 字段为 None（节省体积）。"""

    conv1 = make_conversation(title="会话 A")
    conv2 = make_conversation(title="会话 B", document_ids=[str(uuid.uuid4())])
    mock_service.list_conversations.return_value = [conv1, conv2]

    response = client.get("/api/v1/conversations")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["title"] == "会话 A"
    assert items[0]["messages"] is None  # 列表场景不返回消息
    assert items[1]["title"] == "会话 B"
    assert items[1]["messages"] is None


# ---------------------------------------------------------------------------
# GET /api/v1/conversations/{id} —— 详情
# ---------------------------------------------------------------------------


def test_get_conversation_success_with_messages(
    client: TestClient, mock_service: MagicMock
) -> None:
    """详情成功：返回 200 + ConversationRead，含完整消息列表。"""

    conv_id = uuid.uuid4()
    msg1 = make_message(
        conversation_id=conv_id,
        role=MessageRole.USER,
        content="那篇论文的方法是什么？",
    )
    msg2 = make_message(
        conversation_id=conv_id,
        role=MessageRole.ASSISTANT,
        content="方法包括三个步骤 [C1]。",
        citations=[
            {
                "document_id": str(uuid.uuid4()),
                "document_name": "paper.pdf",
                "start_page": 3,
                "end_page": 3,
                "chunk_index": 2,
                "snippet": "原文片段。",
                "score": 0.91,
            }
        ],
    )
    conv = make_conversation(id=conv_id, title="论文方法追问")
    mock_service.get_conversation.return_value = conv
    mock_service.list_messages.return_value = [msg1, msg2]

    response = client.get(f"/api/v1/conversations/{conv_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(conv_id)
    assert body["title"] == "论文方法追问"
    assert body["messages"] is not None
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"] == "那篇论文的方法是什么？"
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["citations"] is not None
    assert len(body["messages"][1]["citations"]) == 1
    assert body["messages"][1]["citations"][0]["document_name"] == "paper.pdf"
    # 验证 service 被正确调用
    mock_service.get_conversation.assert_called_once_with(conv_id)
    mock_service.list_messages.assert_called_once_with(conv_id)


def test_get_conversation_not_found_returns_404(
    client: TestClient, mock_service: MagicMock
) -> None:
    """详情不存在：service 抛 ConversationNotFoundError → 404 + ErrorResponse。"""

    missing_id = uuid.uuid4()
    mock_service.get_conversation.side_effect = ConversationNotFoundError(
        f"会话不存在：{missing_id}"
    )

    response = client.get(f"/api/v1/conversations/{missing_id}")

    assert response.status_code == 404
    assert "会话不存在" in response.json()["detail"]
    # list_messages 不应被调用（get_conversation 已抛异常）
    mock_service.list_messages.assert_not_called()


def test_get_conversation_invalid_uuid_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """路径参数非合法 UUID：FastAPI 自动返回 422，不调 service。"""

    response = client.get("/api/v1/conversations/not-a-uuid")

    assert response.status_code == 422
    mock_service.get_conversation.assert_not_called()


# ---------------------------------------------------------------------------
# DELETE /api/v1/conversations/{id} —— 删除
# ---------------------------------------------------------------------------


def test_delete_conversation_success(client: TestClient, mock_service: MagicMock) -> None:
    """删除成功：返回 204 无响应体。"""

    conv_id = uuid.uuid4()

    response = client.delete(f"/api/v1/conversations/{conv_id}")

    assert response.status_code == 204
    assert response.content == b""
    mock_service.delete_conversation.assert_called_once_with(conv_id)


def test_delete_conversation_not_found_returns_404(
    client: TestClient, mock_service: MagicMock
) -> None:
    """删除不存在：service 抛 ConversationNotFoundError → 404 + ErrorResponse。"""

    missing_id = uuid.uuid4()
    mock_service.delete_conversation.side_effect = ConversationNotFoundError(
        f"会话不存在：{missing_id}"
    )

    response = client.delete(f"/api/v1/conversations/{missing_id}")

    assert response.status_code == 404
    assert "会话不存在" in response.json()["detail"]
    mock_service.delete_conversation.assert_called_once_with(missing_id)


def test_delete_conversation_invalid_uuid_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """路径参数非合法 UUID：FastAPI 自动返回 422，不调 service。"""

    response = client.delete("/api/v1/conversations/xyz")

    assert response.status_code == 422
    mock_service.delete_conversation.assert_not_called()


# ---------------------------------------------------------------------------
# GET /api/v1/conversations/{id}/messages —— 消息列表
# ---------------------------------------------------------------------------


def test_list_messages_success(client: TestClient, mock_service: MagicMock) -> None:
    """消息列表成功：返回 200 + MessageRead 数组。"""

    conv_id = uuid.uuid4()
    msg1 = make_message(conversation_id=conv_id, role=MessageRole.USER, content="问题 1")
    msg2 = make_message(
        conversation_id=conv_id,
        role=MessageRole.ASSISTANT,
        content="答案 1 [C1]。",
    )
    mock_service.list_messages.return_value = [msg1, msg2]

    response = client.get(f"/api/v1/conversations/{conv_id}/messages")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert body[0]["role"] == "user"
    assert body[0]["content"] == "问题 1"
    assert body[1]["role"] == "assistant"
    assert body[1]["citations"] is None  # assistant 消息未设置 citations 时为 None
    mock_service.list_messages.assert_called_once_with(conv_id)


def test_list_messages_response_includes_request_id(
    client: TestClient, mock_service: MagicMock
) -> None:
    """消息列表响应含 ``request_id``：assistant 消息返回 UUID 字符串，user 消息返回 null。

    验证 ADR 0003 的读出路径：``MessageRead`` schema 暴露 ``request_id`` 字段，
    ``GET /conversations/{id}/messages`` 响应自动携带（Pydantic 序列化）。
    旧消息（迁移前）与 user 消息的 ``request_id`` 为 null。
    """

    conv_id = uuid.uuid4()
    request_id = uuid.uuid4()
    # user 消息：request_id 未设置（默认 None）
    msg1 = make_message(conversation_id=conv_id, role=MessageRole.USER, content="问题")
    # assistant 消息：显式设置 request_id（模拟 #90 写入路径产出）
    msg2 = make_message(
        conversation_id=conv_id,
        role=MessageRole.ASSISTANT,
        content="答案 [C1]。",
        request_id=request_id,
    )
    # 旧消息（迁移前）：assistant 消息但 request_id 为 None
    msg3 = make_message(
        conversation_id=conv_id,
        role=MessageRole.ASSISTANT,
        content="旧答案 [C2]。",
        request_id=None,
    )
    mock_service.list_messages.return_value = [msg1, msg2, msg3]

    response = client.get(f"/api/v1/conversations/{conv_id}/messages")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    # user 消息 request_id 为 null
    assert body[0]["role"] == "user"
    assert body[0]["request_id"] is None
    # assistant 消息 request_id 与写入一致（UUID 字符串形式）
    assert body[1]["role"] == "assistant"
    assert body[1]["request_id"] == str(request_id)
    # 旧 assistant 消息 request_id 为 null（迁移前未持久化）
    assert body[2]["role"] == "assistant"
    assert body[2]["request_id"] is None


def test_list_messages_empty(client: TestClient, mock_service: MagicMock) -> None:
    """会话无消息：返回 200 + 空数组。"""

    conv_id = uuid.uuid4()
    mock_service.list_messages.return_value = []

    response = client.get(f"/api/v1/conversations/{conv_id}/messages")

    assert response.status_code == 200
    assert response.json() == []


def test_list_messages_conversation_not_found_returns_404(
    client: TestClient, mock_service: MagicMock
) -> None:
    """会话不存在：service 抛 ConversationNotFoundError → 404。"""

    missing_id = uuid.uuid4()
    mock_service.list_messages.side_effect = ConversationNotFoundError(f"会话不存在：{missing_id}")

    response = client.get(f"/api/v1/conversations/{missing_id}/messages")

    assert response.status_code == 404
    assert "会话不存在" in response.json()["detail"]


def test_list_messages_invalid_uuid_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """路径参数非合法 UUID：FastAPI 自动返回 422，不调 service。"""

    response = client.get("/api/v1/conversations/not-a-uuid/messages")

    assert response.status_code == 422
    mock_service.list_messages.assert_not_called()
