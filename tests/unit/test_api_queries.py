"""问答 API 路由单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.2 节、阶段 5 验收）：
- POST /api/v1/queries：问答成功（200）、证据不足（422）、LLM 异常（503）、
  Embedding 异常（503）、向量存储异常（500）、无可用文档（404）、
  文档不存在（404）、请求体校验失败（422）

测试策略（PROJECT_PLAN.md 第 13.2 节"测试中应 Mock 模型 API 和 Embedding 服务"）：
- 用 ``fastapi.testclient.TestClient``（基于 httpx，不起真实 uvicorn 服务）。
- 用 ``app.dependency_overrides[get_qa_service]`` 把 service 换成
  ``MagicMock(spec=QaService)``，完全跳过真实数据库、LLM 和 Embedding 调用。
- Mock service.answer 返回预构造的 ``QueryResponse``，验证路由层正确调
  service、正确映射异常到 HTTP 状态码。
- 用内存 SQLite ``session_factory`` 传入 ``create_app``，避免 ``lifespan``
  创建真实文件数据库（虽然 override 后 ``get_db`` 不被调用，但 lifespan 仍执行）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from research_rag.api.app import create_app
from research_rag.api.dependencies import get_qa_service
from research_rag.api.schemas import CitationRead, QueryResponse
from research_rag.db.models import DocumentNotFoundError
from research_rag.db.session import create_session_factory
from research_rag.embedding import DEFAULT_TOP_K, EmbeddingServiceError, VectorStoreError
from research_rag.qa_service import InsufficientEvidenceError, LlmServiceError
from research_rag.services.qa_service import (
    NoAvailableDocumentsError,
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
# 辅助：构造测试用 QueryResponse
# ---------------------------------------------------------------------------


def make_query_response(**overrides: object) -> QueryResponse:
    """构造一个 ``QueryResponse`` 实例，用于 Mock service 返回值。"""

    doc_id = uuid.uuid4()
    defaults: dict[str, object] = {
        "answer": "深度学习使用多层神经网络 [C1]。",
        "citations": [
            CitationRead(
                document_id=doc_id,
                document_name="paper.pdf",
                start_page=1,
                end_page=1,
                chunk_index=0,
                snippet="深度学习是机器学习的一个分支。",
                score=0.92,
            )
        ],
        "request_id": uuid.uuid4(),
        "elapsed_ms": 150,
    }
    defaults.update(overrides)
    return QueryResponse(**defaults)  # type: ignore[arg-type]


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
# POST /api/v1/queries —— 问答
# ---------------------------------------------------------------------------


def test_create_query_success(client: TestClient, mock_service: MagicMock) -> None:
    """问答成功：返回 200 + QueryResponse，service 收到正确参数。"""

    response_data = make_query_response()
    mock_service.answer.return_value = response_data

    payload = {"question": "深度学习是什么？"}
    response = client.post("/api/v1/queries", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == response_data.answer
    assert len(body["citations"]) == 1
    assert body["citations"][0]["document_name"] == "paper.pdf"
    assert body["citations"][0]["start_page"] == 1
    assert body["citations"][0]["end_page"] == 1
    assert body["citations"][0]["chunk_index"] == 0
    assert body["citations"][0]["score"] == pytest.approx(0.92)
    assert body["request_id"] == str(response_data.request_id)
    assert body["elapsed_ms"] == 150
    # 验证 service 被正确调用（top_k 默认从环境变量读，测试环境未设置时为 DEFAULT_TOP_K）
    mock_service.answer.assert_called_once_with(
        question="深度学习是什么？",
        document_ids=[],
        top_k=DEFAULT_TOP_K,
    )


def test_create_query_with_document_ids(client: TestClient, mock_service: MagicMock) -> None:
    """带 document_ids 的请求：service 收到 UUID 列表。"""

    mock_service.answer.return_value = make_query_response()
    doc_id = uuid.uuid4()

    payload = {"question": "问题", "document_ids": [str(doc_id)], "top_k": 3}
    response = client.post("/api/v1/queries", json=payload)

    assert response.status_code == 200
    mock_service.answer.assert_called_once_with(
        question="问题",
        document_ids=[doc_id],
        top_k=3,
    )


def test_create_query_insufficient_evidence_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """证据不足：service 抛 InsufficientEvidenceError → 422 + ErrorResponse。"""

    mock_service.answer.side_effect = InsufficientEvidenceError("上下文证据不足以回答该问题。")

    response = client.post("/api/v1/queries", json={"question": "无关问题"})

    assert response.status_code == 422
    assert "证据不足" in response.json()["detail"]


def test_create_query_llm_error_returns_503(client: TestClient, mock_service: MagicMock) -> None:
    """LLM 异常：service 抛 LlmServiceError → 503 + ErrorResponse。"""

    mock_service.answer.side_effect = LlmServiceError("调用大模型失败：超时")

    response = client.post("/api/v1/queries", json={"question": "问题"})

    assert response.status_code == 503
    assert "调用大模型失败" in response.json()["detail"]


def test_create_query_embedding_error_returns_503(
    client: TestClient, mock_service: MagicMock
) -> None:
    """Embedding 异常：service 抛 EmbeddingServiceError → 503 + ErrorResponse。"""

    mock_service.answer.side_effect = EmbeddingServiceError("加载 Embedding 模型失败")

    response = client.post("/api/v1/queries", json={"question": "问题"})

    assert response.status_code == 503
    assert "Embedding" in response.json()["detail"]


def test_create_query_vector_store_error_returns_500(
    client: TestClient, mock_service: MagicMock
) -> None:
    """向量存储异常：service 抛 VectorStoreError → 500 + ErrorResponse。"""

    mock_service.answer.side_effect = VectorStoreError("向量检索失败")

    response = client.post("/api/v1/queries", json={"question": "问题"})

    assert response.status_code == 500
    assert "向量检索失败" in response.json()["detail"]


def test_create_query_no_available_documents_returns_404(
    client: TestClient, mock_service: MagicMock
) -> None:
    """无可用文档：service 抛 NoAvailableDocumentsError → 404 + ErrorResponse。"""

    mock_service.answer.side_effect = NoAvailableDocumentsError("没有可用的 READY 文档可供问答。")

    response = client.post("/api/v1/queries", json={"question": "问题"})

    assert response.status_code == 404
    assert "READY" in response.json()["detail"]


def test_create_query_document_not_found_returns_404(
    client: TestClient, mock_service: MagicMock
) -> None:
    """文档不存在：service 抛 DocumentNotFoundError → 404 + ErrorResponse。"""

    missing_id = uuid.uuid4()
    mock_service.answer.side_effect = DocumentNotFoundError(f"文档不存在：{missing_id}")

    response = client.post(
        "/api/v1/queries",
        json={"question": "问题", "document_ids": [str(missing_id)]},
    )

    assert response.status_code == 404
    assert "文档不存在" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 请求体校验
# ---------------------------------------------------------------------------


def test_create_query_empty_question_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """question 为空字符串：Pydantic 校验失败 → 422，不调 service。"""

    response = client.post("/api/v1/queries", json={"question": ""})

    assert response.status_code == 422
    mock_service.answer.assert_not_called()


def test_create_query_missing_question_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """缺少 question 字段：Pydantic 校验失败 → 422，不调 service。"""

    response = client.post("/api/v1/queries", json={})

    assert response.status_code == 422
    mock_service.answer.assert_not_called()


def test_create_query_invalid_document_id_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """document_ids 中有非 UUID 字符串：Pydantic 校验失败 → 422，不调 service。"""

    response = client.post(
        "/api/v1/queries",
        json={"question": "问题", "document_ids": ["not-a-uuid"]},
    )

    assert response.status_code == 422
    mock_service.answer.assert_not_called()


def test_create_query_empty_body_returns_422(client: TestClient, mock_service: MagicMock) -> None:
    """空请求体：Pydantic 校验失败 → 422，不调 service。"""

    response = client.post("/api/v1/queries", json={})

    assert response.status_code == 422
    mock_service.answer.assert_not_called()


# ---------------------------------------------------------------------------
# POST /api/v1/queries —— 流式 SSE（阶段 9.1）
# ---------------------------------------------------------------------------


async def _stream_token_then_done() -> AsyncIterator[StreamEvent]:
    """构造正常流式事件序列：2 个 token + 1 个 done。"""
    yield StreamTokenEvent(text="Hello ")
    yield StreamTokenEvent(text="world [C1]。")
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
    )


def test_create_query_stream_returns_sse(client: TestClient, mock_service: MagicMock) -> None:
    """stream=true：返回 text/event-stream，含 token + done 事件。"""

    mock_service.answer_stream.return_value = _stream_token_then_done()

    response = client.post("/api/v1/queries", json={"question": "问题", "stream": True})

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert response.headers.get("cache-control") == "no-cache"

    body = response.text
    # SSE 事件格式：event: <name>\ndata: <json>\n\n
    assert "event: token" in body
    assert "event: done" in body
    assert "Hello " in body
    assert "world [C1]" in body
    assert "paper.pdf" in body  # done 事件中的引用元数据

    # 验证 answer_stream 被正确调用
    mock_service.answer_stream.assert_called_once_with(
        question="问题",
        document_ids=[],
        top_k=DEFAULT_TOP_K,
    )
    # 非流式路径不应被调用
    mock_service.answer.assert_not_called()


async def _stream_error_only() -> AsyncIterator[StreamEvent]:
    """构造错误流式事件序列：仅 1 个 error。"""
    yield StreamErrorEvent(detail="上下文证据不足以回答该问题。")


def test_create_query_stream_error_event(client: TestClient, mock_service: MagicMock) -> None:
    """stream=true + 检索异常：SSE 中含 error 事件，HTTP 仍为 200。"""

    mock_service.answer_stream.return_value = _stream_error_only()

    response = client.post("/api/v1/queries", json={"question": "问题", "stream": True})

    # SSE 已开始，HTTP 状态码仍为 200（错误在事件 data 中）
    assert response.status_code == 200
    body = response.text
    assert "event: error" in body
    assert "证据不足" in body


def test_create_query_stream_passes_document_ids_and_top_k(
    client: TestClient, mock_service: MagicMock
) -> None:
    """stream=true + document_ids + top_k：参数正确传递给 answer_stream。"""

    mock_service.answer_stream.return_value = _stream_token_then_done()
    doc_id = uuid.uuid4()

    response = client.post(
        "/api/v1/queries",
        json={"question": "问题", "stream": True, "document_ids": [str(doc_id)], "top_k": 3},
    )

    assert response.status_code == 200
    mock_service.answer_stream.assert_called_once_with(
        question="问题",
        document_ids=[doc_id],
        top_k=3,
    )


def test_create_query_default_stream_false_uses_answer(
    client: TestClient, mock_service: MagicMock
) -> None:
    """不传 stream（默认 false）：走非流式 ``answer`` 路径（回归测试）。"""

    mock_service.answer.return_value = make_query_response()

    response = client.post("/api/v1/queries", json={"question": "问题"})

    assert response.status_code == 200
    mock_service.answer.assert_called_once()
    mock_service.answer_stream.assert_not_called()
