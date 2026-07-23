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

import json
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
    StreamDone,
    StreamError,
    StreamToken,
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
        "start_page": 1,
        "end_page": 1,
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


def make_sse_lines(events: list[tuple[str, str]]) -> list[str]:
    """从 ``[(event_name, data_json), ...]`` 构造 SSE 行列表。

    每个事件生成 ``event: <name>`` + ``data: <json>`` + 空行（事件分隔）。
    ``iter_lines`` 逐行产出，空行触发 ``_parse_sse_stream`` 的事件结束逻辑。
    """

    lines: list[str] = []
    for event_name, data_json in events:
        lines.append(f"event: {event_name}")
        lines.append(f"data: {data_json}")
        lines.append("")  # 空行：事件分隔
    return lines


def make_sse_response(
    lines: list[str],
    status_code: int = 200,
    json_data: object | None = None,
) -> MagicMock:
    """构造模拟 SSE 流式响应的 Mock 对象。

    ``iter_lines`` 返回预设行列表（模拟 ``decode_unicode=True`` 的 str 行）。
    非 2xx 时设置 ``json()`` 返回错误 detail（供 ``_extract_detail`` 解析）。
    """

    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.iter_lines.return_value = lines
    if json_data is not None:
        response.json.return_value = json_data
    elif not response.ok:
        response.json.return_value = {"detail": "服务端错误"}
        response.text = '{"detail": "服务端错误"}'
    return response


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
        assert call_kwargs["files"] == {"file": ("paper.pdf", b"fake pdf bytes", "application/pdf")}

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
        assert cite.start_page == 1
        assert cite.end_page == 1
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


# ---------------------------------------------------------------------------
# ask_question_stream（阶段 9.1 SSE）
# ---------------------------------------------------------------------------


class TestAskQuestionStream:
    @patch("research_rag.ui.api_client.requests.post")
    def test_parses_token_and_done_events(self, mock_post: MagicMock) -> None:
        """正常 SSE：token 事件产出 StreamToken，done 事件产出 StreamDone（含引用）。"""

        cite_dict = make_citation_dict()
        sse_lines = make_sse_lines(
            [
                ("token", json.dumps({"text": "Hello "}, ensure_ascii=False)),
                ("token", json.dumps({"text": "world [C1]"}, ensure_ascii=False)),
                (
                    "done",
                    json.dumps(
                        {
                            "citations": [cite_dict],
                            "request_id": "33333333-3333-3333-3333-333333333333",
                            "elapsed_ms": 200,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        events = list(client.ask_question_stream("问题"))

        # 2 个 token + 1 个 done
        tokens = [e for e in events if isinstance(e, StreamToken)]
        dones = [e for e in events if isinstance(e, StreamDone)]
        assert len(tokens) == 2
        assert tokens[0].text == "Hello "
        assert tokens[1].text == "world [C1]"
        assert len(dones) == 1
        assert dones[0].elapsed_ms == 200
        assert dones[0].request_id == "33333333-3333-3333-3333-333333333333"
        assert len(dones[0].citations) == 1
        cite = dones[0].citations[0]
        assert isinstance(cite, Citation)
        assert cite.document_name == "test.pdf"
        assert cite.start_page == 1
        assert cite.score == pytest.approx(0.85)

        # 验证请求参数
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["url"] == f"{DEFAULT_API_BASE_URL}/queries"
        assert call_kwargs["json"] == {"question": "问题", "stream": True}
        assert call_kwargs["stream"] is True

    @patch("research_rag.ui.api_client.requests.post")
    def test_parses_error_event(self, mock_post: MagicMock) -> None:
        """SSE error 事件：产出 StreamError，不抛异常。"""

        sse_lines = make_sse_lines(
            [
                ("error", json.dumps({"detail": "上下文证据不足"}, ensure_ascii=False)),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        events = list(client.ask_question_stream("问题"))

        errors = [e for e in events if isinstance(e, StreamError)]
        assert len(errors) == 1
        assert "证据不足" in errors[0].detail

    @patch("research_rag.ui.api_client.requests.post")
    def test_http_error_raises_api_client_error(self, mock_post: MagicMock) -> None:
        """HTTP 非 2xx：抛 ApiClientError（含 status_code 和 detail）。"""

        mock_post.return_value = make_sse_response([], status_code=422)

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            list(client.ask_question_stream("问题"))

        assert exc_info.value.status_code == 422
        assert "服务端错误" in exc_info.value.detail

    @patch("research_rag.ui.api_client.requests.post")
    def test_network_error_wrapped(self, mock_post: MagicMock) -> None:
        """网络错误：包装为 ApiClientError（status_code=0）。"""

        mock_post.side_effect = requests.ConnectionError("Connection refused")

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            list(client.ask_question_stream("问题"))

        assert exc_info.value.status_code == 0
        assert "无法连接" in exc_info.value.detail

    @patch("research_rag.ui.api_client.requests.post")
    def test_passes_document_ids_and_top_k(self, mock_post: MagicMock) -> None:
        """document_ids 和 top_k 正确传入请求体。"""

        sse_lines = make_sse_lines(
            [
                (
                    "done",
                    json.dumps(
                        {
                            "citations": [],
                            "request_id": "44444444-4444-4444-4444-444444444444",
                            "elapsed_ms": 50,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        list(client.ask_question_stream("问题", document_ids=["doc-1", "doc-2"], top_k=5))

        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"] == {
            "question": "问题",
            "stream": True,
            "document_ids": ["doc-1", "doc-2"],
            "top_k": 5,
        }

    @patch("research_rag.ui.api_client.requests.post")
    def test_no_trailing_empty_line_still_parses(self, mock_post: MagicMock) -> None:
        """流末尾无空行（容错）：最后一个事件仍能被解析。"""

        sse_lines = [
            "event: token",
            f"data: {json.dumps({'text': 'Hi'}, ensure_ascii=False)}",
            "event: done",
            f"data: {json.dumps({'citations': [], 'request_id': 'x', 'elapsed_ms': 1}, ensure_ascii=False)}",
            # 无末尾空行（容错：流结束时仍有未发事件）
        ]
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        events = list(client.ask_question_stream("问题"))

        tokens = [e for e in events if isinstance(e, StreamToken)]
        dones = [e for e in events if isinstance(e, StreamDone)]
        assert len(tokens) == 1
        assert tokens[0].text == "Hi"
        assert len(dones) == 1
