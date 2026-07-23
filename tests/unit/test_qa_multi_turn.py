"""qa_service 多轮对话底层函数单元测试（阶段 9.2）。

测试覆盖：
- ``estimate_tokens``：粗估 token 数（中文/英文/空串）
- ``truncate_history_messages``：轮数截断 + token 截断 + 边界
- ``build_prompt_with_history``：消息顺序 + 引用编号独立 + 空历史等价单轮
- ``rewrite_query``：无历史返回原问题、成功改写、LLM 异常回退、空响应回退
- ``answer_with_messages``：多轮路径与单轮共享 ``_invoke_and_parse`` 行为

外部 LLM 调用通过 ``FakeListChatModel`` / 自定义 Fake Mock，CI 不消耗真实 Token。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from typing_extensions import override

from research_rag.qa_service import (
    DEFAULT_MAX_HISTORY_TOKENS,
    DEFAULT_MAX_HISTORY_TURNS,
    INSUFFICIENT_EVIDENCE_MARKER,
    AnswerWithCitations,
    ContextPiece,
    InsufficientEvidenceError,
    LlmServiceError,
    answer_question,
    answer_with_messages,
    build_prompt,
    build_prompt_with_history,
    estimate_tokens,
    rewrite_query,
    truncate_history_messages,
)

if TYPE_CHECKING:
    from langchain_core.callbacks import CallbackManagerForLLMRun


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_contexts(n: int = 1) -> list[ContextPiece]:
    """构造 n 个上下文片段，编号用于断言。"""
    return [
        ContextPiece(
            document_name=f"论文{idx}.pdf",
            start_page=idx,
            end_page=idx,
            chunk_index=idx - 1,
            content=f"片段 {idx} 内容。",
            score=1.0 - idx * 0.1,
        )
        for idx in range(1, n + 1)
    ]


def _make_history(turns: int, *, per_msg_len: int = 10) -> list[BaseMessage]:
    """构造 n 轮历史（user + assistant 交替），每条消息内容长度约 per_msg_len。"""
    history: list[BaseMessage] = []
    for t in range(turns):
        # 内容含轮次编号，便于断言顺序
        user_content = f"用户问题{t}" + "x" * max(0, per_msg_len - 5)
        ai_content = f"助手回答{t}" + "y" * max(0, per_msg_len - 5)
        history.append(HumanMessage(content=user_content))
        history.append(AIMessage(content=ai_content))
    return history


class _RaisingChatModel(BaseChatModel):
    """调用时总是抛 RuntimeError 的假 ChatModel，测试 LLM 失败路径。"""

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = "模拟 LLM 调用失败"
        raise RuntimeError(msg)

    @property
    @override
    def _llm_type(self) -> str:
        return "raising"


class _NonStringContentChatModel(BaseChatModel):
    """返回非字符串 content 的假 ChatModel，测试非字符串响应回退。"""

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # LangChain 部分模型返回 list[dict] 形式 content（如 tool call）
        msg = AIMessage(content=[{"type": "text", "text": "复杂内容"}])  # type: ignore[arg-type]
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    @override
    def _llm_type(self) -> str:
        return "non-string"


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_chinese() -> None:
    """中文文本：len // 3 粗估。"""
    assert estimate_tokens("深度学习") == max(1, 4 // 3)  # 1


def test_estimate_tokens_english() -> None:
    """英文文本：len // 3 粗估。"""
    assert estimate_tokens("hello world") == max(1, 11 // 3)  # 3


def test_estimate_tokens_empty_returns_at_least_one() -> None:
    """空串至少返回 1，避免空消息被忽略导致截断失效。"""
    assert estimate_tokens("") == 1


def test_estimate_tokens_long_text() -> None:
    """长文本按 len // 3 估算。"""
    text = "a" * 300
    assert estimate_tokens(text) == 100


# ---------------------------------------------------------------------------
# truncate_history_messages：轮数截断
# ---------------------------------------------------------------------------


def test_truncate_history_empty_returns_empty() -> None:
    """空历史截断后仍为空。"""
    assert truncate_history_messages([]) == []


def test_truncate_history_within_turn_limit_unchanged() -> None:
    """历史轮数 ≤ max_turns：全部保留。"""
    history = _make_history(3)  # 6 条消息 = 3 轮
    truncated = truncate_history_messages(history, max_turns=5)
    assert len(truncated) == 6
    assert truncated == history


def test_truncate_history_exceeds_turn_limit_keeps_recent() -> None:
    """历史轮数 > max_turns：保留最近 max_turns 轮（最老的被裁）。"""
    history = _make_history(7)  # 14 条消息 = 7 轮
    truncated = truncate_history_messages(history, max_turns=5)
    # 保留最近 5 轮 = 10 条
    assert len(truncated) == 10
    # 最老的 2 轮被裁：第一条保留应是第 3 轮的 user
    assert truncated[0] == history[4]  # 第 3 轮 user（index 4）
    # 最后一条应是第 7 轮的 assistant
    assert truncated[-1] == history[-1]


def test_truncate_history_max_turns_zero_returns_empty() -> None:
    """max_turns=0：返回空列表（全部裁掉）。"""
    history = _make_history(2)
    assert truncate_history_messages(history, max_turns=0) == []


def test_truncate_history_odd_message_count() -> None:
    """历史消息数为奇数（如仅 user 无 assistant）：仍按 max_messages 切片。"""
    history: list[BaseMessage] = [
        HumanMessage(content="问题1"),
        AIMessage(content="答案1"),
        HumanMessage(content="问题2"),  # 无对应 assistant 回复
        AIMessage(content="答案3"),
        HumanMessage(content="问题4"),
    ]
    truncated = truncate_history_messages(history, max_turns=1)
    # max_messages = 2，取最后 2 条
    assert len(truncated) == 2
    assert truncated[0] == history[-2]  # 答案3
    assert truncated[1] == history[-1]  # 问题4


# ---------------------------------------------------------------------------
# truncate_history_messages：token 截断
# ---------------------------------------------------------------------------


def test_truncate_history_token_limit_trims_oldest() -> None:
    """token 上限触发：从最老开始裁，直到总 token ≤ max_tokens。"""
    # 每条消息内容 30 字符 → 10 token；4 条 = 40 token
    history = _make_history(2, per_msg_len=30)
    # 总 token = 4 * 10 = 40
    # 设 max_tokens=25 → 裁掉最老 2 条（20 token），剩 20 token ≤ 25
    truncated = truncate_history_messages(history, max_turns=10, max_tokens=25)
    assert len(truncated) == 2
    # 保留最近 2 条（第 2 轮的 user + assistant）
    assert truncated[0] == history[2]
    assert truncated[1] == history[3]


def test_truncate_history_token_limit_keeps_all_when_under_limit() -> None:
    """token 总数 ≤ max_tokens：全部保留（不裁）。"""
    history = _make_history(2, per_msg_len=10)  # 4 条 * ~4 token = 16 token
    truncated = truncate_history_messages(history, max_turns=10, max_tokens=100)
    assert len(truncated) == 4


def test_truncate_history_token_limit_zero_returns_empty() -> None:
    """max_tokens=0：循环裁到空（每条消息至少 1 token，永远 > 0）。"""
    history = _make_history(2)
    assert truncate_history_messages(history, max_turns=10, max_tokens=0) == []


def test_truncate_history_turns_then_tokens_double_protection() -> None:
    """轮数 + token 双重保护：先按轮数粗筛，再按 token 精裁。"""
    # 10 轮 = 20 条消息，每条 30 字符 = 10 token，总 200 token
    history = _make_history(10, per_msg_len=30)
    # max_turns=5 → 粗筛到 10 条（100 token）
    # max_tokens=45 → 精裁掉最老 6 条（60 token），剩 4 条（40 token）≤ 45
    truncated = truncate_history_messages(history, max_turns=5, max_tokens=45)
    assert len(truncated) == 4
    # 保留的是最后 4 条
    assert truncated[0] == history[-4]
    assert truncated[-1] == history[-1]


def test_truncate_history_default_constants() -> None:
    """默认参数：max_turns=5, max_tokens=4000。"""
    assert DEFAULT_MAX_HISTORY_TURNS == 5
    assert DEFAULT_MAX_HISTORY_TOKENS == 4000
    # 5 轮正常历史（每条短消息）应在默认值内全部保留
    history = _make_history(5, per_msg_len=10)
    truncated = truncate_history_messages(history)
    assert len(truncated) == 10


# ---------------------------------------------------------------------------
# build_prompt_with_history
# ---------------------------------------------------------------------------


def test_build_prompt_with_history_structure() -> None:
    """带历史 prompt 结构：[SystemMessage, *history, HumanMessage]。"""
    contexts = _make_contexts(2)
    history = _make_history(1)
    messages = build_prompt_with_history("当前问题", contexts, history)

    # SystemMessage + 2 条历史 + 当前 HumanMessage = 4 条
    assert len(messages) == 4
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)  # 历史 user
    assert isinstance(messages[2], AIMessage)  # 历史 assistant
    assert isinstance(messages[3], HumanMessage)  # 当前问题


def test_build_prompt_with_history_empty_equals_build_prompt() -> None:
    """空历史时，build_prompt_with_history 等价于 build_prompt。"""
    contexts = _make_contexts(1)
    base = build_prompt("问题", contexts)
    with_history = build_prompt_with_history("问题", contexts, [])
    assert len(with_history) == len(base)  # 2
    assert isinstance(with_history[0], SystemMessage)
    assert isinstance(with_history[1], HumanMessage)
    # 内容一致
    assert with_history[0].content == base[0].content
    assert with_history[1].content == base[1].content


def test_build_prompt_with_history_current_question_in_last_message() -> None:
    """当前问题注入最后一条 HumanMessage（不是历史）。"""
    contexts = _make_contexts(1)
    history = _make_history(1)
    messages = build_prompt_with_history("那篇论文的方法", contexts, history)
    assert "那篇论文的方法" in messages[-1].content


def test_build_prompt_with_history_citation_indices_independent_per_turn() -> None:
    """每轮引用编号独立：[C1] 只指代当前轮 contexts（历史轮引用不混入）。

    验证 prompt 中当前 HumanMessage 的上下文块编号从 [C1] 开始，
    不受历史消息中可能出现的 [C1] 标记影响。
    """
    contexts = _make_contexts(2)
    # 历史中含 [C1] 标记（模拟上一轮 assistant 引用）
    history: list[BaseMessage] = [
        HumanMessage(content="上一轮问题"),
        AIMessage(content="上一轮答案 [C1]。"),
    ]
    messages = build_prompt_with_history("追问", contexts, history)
    current_human = messages[-1]
    # 当前 HumanMessage 应含 [C1] 和 [C2]（当前轮 contexts 编号）
    assert "[C1]" in current_human.content
    assert "[C2]" in current_human.content


# ---------------------------------------------------------------------------
# rewrite_query
# ---------------------------------------------------------------------------


def test_rewrite_query_no_history_returns_original() -> None:
    """无历史：直接返回原问题（首轮无需改写）。"""
    chat_model = FakeListChatModel(responses=["不应被调用"])
    result = rewrite_query("独立问题", [], chat_model)
    assert result == "独立问题"


def test_rewrite_query_success_returns_rewritten() -> None:
    """有历史 + LLM 成功：返回改写后的问题（strip 后）。"""
    chat_model = FakeListChatModel(responses=["  论文A的核心方法是什么？  "])
    history = _make_history(1)
    result = rewrite_query("那篇论文的方法再详细说说", history, chat_model)
    assert result == "论文A的核心方法是什么？"


def test_rewrite_query_llm_exception_falls_back_to_original() -> None:
    """LLM 调用抛异常：回退到原问题（不阻塞问答）。"""
    chat_model = _RaisingChatModel()
    history = _make_history(1)
    original = "那篇论文的方法"
    result = rewrite_query(original, history, chat_model)
    assert result == original


def test_rewrite_query_empty_response_falls_back() -> None:
    """LLM 返回空字符串：回退到原问题。"""
    chat_model = FakeListChatModel(responses=["   "])
    history = _make_history(1)
    original = "原问题"
    result = rewrite_query(original, history, chat_model)
    assert result == original


def test_rewrite_query_non_string_content_falls_back() -> None:
    """LLM 返回非字符串 content：回退到原问题。"""
    chat_model = _NonStringContentChatModel()
    history = _make_history(1)
    original = "原问题"
    result = rewrite_query(original, history, chat_model)
    assert result == original


# ---------------------------------------------------------------------------
# answer_with_messages（多轮路径）
# ---------------------------------------------------------------------------


def test_answer_with_messages_success_returns_answer_with_citations() -> None:
    """多轮路径正常调用：返回 AnswerWithCitations，引用编号正确解析。"""
    contexts = _make_contexts(2)
    history = _make_history(1)
    messages = build_prompt_with_history("追问", contexts, history)
    chat_model = FakeListChatModel(responses=["根据 [C1] 可知答案。"])
    result = answer_with_messages(messages, contexts, chat_model)

    assert isinstance(result, AnswerWithCitations)
    assert result.answer_text == "根据 [C1] 可知答案。"
    assert result.citation_indices == [1]
    assert len(result.citations) == 1
    assert result.citations[0].document_name == "论文1.pdf"


def test_answer_with_messages_empty_contexts_raises() -> None:
    """空上下文：抛 LlmServiceError（与单轮一致）。"""
    chat_model = FakeListChatModel(responses=["x"])
    with pytest.raises(LlmServiceError):
        answer_with_messages([HumanMessage(content="x")], [], chat_model)


def test_answer_with_messages_insufficient_evidence_raises() -> None:
    """证据不足标记：抛 InsufficientEvidenceError（与单轮一致）。"""
    contexts = _make_contexts(1)
    chat_model = FakeListChatModel(responses=[INSUFFICIENT_EVIDENCE_MARKER])
    with pytest.raises(InsufficientEvidenceError):
        answer_with_messages([HumanMessage(content="x")], contexts, chat_model)


def test_answer_with_messages_llm_error_raises_llm_service_error() -> None:
    """LLM 调用抛异常：包装为 LlmServiceError。"""
    contexts = _make_contexts(1)
    chat_model = _RaisingChatModel()
    with pytest.raises(LlmServiceError):
        answer_with_messages([HumanMessage(content="x")], contexts, chat_model)


def test_answer_with_messages_and_answer_question_share_behavior() -> None:
    """多轮与单轮路径共享 _invoke_and_parse：相同 LLM 输出 → 相同解析结果。"""
    contexts = _make_contexts(2)
    chat_model = FakeListChatModel(responses=["答案 [C2]。"])

    # 单轮
    single = answer_question("问题", contexts, chat_model)
    # 多轮（用相同 chat_model，FakeListChatModel 已消费一个 response，需重建）
    chat_model2 = FakeListChatModel(responses=["答案 [C2]。"])
    history = _make_history(1)
    messages = build_prompt_with_history("问题", contexts, history)
    multi = answer_with_messages(messages, contexts, chat_model2)

    assert single.answer_text == multi.answer_text
    assert single.citation_indices == multi.citation_indices
    assert len(multi.citations) == 1
    assert multi.citations[0].document_name == "论文2.pdf"
