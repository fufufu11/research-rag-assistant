"""qa_service 单元测试。

测试覆盖（PROJECT_PLAN.md 第 13.1 节、阶段 4 验收）：
- create_chat_model：依赖缺失抛 LlmServiceError；正常创建传参正确
- build_prompt：SystemMessage 含四条约束；HumanMessage 含问题、编号、文档名、页码
- parse_citation_indices：单个/多个/重复/大小写/无引用/多位编号
- map_citations：正常映射/越界跳过/重复去重/字段正确
- answer_question：正常答案+引用；证据不足；LLM 异常；无引用答案；空上下文
- retrieval_to_context：从 RetrievalResult 转换
- 数据结构不可变

外部 LLM 调用全部通过 ``FakeListChatModel``（langchain_core 内置）或自定义
``_RaisingChatModel`` Mock，CI 不消耗真实 Token，也不需要任何 API 密钥。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI
from typing_extensions import override

from research_rag.qa_service import (
    DEFAULT_LLM_MAX_RETRIES,
    DEFAULT_LLM_TIMEOUT,
    INSUFFICIENT_EVIDENCE_MARKER,
    AnswerWithCitations,
    Citation,
    ContextPiece,
    InsufficientEvidenceError,
    LlmConfig,
    LlmServiceError,
    answer_question,
    build_prompt,
    create_chat_model,
    map_citations,
    parse_citation_indices,
    retrieval_to_context,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.callbacks import CallbackManagerForLLMRun


# ---------------------------------------------------------------------------
# 辅助：构造测试用的 ContextPiece 列表
# ---------------------------------------------------------------------------


def _make_contexts() -> list[ContextPiece]:
    """构造两个文档的上下文片段（深度学习 vs 检索主题）。"""
    return [
        ContextPiece(
            document_name="论文A.pdf",
            start_page=1,
            end_page=1,
            chunk_index=0,
            content="深度学习是机器学习的一个分支，使用多层神经网络。",
            score=0.92,
        ),
        ContextPiece(
            document_name="论文B.pdf",
            start_page=2,
            end_page=2,
            chunk_index=1,
            content="余弦相似度衡量两个向量方向的差异，常用于向量检索。",
            score=0.85,
        ),
    ]


# ---------------------------------------------------------------------------
# 辅助：调用时总是抛异常的假 ChatModel（用于测试 LLM 失败路径）
# ---------------------------------------------------------------------------


class _RaisingChatModel(BaseChatModel):
    """测试用：调用时总是抛 RuntimeError 的假 ChatModel。

    继承 ``BaseChatModel`` 以满足 ``answer_question`` 的类型约束，
    并在 ``_generate`` 中抛异常以模拟 LLM 服务故障（网络超时、5xx 等）。
    """

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


# ---------------------------------------------------------------------------
# LlmConfig 测试
# ---------------------------------------------------------------------------


def test_llm_config_default_values() -> None:
    """默认配置应符合保守值（timeout=30s, max_retries=2）。"""
    config = LlmConfig()
    assert config.timeout == DEFAULT_LLM_TIMEOUT
    assert config.timeout == 30.0
    assert config.max_retries == DEFAULT_LLM_MAX_RETRIES
    assert config.max_retries == 2
    assert config.base_url == ""
    assert config.api_key == ""
    assert config.model == ""


def test_llm_config_custom_values() -> None:
    """自定义配置应生效。"""
    config = LlmConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-chat",
        timeout=10.0,
        max_retries=3,
    )
    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key == "sk-test"
    assert config.model == "deepseek-chat"
    assert config.timeout == 10.0
    assert config.max_retries == 3


# ---------------------------------------------------------------------------
# create_chat_model 测试
# ---------------------------------------------------------------------------


def test_create_chat_model_raises_on_missing_langchain_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未安装 langchain-openai 时应抛 LlmServiceError。"""
    import sys

    # 模拟 langchain_openai 不可导入（与 test_embedding.py 中 Mock 方式一致）
    monkeypatch.setitem(sys.modules, "langchain_openai", None)

    with pytest.raises(LlmServiceError, match="langchain_openai"):
        create_chat_model(LlmConfig(api_key="test-key", model="test"))


