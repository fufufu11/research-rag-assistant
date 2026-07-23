"""ApiClient 多轮对话测试（阶段 9.2）。

测试覆盖：
- ``create_conversation``：默认创建 / 带 title+document_ids / 网络错误
- ``list_conversations``：列表（含/空）/ 非法响应
- ``get_conversation``：详情（含 messages）/ 404
- ``delete_conversation``：成功 / 404
- ``list_messages``：消息列表 / 空列表 / 404
- ``ask_question`` with ``conversation_id``：参数透传 + 响应解析 conversation_id
- ``ask_question_stream`` with ``conversation_id``：参数透传 + done 事件解析 conversation_id
- ``StreamDone.conversation_id``：None（单轮）/ 非 None（多轮）解析
- ``QueryResult.conversation_id``：响应 JSON 含 conversation_id 时正确解析

测试策略（与 test_api_client.py 一致）：
- ``@patch("research_rag.ui.api_client.requests.request")`` / ``requests.post``
- 返回预构造 Mock 响应对象，完全跳过真实 HTTP 调用
- 验证请求 URL / method / payload 正确，响应正确解析为 dataclass
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
    ConversationInfo,
    MessageInfo,
    QueryResult,
    StreamDone,
    StreamToken,
)

# ---------------------------------------------------------------------------
# 辅助：构造 Mock 响应（与 test_api_client.py 一致）
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


def make_citation_dict(**overrides: object) -> dict[str, object]:
    """构造一个 ``CitationRead`` 格式的 dict。"""

    base: dict[str, object] = {
        "document_id": "11111111-1111-1111-1111-111111111111",
        "document_name": "test.pdf",
        "start_page": 1,
        "end_page": 1,
        "chunk_index": 0,
        "snippet": "原文片段。",
        "score": 0.85,
    }
    base.update(overrides)
    return base


def make_conversation_dict(**overrides: object) -> dict[str, object]:
    """构造一个 ``ConversationRead`` 格式的 dict（列表场景，无 messages）。"""

    base: dict[str, object] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "title": None,
        "document_ids": None,
        "created_at": "2026-07-22T10:00:00",
        "updated_at": "2026-07-22T10:00:00",
        "messages": None,
    }
    base.update(overrides)
    return base


def make_message_dict(**overrides: object) -> dict[str, object]:
    """构造一个 ``MessageRead`` 格式的 dict。"""

    base: dict[str, object] = {
        "id": "22222222-2222-2222-2222-222222222222",
        "role": "user",
        "content": "用户问题。",
        "citations": None,
        "created_at": "2026-07-22T10:00:01",
    }
    base.update(overrides)
    return base


def make_query_dict(**overrides: object) -> dict[str, object]:
    """构造一个 ``QueryResponse`` 格式的 dict（含 conversation_id）。"""

    base: dict[str, object] = {
        "answer": "答案 [C1]。",
        "citations": [make_citation_dict()],
        "request_id": "33333333-3333-3333-3333-333333333333",
        "elapsed_ms": 200,
        "conversation_id": None,
    }
    base.update(overrides)
    return base


def make_sse_lines(events: list[tuple[str, str]]) -> list[str]:
    """从 ``[(event_name, data_json), ...]`` 构造 SSE 行列表。"""

    lines: list[str] = []
    for event_name, data_json in events:
        lines.append(f"event: {event_name}")
        lines.append(f"data: {data_json}")
        lines.append("")  # 空行：事件分隔
    return lines


def make_sse_response(
    lines: list[str],
    status_code: int = 200,
) -> MagicMock:
    """构造模拟 SSE 流式响应的 Mock 对象。"""

    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.iter_lines.return_value = lines
    if not response.ok:
        response.json.return_value = {"detail": "服务端错误"}
        response.text = '{"detail": "服务端错误"}'
    return response


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


class TestCreateConversation:
    @patch("research_rag.ui.api_client.requests.request")
    def test_default_create(self, mock_request: MagicMock) -> None:
        """默认创建：无 title、无 document_ids → POST 空 body。"""

        mock_request.return_value = make_response(201, make_conversation_dict())

        client = ApiClient()
        conv = client.create_conversation()

        assert isinstance(conv, ConversationInfo)
        assert conv.id == "11111111-1111-1111-1111-111111111111"
        assert conv.title is None
        assert conv.document_ids is None
        assert conv.messages is None

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["method"] == "POST"
        assert call_kwargs["url"] == f"{DEFAULT_API_BASE_URL}/conversations"
        assert call_kwargs["json"] == {}

    @patch("research_rag.ui.api_client.requests.request")
    def test_with_title_and_document_ids(self, mock_request: MagicMock) -> None:
        """带 title 和 document_ids：参数正确传入 JSON body。"""

        mock_request.return_value = make_response(
            201,
            make_conversation_dict(
                title="论文方法追问",
                document_ids=["44444444-4444-4444-4444-444444444444"],
            ),
        )

        client = ApiClient()
        conv = client.create_conversation(
            title="论文方法追问",
            document_ids=["44444444-4444-4444-4444-444444444444"],
        )

        assert conv.title == "论文方法追问"
        assert conv.document_ids == ["44444444-4444-4444-4444-444444444444"]

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["json"] == {
            "title": "论文方法追问",
            "document_ids": ["44444444-4444-4444-4444-444444444444"],
        }

    @patch("research_rag.ui.api_client.requests.request")
    def test_empty_document_ids_not_sent(self, mock_request: MagicMock) -> None:
        """``document_ids`` 为空列表时不写入 payload（与 list[uuid] None 同等语义）。"""

        mock_request.return_value = make_response(201, make_conversation_dict())

        client = ApiClient()
        client.create_conversation(document_ids=[])

        call_kwargs = mock_request.call_args.kwargs
        # document_ids=[] 走 falsy 分支，不写入 payload
        assert "document_ids" not in call_kwargs["json"]

    @patch("research_rag.ui.api_client.requests.request")
    def test_network_error_wrapped(self, mock_request: MagicMock) -> None:
        """网络错误：包装为 ApiClientError（status_code=0）。"""

        mock_request.side_effect = requests.ConnectionError("Connection refused")

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.create_conversation()

        assert exc_info.value.status_code == 0
        assert "无法连接" in exc_info.value.detail


# ---------------------------------------------------------------------------
# list_conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    @patch("research_rag.ui.api_client.requests.request")
    def test_returns_list(self, mock_request: MagicMock) -> None:
        """列表：返回 ConversationInfo 数组。"""

        mock_request.return_value = make_response(
            200,
            {
                "items": [
                    make_conversation_dict(title="会话 A"),
                    make_conversation_dict(
                        title="会话 B",
                        document_ids=["55555555-5555-5555-5555-555555555555"],
                    ),
                ]
            },
        )

        client = ApiClient()
        convs = client.list_conversations()

        assert len(convs) == 2
        assert convs[0].title == "会话 A"
        assert convs[0].messages is None  # 列表场景不返回消息
        assert convs[1].title == "会话 B"
        assert convs[1].document_ids == ["55555555-5555-5555-5555-555555555555"]

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["url"] == f"{DEFAULT_API_BASE_URL}/conversations"

    @patch("research_rag.ui.api_client.requests.request")
    def test_empty_list(self, mock_request: MagicMock) -> None:
        """空列表：返回空数组。"""

        mock_request.return_value = make_response(200, {"items": []})

        client = ApiClient()
        assert client.list_conversations() == []


# ---------------------------------------------------------------------------
# get_conversation
# ---------------------------------------------------------------------------


class TestGetConversation:
    @patch("research_rag.ui.api_client.requests.request")
    def test_success_with_messages(self, mock_request: MagicMock) -> None:
        """详情成功：返回 ConversationInfo，含完整消息列表。"""

        mock_request.return_value = make_response(
            200,
            make_conversation_dict(
                title="论文方法追问",
                messages=[
                    make_message_dict(role="user", content="问题"),
                    make_message_dict(
                        role="assistant",
                        content="答案 [C1]。",
                        citations=[make_citation_dict()],
                    ),
                ],
            ),
        )

        client = ApiClient()
        conv = client.get_conversation("11111111-1111-1111-1111-111111111111")

        assert conv.title == "论文方法追问"
        assert conv.messages is not None
        assert len(conv.messages) == 2
        assert isinstance(conv.messages[0], MessageInfo)
        assert conv.messages[0].role == "user"
        assert conv.messages[0].content == "问题"
        assert conv.messages[0].citations is None
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].citations is not None
        assert len(conv.messages[1].citations) == 1  # type: ignore[union-attr]
        assert isinstance(conv.messages[1].citations[0], Citation)
        assert conv.messages[1].citations[0].document_name == "test.pdf"

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["url"] == (
            f"{DEFAULT_API_BASE_URL}/conversations/11111111-1111-1111-1111-111111111111"
        )

    @patch("research_rag.ui.api_client.requests.request")
    def test_not_found_404(self, mock_request: MagicMock) -> None:
        """详情不存在：抛 ApiClientError（404）。"""

        mock_request.return_value = make_response(404, {"detail": "会话不存在"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.get_conversation("nonexistent-id")

        assert exc_info.value.status_code == 404
        assert "会话不存在" in exc_info.value.detail


# ---------------------------------------------------------------------------
# delete_conversation
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    @patch("research_rag.ui.api_client.requests.request")
    def test_success(self, mock_request: MagicMock) -> None:
        """删除成功：返回 204 无响应体。"""

        mock_request.return_value = make_response(204, None, text="")

        client = ApiClient()
        # 不抛异常即成功
        client.delete_conversation("11111111-1111-1111-1111-111111111111")

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["method"] == "DELETE"
        assert call_kwargs["url"] == (
            f"{DEFAULT_API_BASE_URL}/conversations/11111111-1111-1111-1111-111111111111"
        )

    @patch("research_rag.ui.api_client.requests.request")
    def test_not_found_404(self, mock_request: MagicMock) -> None:
        """删除不存在：抛 ApiClientError（404）。"""

        mock_request.return_value = make_response(404, {"detail": "会话不存在"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.delete_conversation("nonexistent-id")

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


class TestListMessages:
    @patch("research_rag.ui.api_client.requests.request")
    def test_returns_list(self, mock_request: MagicMock) -> None:
        """消息列表：返回 MessageInfo 数组。"""

        mock_request.return_value = make_response(
            200,
            [
                make_message_dict(role="user", content="问题"),
                make_message_dict(role="assistant", content="答案"),
            ],
        )

        client = ApiClient()
        msgs = client.list_messages("11111111-1111-1111-1111-111111111111")

        assert len(msgs) == 2
        assert isinstance(msgs[0], MessageInfo)
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["method"] == "GET"
        assert call_kwargs["url"] == (
            f"{DEFAULT_API_BASE_URL}/conversations/11111111-1111-1111-1111-111111111111/messages"
        )

    @patch("research_rag.ui.api_client.requests.request")
    def test_empty_list(self, mock_request: MagicMock) -> None:
        """空消息列表：返回空数组。"""

        mock_request.return_value = make_response(200, [])

        client = ApiClient()
        assert client.list_messages("11111111-1111-1111-1111-111111111111") == []

    @patch("research_rag.ui.api_client.requests.request")
    def test_not_found_404(self, mock_request: MagicMock) -> None:
        """会话不存在：抛 ApiClientError（404）。"""

        mock_request.return_value = make_response(404, {"detail": "会话不存在"})

        client = ApiClient()
        with pytest.raises(ApiClientError) as exc_info:
            client.list_messages("nonexistent-id")

        assert exc_info.value.status_code == 404

    @patch("research_rag.ui.api_client.requests.request")
    def test_response_with_items_wrapper(self, mock_request: MagicMock) -> None:
        """兼容 ``{"items": [...]}`` 包裹格式（API 可能返回包裹结构）。"""

        mock_request.return_value = make_response(
            200,
            {"items": [make_message_dict(role="user", content="问题")]},
        )

        client = ApiClient()
        msgs = client.list_messages("11111111-1111-1111-1111-111111111111")

        assert len(msgs) == 1
        assert msgs[0].role == "user"


# ---------------------------------------------------------------------------
# ask_question with conversation_id
# ---------------------------------------------------------------------------


class TestAskQuestionWithConversation:
    @patch("research_rag.ui.api_client.requests.request")
    def test_passes_conversation_id_to_payload(self, mock_request: MagicMock) -> None:
        """``conversation_id`` 正确写入 JSON payload。"""

        mock_request.return_value = make_response(200, make_query_dict())

        client = ApiClient()
        client.ask_question("追问", conversation_id="conv-123")

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["json"] == {
            "question": "追问",
            "conversation_id": "conv-123",
        }

    @patch("research_rag.ui.api_client.requests.request")
    def test_without_conversation_id_not_in_payload(self, mock_request: MagicMock) -> None:
        """未传 ``conversation_id`` 时 payload 不含该字段。"""

        mock_request.return_value = make_response(200, make_query_dict())

        client = ApiClient()
        client.ask_question("问题")

        call_kwargs = mock_request.call_args.kwargs
        assert "conversation_id" not in call_kwargs["json"]
        assert call_kwargs["json"] == {"question": "问题"}

    @patch("research_rag.ui.api_client.requests.request")
    def test_response_conversation_id_parsed(self, mock_request: MagicMock) -> None:
        """响应 JSON 含 ``conversation_id`` 时正确解析到 QueryResult。"""

        mock_request.return_value = make_response(
            200,
            make_query_dict(conversation_id="conv-from-server"),
        )

        client = ApiClient()
        result = client.ask_question("追问", conversation_id="conv-123")

        assert isinstance(result, QueryResult)
        assert result.conversation_id == "conv-from-server"

    @patch("research_rag.ui.api_client.requests.request")
    def test_response_conversation_id_none_when_absent(self, mock_request: MagicMock) -> None:
        """响应 JSON 不含 ``conversation_id`` 时 QueryResult.conversation_id 为 None。"""

        mock_request.return_value = make_response(
            200,
            {
                "answer": "答案。",
                "citations": [],
                "request_id": "x",
                "elapsed_ms": 10,
                # 注意：故意不带 conversation_id 字段
            },
        )

        client = ApiClient()
        result = client.ask_question("问题")

        assert result.conversation_id is None

    @patch("research_rag.ui.api_client.requests.request")
    def test_full_payload_with_all_params(self, mock_request: MagicMock) -> None:
        """``conversation_id`` + ``document_ids`` + ``top_k`` 同时传递。"""

        mock_request.return_value = make_response(200, make_query_dict())

        client = ApiClient()
        client.ask_question(
            "追问",
            document_ids=["doc-1", "doc-2"],
            top_k=5,
            conversation_id="conv-123",
        )

        call_kwargs = mock_request.call_args.kwargs
        assert call_kwargs["json"] == {
            "question": "追问",
            "document_ids": ["doc-1", "doc-2"],
            "top_k": 5,
            "conversation_id": "conv-123",
        }


# ---------------------------------------------------------------------------
# ask_question_stream with conversation_id
# ---------------------------------------------------------------------------


class TestAskQuestionStreamWithConversation:
    @patch("research_rag.ui.api_client.requests.post")
    def test_conversation_id_in_payload(self, mock_post: MagicMock) -> None:
        """``conversation_id`` 正确写入流式请求 JSON。"""

        sse_lines = make_sse_lines(
            [
                (
                    "done",
                    json.dumps(
                        {
                            "citations": [],
                            "request_id": "x",
                            "elapsed_ms": 50,
                            "conversation_id": "conv-from-server",
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        list(client.ask_question_stream("追问", conversation_id="conv-123"))

        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"] == {
            "question": "追问",
            "stream": True,
            "conversation_id": "conv-123",
        }

    @patch("research_rag.ui.api_client.requests.post")
    def test_done_event_has_conversation_id(self, mock_post: MagicMock) -> None:
        """done 事件含 ``conversation_id`` 时正确解析到 StreamDone。"""

        sse_lines = make_sse_lines(
            [
                ("token", json.dumps({"text": "答案 "}, ensure_ascii=False)),
                (
                    "done",
                    json.dumps(
                        {
                            "citations": [make_citation_dict()],
                            "request_id": "req-456",
                            "elapsed_ms": 120,
                            "conversation_id": "conv-from-server",
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        events = list(client.ask_question_stream("追问", conversation_id="conv-123"))

        tokens = [e for e in events if isinstance(e, StreamToken)]
        dones = [e for e in events if isinstance(e, StreamDone)]
        assert len(tokens) == 1
        assert tokens[0].text == "答案 "
        assert len(dones) == 1
        assert dones[0].conversation_id == "conv-from-server"
        assert dones[0].request_id == "req-456"
        assert dones[0].elapsed_ms == 120
        assert len(dones[0].citations) == 1

    @patch("research_rag.ui.api_client.requests.post")
    def test_done_event_conversation_id_none_when_absent(self, mock_post: MagicMock) -> None:
        """done 事件不含 ``conversation_id`` 时 StreamDone.conversation_id 为 None。"""

        sse_lines = make_sse_lines(
            [
                (
                    "done",
                    json.dumps(
                        {
                            "citations": [],
                            "request_id": "x",
                            "elapsed_ms": 10,
                            # 故意不带 conversation_id
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        events = list(client.ask_question_stream("问题"))

        dones = [e for e in events if isinstance(e, StreamDone)]
        assert len(dones) == 1
        assert dones[0].conversation_id is None

    @patch("research_rag.ui.api_client.requests.post")
    def test_done_event_conversation_id_null_explicit(self, mock_post: MagicMock) -> None:
        """done 事件 ``conversation_id`` 显式为 null 时 StreamDone.conversation_id 为 None。"""

        sse_lines = make_sse_lines(
            [
                (
                    "done",
                    json.dumps(
                        {
                            "citations": [],
                            "request_id": "x",
                            "elapsed_ms": 10,
                            "conversation_id": None,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        events = list(client.ask_question_stream("问题"))

        dones = [e for e in events if isinstance(e, StreamDone)]
        assert len(dones) == 1
        assert dones[0].conversation_id is None

    @patch("research_rag.ui.api_client.requests.post")
    def test_full_payload_with_all_params(self, mock_post: MagicMock) -> None:
        """``conversation_id`` + ``document_ids`` + ``top_k`` 同时传递。"""

        sse_lines = make_sse_lines(
            [
                (
                    "done",
                    json.dumps(
                        {"citations": [], "request_id": "x", "elapsed_ms": 1},
                        ensure_ascii=False,
                    ),
                ),
            ]
        )
        mock_post.return_value = make_sse_response(sse_lines)

        client = ApiClient()
        list(
            client.ask_question_stream(
                "追问",
                document_ids=["doc-1"],
                top_k=5,
                conversation_id="conv-123",
            )
        )

        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"] == {
            "question": "追问",
            "stream": True,
            "document_ids": ["doc-1"],
            "top_k": 5,
            "conversation_id": "conv-123",
        }
