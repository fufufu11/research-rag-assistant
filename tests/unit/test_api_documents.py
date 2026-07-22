"""文档管理 API 路由单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.2 节、阶段 5 验收）：
- POST /api/v1/documents：上传成功（201）、重复上传（409）
- GET /api/v1/documents：列表（200）
- GET /api/v1/documents/{doc_id}：详情成功（200）、不存在（404）
- DELETE /api/v1/documents/{doc_id}：删除成功（204）、不存在（404）

测试策略（PROJECT_PLAN.md 第 13.2 节"测试中应 Mock 模型 API 和 Embedding 服务"）：
- 用 ``fastapi.testclient.TestClient``（基于 httpx，不起真实 uvicorn 服务）。
- 用 ``app.dependency_overrides[get_document_service]`` 把 service 换成
  ``MagicMock(spec=DocumentService)``，完全跳过真实数据库、文件 IO 和 PDF 解析。
- Mock service 方法返回预构造的 ``Document`` ORM 实例（``Document(...)`` 直接
  构造，不持久化），验证路由层正确调 service、正确转 ``DocumentRead``、
  正确映射异常到 HTTP 状态码。
- 用内存 SQLite ``session_factory`` 传入 ``create_app``，避免 ``lifespan``
  创建真实文件数据库（虽然 override 后 ``get_db`` 不被调用，但 lifespan 仍执行）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from research_rag.api.app import create_app
from research_rag.api.dependencies import get_document_service
from research_rag.db.models import (
    Document,
    DocumentNotFoundError,
    DocumentStatus,
    DuplicateDocumentError,
)
from research_rag.db.session import create_session_factory
from research_rag.services.document_service import DocumentService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI


# ---------------------------------------------------------------------------
# 辅助：构造测试用 Document ORM 实例（不持久化）
# ---------------------------------------------------------------------------


def make_document(**overrides: object) -> Document:
    """构造一个 ``Document`` ORM 实例，不落库。

    默认值代表一个处理完成的文档（status=READY）。``id`` / ``created_at``
    / ``updated_at`` 显式提供，因为 ORM 的 ``default`` / ``onupdate`` 回调
    只在 ``flush`` 时触发，未持久化的实例这些属性为 ``None``，会导致
    ``DocumentRead.model_validate(doc)`` 时 Pydantic 校验失败
    （``uuid_type`` / ``datetime_type``）。
    """

    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "original_name": "paper.pdf",
        "stored_name": "abc123def456abcd.pdf",
        "sha256": "a" * 64,
        "page_count": 3,
        "status": DocumentStatus.READY,
        "error_message": None,
        "created_at": datetime.now(UTC).replace(tzinfo=None),
        "updated_at": datetime.now(UTC).replace(tzinfo=None),
    }
    defaults.update(overrides)
    return Document(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_service() -> MagicMock:
    """``MagicMock(spec=DocumentService)``：限定只能调 DocumentService 的方法。

    ``spec`` 让 ``mock.upload_document`` 合法、``mock.nonexistent`` 抛 AttributeError，
    既能断言调用参数，又能防止测试误调用不存在的方法。
    """

    return MagicMock(spec=DocumentService)


@pytest.fixture
def app(mock_service: MagicMock) -> FastAPI:
    """创建应用实例：注入内存 SQLite factory，override service 依赖。

    - 传 ``session_factory`` 避免 lifespan 创建真实文件数据库（默认
      ``sqlite:///./data/app.db`` 会在项目目录建文件）。
    - ``app.dependency_overrides[get_document_service]`` 把真实 service 构造
      替换为返回 ``mock_service``，使 ``get_db`` / ``get_session_factory``
      都不被调用，测试与真实数据库完全解耦。
    """

    app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
    app.dependency_overrides[get_document_service] = lambda: mock_service
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """``TestClient``：基于 httpx 的 ASGI 测试客户端。

    用 ``with`` 触发 ``lifespan``（启动建 engine/factory，关闭 dispose）。
    不 ``with`` 则 lifespan 不执行，``app.state.session_factory`` 可能未初始化。
    """

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# POST /api/v1/documents —— 上传
# ---------------------------------------------------------------------------


def test_upload_document_success(client: TestClient, mock_service: MagicMock) -> None:
    """上传成功：返回 201 + DocumentRead，service 收到文件字节和文件名。"""

    doc = make_document(original_name="thesis.pdf", page_count=12)
    mock_service.upload_document.return_value = doc

    response = client.post(
        "/api/v1/documents",
        files={"file": ("thesis.pdf", b"%PDF-1.4 fake content", "application/pdf")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["original_name"] == "thesis.pdf"
    assert body["page_count"] == 12
    assert body["status"] == "ready"  # DocumentStatus.READY.value
    assert body["sha256"] == "a" * 64
    assert body["id"] == str(doc.id)
    assert body["error_message"] is None
    # 验证 service 被正确调用：收到文件字节和原始文件名
    mock_service.upload_document.assert_called_once_with(b"%PDF-1.4 fake content", "thesis.pdf")


def test_upload_document_duplicate_returns_409(client: TestClient, mock_service: MagicMock) -> None:
    """重复上传：service 抛 DuplicateDocumentError → 409 + ErrorResponse。"""

    mock_service.upload_document.side_effect = DuplicateDocumentError("文档已存在：paper.pdf")

    response = client.post(
        "/api/v1/documents",
        files={"file": ("paper.pdf", b"content", "application/pdf")},
    )

    assert response.status_code == 409
    assert "文档已存在" in response.json()["detail"]
    mock_service.upload_document.assert_called_once()


# ---------------------------------------------------------------------------
# GET /api/v1/documents —— 列表
# ---------------------------------------------------------------------------


def test_list_documents_empty(client: TestClient, mock_service: MagicMock) -> None:
    """空列表：返回 200 + {"items": []}。"""

    mock_service.list_documents.return_value = []

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    assert response.json() == {"items": []}
    mock_service.list_documents.assert_called_once_with()


def test_list_documents_returns_all(client: TestClient, mock_service: MagicMock) -> None:
    """多条列表：返回 200 + items 数组，顺序与 service 返回一致。"""

    doc1 = make_document(original_name="a.pdf")
    doc2 = make_document(original_name="b.pdf", page_count=5)
    mock_service.list_documents.return_value = [doc1, doc2]

    response = client.get("/api/v1/documents")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert items[0]["original_name"] == "a.pdf"
    assert items[1]["original_name"] == "b.pdf"
    assert items[1]["page_count"] == 5


# ---------------------------------------------------------------------------
# GET /api/v1/documents/{doc_id} —— 详情
# ---------------------------------------------------------------------------


def test_get_document_success(client: TestClient, mock_service: MagicMock) -> None:
    """详情成功：返回 200 + DocumentRead，service 收到 UUID 参数。"""

    doc = make_document(original_name="report.pdf", page_count=7)
    mock_service.get_document.return_value = doc

    response = client.get(f"/api/v1/documents/{doc.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["original_name"] == "report.pdf"
    assert body["page_count"] == 7
    assert body["id"] == str(doc.id)
    mock_service.get_document.assert_called_once_with(doc.id)


def test_get_document_not_found_returns_404(client: TestClient, mock_service: MagicMock) -> None:
    """详情不存在：service 抛 DocumentNotFoundError → 404 + ErrorResponse。"""

    missing_id = uuid.uuid4()
    mock_service.get_document.side_effect = DocumentNotFoundError(f"文档不存在：{missing_id}")

    response = client.get(f"/api/v1/documents/{missing_id}")

    assert response.status_code == 404
    assert "文档不存在" in response.json()["detail"]
    mock_service.get_document.assert_called_once_with(missing_id)


def test_get_document_invalid_uuid_returns_422(client: TestClient, mock_service: MagicMock) -> None:
    """路径参数非合法 UUID：FastAPI 自动返回 422，不调 service。"""

    response = client.get("/api/v1/documents/not-a-uuid")

    assert response.status_code == 422
    mock_service.get_document.assert_not_called()


# ---------------------------------------------------------------------------
# DELETE /api/v1/documents/{doc_id} —— 删除
# ---------------------------------------------------------------------------


def test_delete_document_success(client: TestClient, mock_service: MagicMock) -> None:
    """删除成功：返回 204 无响应体，service 收到 UUID。"""

    doc_id = uuid.uuid4()

    response = client.delete(f"/api/v1/documents/{doc_id}")

    assert response.status_code == 204
    assert response.content == b""  # 无响应体
    mock_service.delete_document.assert_called_once_with(doc_id)


def test_delete_document_not_found_returns_404(client: TestClient, mock_service: MagicMock) -> None:
    """删除不存在：service 抛 DocumentNotFoundError → 404 + ErrorResponse。"""

    missing_id = uuid.uuid4()
    mock_service.delete_document.side_effect = DocumentNotFoundError(f"文档不存在：{missing_id}")

    response = client.delete(f"/api/v1/documents/{missing_id}")

    assert response.status_code == 404
    assert "文档不存在" in response.json()["detail"]
    mock_service.delete_document.assert_called_once_with(missing_id)


def test_delete_document_invalid_uuid_returns_422(
    client: TestClient, mock_service: MagicMock
) -> None:
    """路径参数非合法 UUID：FastAPI 自动返回 422，不调 service。"""

    response = client.delete("/api/v1/documents/xyz")

    assert response.status_code == 422
    mock_service.delete_document.assert_not_called()