def test_create_chat_model_returns_chat_openai_with_config() -> None:
    """正常创建应返回 ChatOpenAI 实例且参数传递正确。"""
    config = LlmConfig(
        base_url="https://api.example.com",
        api_key="test-key",
        model="test-model",
        timeout=15.0,
        max_retries=3,
    )
    model = create_chat_model(config)

    # 类型检查：返回的应是 ChatOpenAI（继承 BaseChatModel）
    assert isinstance(model, ChatOpenAI)
    # 字段验证：model_name / max_retries / request_timeout / openai_api_base
    assert model.model_name == "test-model"
    assert model.max_retries == 3
    assert model.request_timeout == 15.0
    assert model.openai_api_base == "https://api.example.com"


def test_create_chat_model_empty_base_url_uses_default() -> None:
    """base_url 为空字符串时应正常创建（ChatOpenAI 内部用 OpenAI 默认端点）。"""
    config = LlmConfig(api_key="test-key", model="gpt-4o-mini")
    model = create_chat_model(config)
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# build_prompt 测试
# ---------------------------------------------------------------------------


def test_build_prompt_returns_system_and_human_message() -> None:
    """应返回 [SystemMessage, HumanMessage] 两条消息。"""
    contexts = _make_contexts()
    messages = build_prompt("问题", contexts)
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)


def test_build_prompt_system_message_contains_four_constraints() -> None:
    """SystemMessage 应包含第 9.3 节四条约束。"""
    contexts = _make_contexts()
    messages = build_prompt("问题", contexts)
    system_text = messages[0].content
    assert isinstance(system_text, str)

    # 约束1：只能使用上下文作答
    assert "上下文" in system_text
    assert "外部知识" in system_text
    # 约束2：证据不足时输出 [INSUFFICIENT_EVIDENCE]
    assert INSUFFICIENT_EVIDENCE_MARKER in system_text
    # 约束3：用 [C1]/[C3] 引用
    assert "[C1]" in system_text
    # 约束4：不得编造文档名、页码
    assert "编造" in system_text


def test_build_prompt_human_message_contains_question() -> None:
    """HumanMessage 应包含用户问题。"""
    contexts = _make_contexts()
    messages = build_prompt("深度学习是什么？", contexts)
    user_text = messages[1].content
    assert isinstance(user_text, str)
    assert "深度学习是什么？" in user_text


def test_build_prompt_human_message_contains_citation_indices() -> None:
    """HumanMessage 应包含 [C1]、[C2] 等编号标记。"""
    contexts = _make_contexts()
    messages = build_prompt("问题", contexts)
    user_text = messages[1].content
    assert isinstance(user_text, str)
    assert "[C1]" in user_text
    assert "[C2]" in user_text


def test_build_prompt_human_message_contains_document_and_page() -> None:
    """HumanMessage 应包含文档名和页码，便于服务端映射。"""
    contexts = _make_contexts()
    messages = build_prompt("问题", contexts)
    user_text = messages[1].content
    assert isinstance(user_text, str)
    assert "论文A.pdf" in user_text
    assert "论文B.pdf" in user_text
    assert "第1页" in user_text
    assert "第2页" in user_text


def test_build_prompt_preserves_context_order() -> None:
    """上下文顺序应与编号一致（[C1] 对应 contexts[0]）。"""
    contexts = _make_contexts()
    messages = build_prompt("问题", contexts)
    user_text = messages[1].content
    assert isinstance(user_text, str)
    # [C1] 块应在 [C2] 块之前
    assert user_text.index("[C1]") < user_text.index("[C2]")
    # [C1] 块应包含 contexts[0] 的内容
    assert contexts[0].content in user_text


def test_build_prompt_single_context() -> None:
    """单个上下文也应能构造 Prompt（不抛异常）。"""
    contexts = [
        ContextPiece(
            document_name="single.pdf",
            start_page=1,
            end_page=1,
            chunk_index=0,
            content="单个片段。",
            score=0.5,
        )
    ]
    messages = build_prompt("问题", contexts)
    assert len(messages) == 2


# ---------------------------------------------------------------------------
# parse_citation_indices 测试
# ---------------------------------------------------------------------------


