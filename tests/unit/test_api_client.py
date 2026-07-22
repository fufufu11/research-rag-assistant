"""API 客户端（``ApiClient``）单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.2 节、阶段 6 验收）：
- ``upload_document``：成功（201）、重复上传（409）
- ``list_documents``：成功（含列表 / 空列表）
- ``get_document``：成功、不存在（404）
- ``delete_document``：成功（204）、不存在（404）
- ``ask_question``：成功（带引用）、证据不足（422）
- 网络错误（连接拒绝）：包装为 ``ApiClientError``（status_code=0）
- ``base_url`` 从环境变量 ``API_BASE_URL`` 读取

测试策略：
- 用 ``unittest.mock.patch`` 替换 ``requests.request``，返回预构造的
  ``Mock`` 响应对象（``status_code`` / ``json()`` / ``ok`` / ``text``），
  完全跳过真实 HTTP 调用，CI 不依赖网络。
- 验证请求 URL / method / 参数正确，以及响应正确解析为 dataclass。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from research_rag.ui.api_client import (
    DEFAULT_API_BASE_URL,
    ApiClient,
    ApiClientError,
    Citation,
    DocumentInfo,
    QueryResult,
)

# ---------------------------------------------------------------------------
# 辅助：构造 Mock 响应
# ---------------------------------------------------------------------------


def make_response(
    status_code: int = 200,
    json_data: object | None = None,
    text: str = "",
) -> MagicMock:
    """构造一个模拟 ``requests.Response`` 的 Mock 对象。"""

    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.side_effect = ValueError("no json")
    return response


def make_doc_dict(**overrides: object) -> dict[str, object]:
    """构造一个 ``DocumentRead`` 格式的 dict。"""

    base: dict[str, object] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "original_name": "test.pdf",
        "stored_name": "11111111.pdf",
        "sha256": "abc123",
        "page_count": 10,
        "status": "ready",
        "error_message": None,
        "created_at": "2026-07-22T10:00:00",
        "updated_at": "2026-07-22T10:00:00",
    }
    base.update(overrides)
    return base


def make_citation_dict(**overrides: object) -> dict[str, object]:
    """构造一个 ``CitationRead`` 格式的 dict。"""

    base: dict[str, object] = {
        "document_id": "11111111-1111-1111-1111-111111111111",
        "document_name": "test.pdf",
        "page_number": 1,
        "chunk_index": 0,
        "snippet": "这是原文片段内容。",
        "score": 0.85,
    }
    base.update(overrides)
    return base


def make_query_dict(**overrides: object) -> dict[str, object]:
    """构造一个 ``QueryResponse`` 格式的 dict。"""

    base: dict[str, object] = {
        "answer": "这是答案 [C1]。",
        "citations": [make_citation_dict()],
        "request_id": "22222222-2222-2222-2222-222222222222",
        "elapsed_ms": 500,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# ApiClient 初始化
# ---------------------------------------------------------------------------


class TestApiClientInit:
    def test_default_base_url(self) -> None:
        client = ApiClient()
        assert client.base_url == DEFAULT_API_BASE_URL

    def test_custom_base_url(self) -> None:
        client = ApiClient(base_url="http://example.com/api/v2/")
        assert client.base_url == "http://example.com/api/v2"

    def test_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_BASE_URL", "http://env-host:9000/api/v1")
        client = ApiClient()
        assert client.base_url == "http://env-host:9000/api/v1"

    def test_explicit_base_url_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_BASE_URL", "http://env-host:9000/api/v1")
        client = ApiClient(base_url="http://explicit-host/api/v1")
        assert client.base_url == "http://explicit-host/api/v1"


# ---------------------------------------------------------------------------
# upload_document
# ---------------------------------------------------------------------------


class TestUploadDocument:
    @patch("research_rag.ui.api_client.requests.request")
    def test_success(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(201, make_doc_dict(original_name="paper.pdf"))

        client = ApiClient()
        doc = client.upload_document(b"fake pdf bytes", "paper.pdf")

        assert isinstance(doc, DocumentInfo)
        assert doc.original_name == "paper.pdf"
        assert doc.status == "ready"
        assert doc.page_count == 10

        # 验证请求参数
        call_kwargs = mock_request.call_args.kwargs
        assert mock_request.call_args.kwargs["method"] == "POST"
        assert call_kwargs["url"] == f"{DEFAULT_API_BASE_URL}/documents"
        assert call_kwargs["files"] == ("file", ("paper.pdf", b"fake pdf bytes", "application/pdf"))

    @patch("research_rag.ui.api_client.requests.request")
    def test_duplicate_returns_409(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(409, {"detail": "文档已存在"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.upload_document(b"bytes", "dup.pdf")

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail == "文档已存在"


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------


class TestListDocuments:
    @patch("research_rag.ui.api_client.requests.request")
    def test_returns_list(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(
            200,
            {"items": [make_doc_dict(), make_doc_dict(original_name="second.pdf")]},
        )

        client = ApiClient()
        docs = client.list_documents()

        assert len(docs) == 2
        assert docs[0].original_name == "test.pdf"
        assert docs[1].original_name == "second.pdf"
        assert mock_request.call_args.kwargs["method"] == "GET"

    @patch("research_rag.ui.api_client.requests.request")
    def test_empty_list(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(200, {"items": []})

        client = ApiClient()
        docs = client.list_documents()
        assert docs == []


# ---------------------------------------------------------------------------
# get_document
# ---------------------------------------------------------------------------


class TestGetDocument:
    @patch("research_rag.ui.api_client.requests.request")
    def test_success(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(200, make_doc_dict(page_count=42))

        client = ApiClient()
        doc = client.get_document("11111111-1111-1111-1111-111111111111")

        assert doc.page_count == 42
        assert mock_request.call_args.kwargs["method"] == "GET"

    @patch("research_rag.ui.api_client.requests.request")
    def test_not_found_404(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(404, {"detail": "文档不存在"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.get_document("nonexistent-id")

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# delete_document
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    @patch("research_rag.ui.api_client.requests.request")
    def test_success(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(204, None, text="")

        client = ApiClient()
        # 不抛异常即成功
        client.delete_document("11111111-1111-1111-1111-111111111111")

        assert mock_request.call_args.kwargs["method"] == "DELETE"

    @patch("research_rag.ui.api_client.requests.request")
    def test_not_found_404(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(404, {"detail": "文档不存在"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.delete_document("nonexistent-id")

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# ask_question
# ---------------------------------------------------------------------------


class TestAskQuestion:
    @patch("research_rag.ui.api_client.requests.request")
    def test_success_with_citations(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(200, make_query_dict())

        client = ApiClient()
        result = client.ask_question("论文主题是什么？")

        assert isinstance(result, QueryResult)
        assert result.answer == "这是答案 [C1]。"
        assert result.elapsed_ms == 500
        assert len(result.citations) == 1

        cite = result.citations[0]
        assert isinstance(cite, Citation)
        assert cite.document_name == "test.pdf"
        assert cite.page_number == 1
        assert cite.snippet == "这是原文片段内容。"
        assert cite.score == pytest.approx(0.85)

        # 验证请求 body
        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["json"] == {"question": "论文主题是什么？"}

    @patch("research_rag.ui.api_client.requests.request")
    def test_with_document_ids_and_top_k(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(200, make_query_dict())

        client = ApiClient()
        client.ask_question(
            "问题",
            document_ids=["doc-1", "doc-2"],
            top_k=5,
        )

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["json"] == {
            "question": "问题",
            "document_ids": ["doc-1", "doc-2"],
            "top_k": 5,
        }

    @patch("research_rag.ui.api_client.requests.request")
    def test_insufficient_evidence_422(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(422, {"detail": "证据不足，无法回答"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.ask_question("无法回答的问题")

        assert exc_info.value.status_code == 422
        assert "证据不足" in exc_info.value.detail

    @patch("research_rag.ui.api_client.requests.request")
    def test_no_citations(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(
            200,
            make_query_dict(citations=[], answer="无引用答案。"),
        )

        client = ApiClient()
        result = client.ask_question("问题")

        assert result.citations == []
        assert result.answer == "无引用答案。"


# ---------------------------------------------------------------------------
# 网络错误
# ---------------------------------------------------------------------------


class TestNetworkError:
    @patch("research_rag.ui.api_client.requests.request")
    def test_connection_error_wrapped(self, mock_request: MagicMock) -> None:
        mock_request.side_effect = requests.ConnectionError("Connection refused")

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.list_documents()

        assert exc_info.value.status_code == 0
        assert "无法连接" in exc_info.value.detail

    @patch("research_rag.ui.api_client.requests.request")
    def test_timeout_wrapped(self, mock_request: MagicMock) -> None:
        mock_request.side_effect = requests.Timeout("read timeout")

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.ask_question("问题")

        assert exc_info.value.status_code == 0


# ---------------------------------------------------------------------------
# 错误响应 detail 解析
# ---------------------------------------------------------------------------


class TestErrorDetailExtraction:
    @patch("research_rag.ui.api_client.requests.request")
    def test_non_json_error_body(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(500, None, text="Internal Server Error")

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.list_documents()

        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal Server Error"

    @patch("research_rag.ui.api_client.requests.request")
    def test_json_error_without_detail_key(self, mock_request: MagicMock) -> None:
        mock_request.return_value = make_response(500, {"error": "something"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.list_documents()

        assert exc_info.value.status_code == 500
