"""历史消息反馈状态初始化单元测试（Issue #92）。

测试 ``_init_feedback_state_for_history`` 纯函数：进入历史会话时批量查询
每条 assistant 消息的反馈状态，写入 ``session_state`` 供 ``_render_feedback_buttons``
读取。函数接收 ``session_state: dict`` 参数（由调用方传 ``st.session_state``），
避免直接依赖 streamlit，便于单元测试。

测试策略：
- 用 ``unittest.mock.MagicMock`` 模拟 ``ApiClient``，``get_feedback`` 返回预设
  ``FeedbackInfo`` 或 ``None``，验证调用次数与 ``session_state`` 写入。
- 传入普通 ``dict`` 作为 ``session_state``，断言键值正确。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from research_rag.ui.api_client import FeedbackInfo, MessageInfo
from research_rag.ui.app import _init_feedback_state_for_history


def _make_assistant_msg(request_id: str | None) -> MessageInfo:
    """构造 assistant 消息（仅关心 request_id 字段）。"""

    return MessageInfo(
        id="msg-id",
        role="assistant",
        content="答案",
        citations=None,
        request_id=request_id,
        created_at="2026-07-26T10:00:00Z",
    )


def _make_user_msg() -> MessageInfo:
    """构造 user 消息（无 request_id）。"""

    return MessageInfo(
        id="msg-id",
        role="user",
        content="问题",
        citations=None,
        request_id=None,
        created_at="2026-07-26T10:00:00Z",
    )


class TestInitFeedbackStateForHistory:
    def test_assistant_with_request_id_writes_none_when_no_feedback(self) -> None:
        """assistant 消息有 request_id 且无反馈（get_feedback 返回 None）：
        ``session_state["feedback-<request_id>"]`` 写入 ``None``。"""

        client = MagicMock()
        client.get_feedback.return_value = None

        messages = [_make_assistant_msg(request_id="req-1")]
        session_state: dict[str, object] = {}

        _init_feedback_state_for_history(client, messages, session_state)

        assert session_state["feedback-req-1"] is None
        client.get_feedback.assert_called_once_with("req-1")

    def test_writes_like_when_feedback_rating_is_like(self) -> None:
        """已点赞（get_feedback 返回 rating="like"）：session_state 写入 "like"。"""

        client = MagicMock()
        client.get_feedback.return_value = FeedbackInfo(
            id="fb-1",
            request_id="req-1",
            message_id=None,
            rating="like",
            comment=None,
            created_at="2026-07-26T10:00:00Z",
            updated_at="2026-07-26T10:00:00Z",
        )

        messages = [_make_assistant_msg(request_id="req-1")]
        session_state: dict[str, object] = {}

        _init_feedback_state_for_history(client, messages, session_state)

        assert session_state["feedback-req-1"] == "like"

    def test_writes_dislike_when_feedback_rating_is_dislike(self) -> None:
        """已点踩（get_feedback 返回 rating="dislike"）：session_state 写入 "dislike"。"""

        client = MagicMock()
        client.get_feedback.return_value = FeedbackInfo(
            id="fb-1",
            request_id="req-1",
            message_id=None,
            rating="dislike",
            comment="答案不准确",
            created_at="2026-07-26T10:00:00Z",
            updated_at="2026-07-26T10:00:00Z",
        )

        messages = [_make_assistant_msg(request_id="req-1")]
        session_state: dict[str, object] = {}

        _init_feedback_state_for_history(client, messages, session_state)

        assert session_state["feedback-req-1"] == "dislike"

    def test_skips_user_messages_and_none_request_id(self) -> None:
        """user 消息与 request_id=None 的 assistant 消息跳过：不调 get_feedback，不写 key。"""

        client = MagicMock()
        client.get_feedback.return_value = None

        messages = [
            _make_user_msg(),
            _make_assistant_msg(request_id=None),
            _make_assistant_msg(request_id="req-1"),
        ]
        session_state: dict[str, object] = {}

        _init_feedback_state_for_history(client, messages, session_state)

        # 只对第三条（request_id="req-1"）调用 get_feedback
        client.get_feedback.assert_called_once_with("req-1")
        # request_id=None 的消息不写 key
        assert "feedback-None" not in session_state
        assert "feedback-req-1" in session_state

    def test_does_not_overwrite_existing_state(self) -> None:
        """session_state 已有同 key 时不覆盖：避免 rerun 时丢失用户刚操作的反馈。

        场景：用户在本会话刚点了赞（session_state["feedback-req-1"]="like"），
        st.rerun() 触发 _render_chat 重新调用本函数，不应调 get_feedback 覆盖
        本地状态（否则用户刚点的赞会被服务端状态覆盖，且多一次网络请求）。
        """

        client = MagicMock()
        client.get_feedback.return_value = None

        messages = [_make_assistant_msg(request_id="req-1")]
        session_state: dict[str, object] = {"feedback-req-1": "like"}

        _init_feedback_state_for_history(client, messages, session_state)

        # 不调 get_feedback（已有状态跳过）
        client.get_feedback.assert_not_called()
        # 已有状态不覆盖
        assert session_state["feedback-req-1"] == "like"