def test_parse_citation_indices_single() -> None:
    """单个引用应正确提取。"""
    assert parse_citation_indices("根据 [C1] 可知") == [1]


def test_parse_citation_indices_multiple() -> None:
    """多个引用应按出现顺序提取。"""
    assert parse_citation_indices("[C1] [C3] [C2]") == [1, 3, 2]


def test_parse_citation_indices_dedup() -> None:
    """重复编号应去重，保留首次出现位置。"""
    assert parse_citation_indices("[C1] 中间 [C1] 结尾") == [1]


def test_parse_citation_indices_case_insensitive() -> None:
    """大小写不敏感：[c1] 和 [C2] 都应识别。"""
    assert parse_citation_indices("[c1] [C2]") == [1, 2]


def test_parse_citation_indices_no_match() -> None:
    """无引用标记时应返回空列表。"""
    assert parse_citation_indices("这是一个没有引用的答案。") == []


def test_parse_citation_indices_multi_digit() -> None:
    """多位编号应正确提取。"""
    assert parse_citation_indices("参见 [C12] 和 [C99]") == [12, 99]


def test_parse_citation_indices_empty_string() -> None:
    """空字符串应返回空列表。"""
    assert parse_citation_indices("") == []


def test_parse_citation_indices_not_confused_with_other_brackets() -> None:
    """不应误匹配非 [C数字] 形式的方括号。"""
    # [A1]、[1]、[备注] 都不应被识别
    assert parse_citation_indices("[A1] [1] [备注]") == []


# ---------------------------------------------------------------------------
# map_citations 测试
# ---------------------------------------------------------------------------


def test_map_citations_normal() -> None:
    """正常编号应映射到正确的 Citation。"""
    contexts = _make_contexts()
    citations = map_citations([1, 2], contexts)
    assert len(citations) == 2
    assert citations[0].document_name == "论文A.pdf"
    assert citations[0].start_page == 1
    assert citations[0].end_page == 1
    assert citations[0].snippet == contexts[0].content
    assert citations[0].score == pytest.approx(0.92)


def test_map_citations_out_of_range_skipped() -> None:
    """越界编号（0、超过上下文数量）应静默跳过。"""
    contexts = _make_contexts()
    citations = map_citations([0, 1, 99], contexts)
    assert len(citations) == 1
    assert citations[0].document_name == "论文A.pdf"


def test_map_citations_dedup() -> None:
    """重复编号应只映射一次。"""
    contexts = _make_contexts()
    citations = map_citations([1, 1, 1], contexts)
    assert len(citations) == 1


def test_map_citations_empty_indices() -> None:
    """空编号列表应返回空引用列表。"""
    contexts = _make_contexts()
    assert map_citations([], contexts) == []


def test_map_citations_preserves_order() -> None:
    """映射顺序应与编号顺序一致（去重后）。"""
    contexts = _make_contexts()
    citations = map_citations([2, 1], contexts)
    assert len(citations) == 2
    assert citations[0].document_name == "论文B.pdf"
    assert citations[1].document_name == "论文A.pdf"


# ---------------------------------------------------------------------------
# answer_question 测试
# ---------------------------------------------------------------------------


def test_answer_question_normal_with_citations() -> None:
    """正常答案含引用标记时应正确解析并映射。"""
    contexts = _make_contexts()
    fake_model = FakeListChatModel(responses=["根据 [C1] 可知，深度学习使用多层神经网络 [C2]。"])

    result = answer_question("深度学习是什么？", contexts, fake_model)

    assert result.answer_text == "根据 [C1] 可知，深度学习使用多层神经网络 [C2]。"
    assert result.citation_indices == [1, 2]
    assert len(result.citations) == 2
    assert result.citations[0].document_name == "论文A.pdf"
    assert result.citations[0].start_page == 1
    assert result.citations[0].end_page == 1
    assert result.citations[0].snippet == contexts[0].content
    assert result.citations[1].document_name == "论文B.pdf"
    assert result.citations[1].start_page == 2
    assert result.citations[1].end_page == 2


