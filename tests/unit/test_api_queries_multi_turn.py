"""问答 API 路由多轮对话测试（阶段 9.2）。

测试覆盖：
- POST /api/v1/queries 非流式：``conversation_id`` 透传给 ``QaService.answer``，
  响应含 ``conversation_id`` 字段
- POST /api/v1/queries 流式：``conversation_id`` 透传给 ``QaService.answer_stream``，
  SSE ``done`` 事件含 ``conversation_id`` 字段
- POST /api/v1/queries 会话不存在：service 抛 ``ConversationNotFoundError`` → 404
- POST /api/v1/queries 流式会话不存在：SSE ``error`` 事件（HTTP 200）
- 请求体 ``conversation_id`` 非合法 UUID：Pydantic 校验失败 → 422

测试策略（与 test_api_queries.py 一致）：
- ``MagicMock(spec=QaService)`` + ``app.dependency_overrides[get_qa_service]``
- 内存 SQLite factory 避免 lifespan 建文件数据库
- ``TestClient`` + ``with`` 触发 lifespan
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from research_rag.api.app import create_app
from research_rag.api.dependencies import get_qa_service
from research_rag.api.schemas import CitationRead, QueryResponse
from research_rag.db.session import create_session_factory
from research_rag.embedding import DEFAULT_TOP_K
from research_rag.services.qa_service import (
    ConversationNotFoundError,
    QaService,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamTokenEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi import FastAPI

    from research_rag.services.qa_service import StreamEvent


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def make_query_response(**overrides: object) -> QueryResponse:
    """构造一个带 ``conversation_id`` 的 ``QueryResponse``。"""

    doc_id = uuid.uuid4()
    defaults: dict[str, object] = {
        "answer": "答案 [C1]。",
        "citations": [
            CitationRead(
                document_id=doc_id,
                document_name="paper.pdf",
                start_page=1,
                end_page=1,
                chunk_index=0,
                snippet="原文片段。",
                score=0.9,
            )
        ],
        "request_id": uuid.uuid4(),
        "elapsed_ms": 100,
        "conversation_id": uuid.uuid4(),
    }
    defaults.update(overrides)
    return QueryResponse(**defaults)  # type: ignore[arg-type]


async def _stream_with_conversation_id(
    conv_id: uuid.UUID,
) -> AsyncIterator[StreamEvent]:
    """构造正常流式事件序列，done 携带 ``conversation_id``。"""
    yield StreamTokenEvent(text="答案 ")
    yield StreamTokenEvent(text="[C1]。")
    yield StreamDoneEvent(
        citations=[
            CitationRead(
                document_id=uuid.uuid4(),
                document_name="paper.pdf",
                start_page=1,
                end_page=1,
                chunk_index=0,
                snippet="原文片段。",
                score=0.9,
            )
        ],
        request_id=uuid.uuid4(),
        elapsed_ms=120,
        conversation_id=conv_id,
    )


async def _stream_error_conversation_not_found() -> AsyncIterator[StreamEvent]:
    """构造会话不存在的错误流式事件序列。"""
    yield StreamErrorEvent(detail="会话不存在：xxx")


# ---------------------------------------------------------------------------
# Fixtures（与 test_api_queries.py 一致）
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
# 非流式：conversation_id 透传
# ---------------------------------------------------------------------------


def test_answer_passes_conversation_id_to_service(
    client: TestClient, mock_service: MagicMock
) -> None:
    """非流式：``conversation_id`` 透传给 ``QaService.answer``。"""

    conv_id = uuid.uuid4()
    response_data = make_query_response(conversation_id=conv_id)
    mock_service.answer.return_value = response_data

    response = client.post(
        "/api/v1/queries",
        json={"question": "那篇论文的方法再详细说说", "conversation_id": str(conv_id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == str(conv_id)
    mock_service.answer.assert_called_once_with(
        question="那篇论文的方法再详细说说",
        document_ids=[],
        top_k=DEFAULT_TOP_K,
        conversation_id=conv_id,
    )


def test_answer_without_conversation_id_passes_none(
    client: TestClient, mock_service: MagicMock
) -> None:
    """非流式：未传 ``conversation_id`` 时透传 None（回归测试，确保默认值仍为 None）。"""

    mock_service.answer.return_value = make_query_response(conversation_id=None)

    response = client.post("/api/v1/queries", json={"question": "单轮问题"})

    assert response.status_code == 200
    assert response.json()["conversation_id"] is None
    mock_service.answer.assert_called_once_with(
        question="单轮问题",
        document_ids=[],
        top_k=DEFAULT_TOP_K,
        conversation_id=None,
    )


def test_answer_conversation_not_found_returns_404(
    client: TestClient, mock_service: MagicMock
) -> None:
    """非流式：会话不存在 → service 抛 ``ConversationNotFoundError`` → 404。"""

    conv_id = uuid.uuid4()
    mock_service.answer.side_effect = ConversationNotFoundError(f"会话不存在：{conv_id}")

    response = client.post(
        "/api/v1/queries",
        json={"question": "继续追问", "conversation_id": str(conv_id)},
    )

    assert response.status_code == 404
    assert "会话不存在" in response.json()["detail"]


def test_answer_invalid_conversation_id_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """非流式：``conversation_id`` 非合法 UUID → Pydantic 校验失败 → 422。"""

    response = client.post(
        "/api/v1/queries",
        json={"question": "问题", "conversation_id": "not-a-uuid"},
    )

    assert response.status_code == 422
    mock_service.answer.assert_not_called()


def test_answer_with_conversation_id_and_document_ids(
    client: TestClient, mock_service: MagicMock
) -> None:
    """非流式：``conversation_id`` + ``document_ids`` + ``top_k`` 同时传递。

    会话级 ``document_ids`` 锁定由 service 层处理，路由只负责透传。
    """

    conv_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    mock_service.answer.return_value = make_query_response(conversation_id=conv_id)

    response = client.post(
        "/api/v1/queries",
        json={
            "question": "追问",
            "conversation_id": str(conv_id),
            "document_ids": [str(doc_id)],
            "top_k": 3,
        },
    )

    assert response.status_code == 200
    mock_service.answer.assert_called_once_with(
        question="追问",
        document_ids=[doc_id],
        top_k=3,
        conversation_id=conv_id,
    )


# ---------------------------------------------------------------------------
# 流式：conversation_id 透传
# ---------------------------------------------------------------------------


def test_stream_passes_conversation_id_to_service(
    client: TestClient, mock_service: MagicMock
) -> None:
    """流式：``conversation_id`` 透传给 ``QaService.answer_stream``。"""

    conv_id = uuid.uuid4()
    mock_service.answer_stream.return_value = _stream_with_conversation_id(conv_id)

    response = client.post(
        "/api/v1/queries",
        json={"question": "那篇论文的方法", "stream": True, "conversation_id": str(conv_id)},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    body = response.text
    # done 事件含 conversation_id
    assert "event: done" in body
    assert str(conv_id) in body

    mock_service.answer_stream.assert_called_once_with(
        question="那篇论文的方法",
        document_ids=[],
        top_k=DEFAULT_TOP_K,
        conversation_id=conv_id,
    )
    mock_service.answer.assert_not_called()


def test_stream_done_event_has_conversation_id_none_when_not_set(
    client: TestClient, mock_service: MagicMock
) -> None:
    """流式：未传 ``conversation_id`` 时 done 事件 ``conversation_id`` 为 null。"""

    mock_service.answer_stream.return_value = _stream_with_conversation_id(None)

    response = client.post("/api/v1/queries", json={"question": "单轮", "stream": True})

    assert response.status_code == 200
    body = response.text
    # 解析 done 事件 data
    assert "event: done" in body
    # 提取 done 事件的 data JSON
    done_data = _extract_done_data(body)
    assert done_data is not None
    assert done_data["conversation_id"] is None


def test_stream_conversation_not_found_emits_error_event(
    client: TestClient, mock_service: MagicMock
) -> None:
    """流式：会话不存在 → SSE ``error`` 事件，HTTP 仍为 200。"""

    conv_id = uuid.uuid4()
    mock_service.answer_stream.return_value = _stream_error_conversation_not_found()

    response = client.post(
        "/api/v1/queries",
        json={"question": "追问", "stream": True, "conversation_id": str(conv_id)},
    )

    # SSE 已开始，HTTP 状态码仍为 200
    assert response.status_code == 200
    body = response.text
    assert "event: error" in body
    assert "会话不存在" in body


def test_stream_invalid_conversation_id_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """流式：``conversation_id`` 非合法 UUID → 422（请求体校验先于路由逻辑）。"""

    response = client.post(
        "/api/v1/queries",
        json={"question": "问题", "stream": True, "conversation_id": "not-a-uuid"},
    )

    assert response.status_code == 422
    mock_service.answer_stream.assert_not_called()


def test_stream_conversation_id_with_document_ids_and_top_k(
    client: TestClient, mock_service: MagicMock
) -> None:
    """流式：``conversation_id`` + ``document_ids`` + ``top_k`` 同时传递。"""

    conv_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    mock_service.answer_stream.return_value = _stream_with_conversation_id(conv_id)

    response = client.post(
        "/api/v1/queries",
        json={
            "question": "追问",
            "stream": True,
            "conversation_id": str(conv_id),
            "document_ids": [str(doc_id)],
            "top_k": 5,
        },
    )

    assert response.status_code == 200
    mock_service.answer_stream.assert_called_once_with(
        question="追问",
        document_ids=[doc_id],
        top_k=5,
        conversation_id=conv_id,
    )


# ---------------------------------------------------------------------------
# 辅助：从 SSE 文本中提取 done 事件的 data JSON
# ---------------------------------------------------------------------------


def _extract_done_data(body: str) -> dict[str, object] | None:
    """从 SSE 文本中提取首个 ``done`` 事件的 data JSON。"""

    lines = body.split("\n")
    in_done = False
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("event:"):
            in_done = line[len("event:") :].strip() == "done"
        elif line.startswith("data:") and in_done:
            data_lines.append(line[len("data:") :].strip())
        elif line == "" and in_done and data_lines:
            break
    if not data_lines:
        return None
    return json.loads("\n".join(data_lines))