def test_answer_question_single_citation() -> None:
    """只引用一个编号时应返回单个引用。"""
    contexts = _make_contexts()
    fake_model = FakeListChatModel(responses=["深度学习是机器学习分支 [C1]。"])
    result = answer_question("问题", contexts, fake_model)
    assert result.citation_indices == [1]
    assert len(result.citations) == 1


def test_answer_question_insufficient_evidence() -> None:
    """模型输出 [INSUFFICIENT_EVIDENCE] 时应抛 InsufficientEvidenceError。"""
    contexts = _make_contexts()
    fake_model = FakeListChatModel(responses=[INSUFFICIENT_EVIDENCE_MARKER])

    with pytest.raises(InsufficientEvidenceError):
        answer_question("无关问题", contexts, fake_model)


def test_answer_question_insufficient_evidence_with_extra_text() -> None:
    """即使答案中混有其他文字，只要包含标记就视为证据不足。"""
    contexts = _make_contexts()
    fake_model = FakeListChatModel(responses=[f"抱歉，{INSUFFICIENT_EVIDENCE_MARKER}"])
    with pytest.raises(InsufficientEvidenceError):
        answer_question("问题", contexts, fake_model)


def test_answer_question_llm_failure() -> None:
    """LLM 调用抛异常时应包装为 LlmServiceError。"""
    contexts = _make_contexts()
    fake_model = _RaisingChatModel()

    with pytest.raises(LlmServiceError, match="调用大模型失败"):
        answer_question("问题", contexts, fake_model)


def test_answer_question_no_citations() -> None:
    """模型输出无引用标记时，citation_indices 和 citations 应为空。"""
    contexts = _make_contexts()
    fake_model = FakeListChatModel(responses=["这是一个没有引用的答案。"])

    result = answer_question("问题", contexts, fake_model)

    assert result.answer_text == "这是一个没有引用的答案。"
    assert result.citation_indices == []
    assert result.citations == []


def test_answer_question_invalid_citation_index_skipped() -> None:
    """模型输出越界编号（如 [C99]）时应跳过，不抛异常。"""
    contexts = _make_contexts()
    fake_model = FakeListChatModel(responses=["见 [C1] 和 [C99]。"])
    result = answer_question("问题", contexts, fake_model)
    assert result.citation_indices == [1, 99]
    # 越界编号 99 在 map_citations 中被跳过
    assert len(result.citations) == 1
    assert result.citations[0].document_name == "论文A.pdf"


def test_answer_question_empty_contexts_raises() -> None:
    """空上下文应抛 LlmServiceError（无法构造 Prompt）。"""
    fake_model = FakeListChatModel(responses=["unused"])
    with pytest.raises(LlmServiceError, match="上下文为空"):
        answer_question("问题", [], fake_model)


def test_answer_question_passes_messages_to_model() -> None:
    """应把 build_prompt 的结果传给 chat_model.invoke。"""
    contexts = _make_contexts()
    captured_messages: list[Sequence[BaseMessage]] = []

    class _CapturingModel(BaseChatModel):
        @override
        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: CallbackManagerForLLMRun | None = None,
            **kwargs: Any,
        ) -> ChatResult:
            captured_messages.append(list(messages))
            from langchain_core.messages import AIMessage
            from langchain_core.outputs import ChatGeneration

            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok [C1]"))])

        @property
        @override
        def _llm_type(self) -> str:
            return "capturing"

    result = answer_question("问题", contexts, _CapturingModel())
    assert result.answer_text == "ok [C1]"
    # 验证 invoke 收到了 [SystemMessage, HumanMessage] 两条消息
    assert len(captured_messages) == 1
    assert len(captured_messages[0]) == 2
    assert isinstance(captured_messages[0][0], SystemMessage)
    assert isinstance(captured_messages[0][1], HumanMessage)


# ---------------------------------------------------------------------------
# 数据结构不可变性测试
# ---------------------------------------------------------------------------


def test_llm_config_is_frozen() -> None:
    """LlmConfig 应为不可变 dataclass。"""
    config = LlmConfig()
    with pytest.raises(AttributeError):
        config.timeout = 60.0  # type: ignore[misc]


def test_context_piece_is_frozen() -> None:
    """ContextPiece 应为不可变 dataclass。"""
    ctx = ContextPiece(
        document_name="a.pdf",
        start_page=1,
        end_page=1,
        chunk_index=0,
        content="x",
        score=0.5,
    )
    with pytest.raises(AttributeError):
        ctx.start_page = 2  # type: ignore[misc]


def test_citation_is_frozen() -> None:
    """Citation 应为不可变 dataclass。"""
    citation = Citation(document_name="a.pdf", start_page=1, end_page=1, snippet="x", score=0.5)
    with pytest.raises(AttributeError):
        citation.start_page = 2  # type: ignore[misc]


def test_answer_with_citations_is_frozen() -> None:
    """AnswerWithCitations 应为不可变 dataclass。"""
    answer = AnswerWithCitations(answer_text="x", citation_indices=[1], citations=[])
    with pytest.raises(AttributeError):
        answer.answer_text = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# retrieval_to_context 测试
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeRetrievalResult:
    """模拟 embedding.RetrievalResult（鸭子类型，字段同名即可）。"""

    start_page: int
    end_page: int
    chunk_index: int
    content: str
    score: float


def test_retrieval_to_context_basic() -> None:
    """应正确把 RetrievalResult 列表转换为 ContextPiece 列表。"""
    results: list[_FakeRetrievalResult] = [
        _FakeRetrievalResult(start_page=1, end_page=1, chunk_index=0, content="内容A", score=0.9),
        _FakeRetrievalResult(start_page=2, end_page=2, chunk_index=1, content="内容B", score=0.8),
    ]
    contexts = retrieval_to_context(results, "论文.pdf")

    assert len(contexts) == 2
    assert all(isinstance(c, ContextPiece) for c in contexts)
    assert contexts[0].document_name == "论文.pdf"
    assert contexts[0].start_page == 1
    assert contexts[0].end_page == 1
    assert contexts[0].chunk_index == 0
    assert contexts[0].content == "内容A"
    assert contexts[0].score == pytest.approx(0.9)
    assert contexts[1].start_page == 2


def test_retrieval_to_context_empty() -> None:
    """空列表应返回空列表。"""
    contexts = retrieval_to_context([], "论文.pdf")
    assert contexts == []


def test_retrieval_to_context_preserves_order() -> None:
    """顺序应与输入一致。"""
    results: list[_FakeRetrievalResult] = [
        _FakeRetrievalResult(start_page=3, end_page=3, chunk_index=2, content="C", score=0.7),
        _FakeRetrievalResult(start_page=1, end_page=1, chunk_index=0, content="A", score=0.9),
    ]
    contexts = retrieval_to_context(results, "论文.pdf")
    assert contexts[0].start_page == 3
    assert contexts[1].start_page == 1


# ---------------------------------------------------------------------------
# 端到端：retrieval_to_context → build_prompt → answer_question
# ---------------------------------------------------------------------------


def test_end_to_end_retrieval_to_answer() -> None:
    """端到端：检索结果 → 上下文 → Prompt → 答案 + 引用。"""
    results: list[_FakeRetrievalResult] = [
        _FakeRetrievalResult(
            start_page=3,
            end_page=3,
            chunk_index=5,
            content="Transformer 使用自注意力机制。",
            score=0.9,
        ),
        _FakeRetrievalResult(
            start_page=4,
            end_page=4,
            chunk_index=6,
            content="BERT 是基于 Transformer 的预训练模型。",
            score=0.85,
        ),
    ]
    contexts = retrieval_to_context(results, "attention.pdf")

    fake_model = FakeListChatModel(
        responses=["Transformer 使用自注意力机制 [C1]，BERT 基于此构建 [C2]。"]
    )
    result = answer_question("Transformer 是什么？", contexts, fake_model)

    assert result.citation_indices == [1, 2]
    assert len(result.citations) == 2
    # 真实引用映射：编号 → 文档名 + 页码 + 原文片段
    assert result.citations[0].document_name == "attention.pdf"
    assert result.citations[0].start_page == 3
    assert result.citations[0].end_page == 3
    assert "Transformer" in result.citations[0].snippet
    assert result.citations[1].start_page == 4
    assert result.citations[1].end_page == 4
    assert "BERT" in result.citations[1].snippet
