"""answer_evaluation 单元测试（阶段 9.3）。

测试覆盖：
- ``build_judge_prompt``：SystemMessage + HumanMessage 构造，含四项指标、
  上下文带 ``[C1]`` 编号、问题与答案文本正确嵌入。
- ``parse_judge_response``：JSON 解析、markdown fence、部分解析、字段缺失、
  分数越界 clamp、非数值 score、空响应、非对象 JSON。
- ``check_citations``：正常/越界/空/重复编号的客观校验。
- ``aggregate_judgements``：全部成功/部分失败/全部失败/空列表的均值与统计。
- ``judge_answer``：成功路径、LLM 异常、非字符串返回、解析失败（用
  ``FakeListChatModel`` 与自定义抛异常的假 ChatModel Mock，CI 不消耗真实 Token）。
- ``load_judge_config_from_env``：``JUDGE_LLM_*`` 优先级与回退到 ``LLM_*``。
- 数据结构不可变性。

外部 LLM 调用全部通过 ``FakeListChatModel`` 或自定义 ``_RaisingChatModel`` Mock，
CI 不消耗真实 Token，也不需要任何 API 密钥。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from typing_extensions import override

from research_rag.answer_evaluation import (
    MAX_SCORE,
    METRIC_CITATION,
    METRIC_COMPLETENESS,
    METRIC_FAITHFULNESS,
    METRIC_RELEVANCY,
    MIN_SCORE,
    AnswerEvaluationResult,
    AnswerJudgement,
    AnswerSample,
    CitationCheck,
    JudgeScores,
    MetricScore,
    aggregate_judgements,
    build_judge_prompt,
    check_citations,
    judge_answer,
    load_judge_config_from_env,
    parse_judge_response,
)
from research_rag.qa_service import ContextPiece

if TYPE_CHECKING:
    from langchain_core.callbacks import CallbackManagerForLLMRun
    from langchain_core.outputs import ChatResult


# ---------------------------------------------------------------------------
# 辅助：构造测试用的 ContextPiece 列表
# ---------------------------------------------------------------------------


def _make_contexts(n: int = 2) -> list[ContextPiece]:
    """构造 n 个 ContextPiece（默认 2 个），用于 judge prompt 与引用校验。"""
    contents = [
        "深度学习是机器学习的一个分支，使用多层神经网络。",
        "余弦相似度衡量两个向量方向的差异，常用于向量检索。",
        "BERT 是一种基于 Transformer 的预训练语言模型。",
    ]
    return [
        ContextPiece(
            document_name=f"论文{chr(ord('A') + i)}.pdf",
            start_page=i + 1,
            end_page=i + 1,
            chunk_index=i,
            content=contents[i % len(contents)],
            score=0.9 - i * 0.05,
        )
        for i in range(n)
    ]


def _make_sample(
    question: str = "深度学习是什么？",
    answer_text: str = "深度学习是机器学习分支，使用多层神经网络 [C1]。",
    citation_indices: list[int] | None = None,
    contexts: list[ContextPiece] | None = None,
) -> AnswerSample:
    """构造 AnswerSample（默认含 1 个引用编号、2 个上下文）。"""
    if contexts is None:
        contexts = _make_contexts(2)
    if citation_indices is None:
        citation_indices = [1]
    return AnswerSample(
        question=question,
        contexts=contexts,
        answer_text=answer_text,
        citation_indices=citation_indices,
    )


# ---------------------------------------------------------------------------
# 辅助：调用时抛异常的假 ChatModel（测试 judge LLM 失败路径）
# ---------------------------------------------------------------------------


class _RaisingChatModel(BaseChatModel):
    """测试用：调用时总是抛 RuntimeError 的假 ChatModel。

    继承 ``BaseChatModel`` 以满足 ``judge_answer`` 的类型约束，并在 ``_generate``
    中抛异常以模拟 judge LLM 服务故障（网络超时、5xx 等）。
    """

    @override
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg = "模拟 judge LLM 调用失败"
        raise RuntimeError(msg)

    @property
    @override
    def _llm_type(self) -> str:
        return "raising"


# ---------------------------------------------------------------------------
# build_judge_prompt 测试
# ---------------------------------------------------------------------------


class TestBuildJudgePrompt:
    def test_returns_system_and_human_message(self) -> None:
        """应返回 [SystemMessage, HumanMessage] 两条消息。"""
        sample = _make_sample()
        messages = build_judge_prompt(sample.question, sample.contexts, sample.answer_text)
        assert len(messages) == 2
        assert isinstance(messages[0], SystemMessage)
        assert isinstance(messages[1], HumanMessage)

    def test_system_message_contains_four_metrics(self) -> None:
        """SystemMessage 应包含四项指标名称。"""
        sample = _make_sample()
        messages = build_judge_prompt(sample.question, sample.contexts, sample.answer_text)
        system_text = messages[0].content
        assert isinstance(system_text, str)
        assert METRIC_FAITHFULNESS in system_text
        assert METRIC_RELEVANCY in system_text
        assert METRIC_COMPLETENESS in system_text
        assert METRIC_CITATION in system_text

    def test_system_message_contains_scoring_range(self) -> None:
        """SystemMessage 应说明 1-5 分评分范围与 JSON 输出要求。"""
        sample = _make_sample()
        messages = build_judge_prompt(sample.question, sample.contexts, sample.answer_text)
        system_text = messages[0].content
        assert isinstance(system_text, str)
        assert "1-5" in system_text
        assert "JSON" in system_text

    def test_human_message_contains_question(self) -> None:
        """HumanMessage 应包含用户问题。"""
        sample = _make_sample(question="BERT 是什么？")
        messages = build_judge_prompt(sample.question, sample.contexts, sample.answer_text)
        user_text = messages[1].content
        assert isinstance(user_text, str)
        assert "BERT 是什么？" in user_text

    def test_human_message_contains_answer(self) -> None:
        """HumanMessage 应包含答案文本。"""
        sample = _make_sample(answer_text="深度学习是机器学习分支 [C1]。")
        messages = build_judge_prompt(sample.question, sample.contexts, sample.answer_text)
        user_text = messages[1].content
        assert isinstance(user_text, str)
        assert "深度学习是机器学习分支 [C1]。" in user_text

    def test_human_message_contains_citation_numbered_contexts(self) -> None:
        """HumanMessage 上下文块应带 [C1]/[C2] 编号，与答案引用对应。"""
        contexts = _make_contexts(3)
        messages = build_judge_prompt("问题", contexts, "答案 [C2]。")
        user_text = messages[1].content
        assert isinstance(user_text, str)
        assert "[C1]" in user_text
        assert "[C2]" in user_text
        assert "[C3]" in user_text

    def test_context_order_preserved(self) -> None:
        """上下文顺序应与 [C1] 编号一致（[C1] 块先于 [C2] 块出现）。"""
        contexts = _make_contexts(3)
        messages = build_judge_prompt("问题", contexts, "答案")
        user_text = messages[1].content
        assert isinstance(user_text, str)
        assert user_text.index("[C1]") < user_text.index("[C2]")
        assert user_text.index("[C2]") < user_text.index("[C3]")
        # [C1] 块应包含 contexts[0] 的内容
        assert contexts[0].content in user_text

    def test_single_context(self) -> None:
        """单个上下文也应能构造 Prompt（不抛异常）。"""
        contexts = _make_contexts(1)
        messages = build_judge_prompt("问题", contexts, "答案 [C1]。")
        assert len(messages) == 2
        assert "[C1]" in messages[1].content

    def test_empty_contexts(self) -> None:
        """空上下文列表也应能构造 Prompt（不抛异常，judge 自行处理）。"""
        messages = build_judge_prompt("问题", [], "无引用答案。")
        assert len(messages) == 2

    def test_includes_insufficient_evidence_marker(self) -> None:
        """SystemMessage 应提及 [INSUFFICIENT_EVIDENCE] 标记的评分规则。"""
        sample = _make_sample()
        messages = build_judge_prompt(sample.question, sample.contexts, sample.answer_text)
        system_text = messages[0].content
        assert isinstance(system_text, str)
        assert "INSUFFICIENT_EVIDENCE" in system_text


# ---------------------------------------------------------------------------
# parse_judge_response 测试
# ---------------------------------------------------------------------------


class TestParseJudgeResponse:
    def test_valid_json_all_four_metrics(self) -> None:
        """标准 JSON 响应应解析出四项 MetricScore。"""
        content = (
            '{"faithfulness": {"score": 5, "reason": "全部有据"}, '
            '"relevancy": {"score": 4, "reason": "切题"}, '
            '"completeness": {"score": 3, "reason": "部分覆盖"}, '
            '"citation_correctness": {"score": 5, "reason": "引用正确"}}'
        )
        scores = parse_judge_response(content)
        assert scores.parse_error == ""
        assert scores.faithfulness is not None
        assert scores.faithfulness.score == 5.0
        assert scores.faithfulness.reason == "全部有据"
        assert scores.relevancy is not None
        assert scores.relevancy.score == 4.0
        assert scores.completeness is not None
        assert scores.completeness.score == 3.0
        assert scores.citation_correctness is not None
        assert scores.citation_correctness.score == 5.0

    def test_json_in_markdown_code_block(self) -> None:
        """JSON 包在 ```json ... ``` 代码块中也应解析成功。"""
        content = (
            "```json\n"
            '{"faithfulness": {"score": 4, "reason": "ok"}, '
            '"relevancy": {"score": 4, "reason": "ok"}, '
            '"completeness": {"score": 4, "reason": "ok"}, '
            '"citation_correctness": {"score": 4, "reason": "ok"}}\n'
            "```"
        )
        scores = parse_judge_response(content)
        assert scores.parse_error == ""
        assert scores.faithfulness is not None
        assert scores.faithfulness.score == 4.0

    def test_json_with_extra_text_around(self) -> None:
        """JSON 前后有解释性文本也应解析成功（正则提取 {} 区段）。"""
        content = (
            "好的，我来评分：\n"
            '{"faithfulness": {"score": 5, "reason": "a"}, '
            '"relevancy": {"score": 5, "reason": "b"}, '
            '"completeness": {"score": 5, "reason": "c"}, '
            '"citation_correctness": {"score": 5, "reason": "d"}}\n'
            "以上就是评分结果。"
        )
        scores = parse_judge_response(content)
        assert scores.parse_error == ""
        assert scores.faithfulness is not None
        assert scores.faithfulness.score == 5.0

    def test_partial_parse_missing_metric(self) -> None:
        """缺少某项指标的 JSON 应部分解析（缺失项为 None）。"""
        content = (
            '{"faithfulness": {"score": 5, "reason": "ok"}, '
            '"relevancy": {"score": 4, "reason": "ok"}}'
        )
        scores = parse_judge_response(content)
        assert scores.parse_error == ""
        assert scores.faithfulness is not None
        assert scores.relevancy is not None
        # 缺失项为 None，不计入汇总
        assert scores.completeness is None
        assert scores.citation_correctness is None

    def test_score_out_of_range_high_clamped(self) -> None:
        """超过 MAX_SCORE 的分数应 clamp 到 MAX_SCORE。"""
        content = (
            '{"faithfulness": {"score": 99, "reason": "x"}, '
            '"relevancy": {"score": 6, "reason": "x"}, '
            '"completeness": {"score": 5, "reason": "x"}, '
            '"citation_correctness": {"score": 5, "reason": "x"}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is not None
        assert scores.faithfulness.score == MAX_SCORE
        assert scores.relevancy is not None
        assert scores.relevancy.score == MAX_SCORE

    def test_score_out_of_range_low_clamped(self) -> None:
        """低于 MIN_SCORE 的分数应 clamp 到 MIN_SCORE。"""
        content = (
            '{"faithfulness": {"score": 0, "reason": "x"}, '
            '"relevancy": {"score": -3, "reason": "x"}, '
            '"completeness": {"score": 1, "reason": "x"}, '
            '"citation_correctness": {"score": 1, "reason": "x"}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is not None
        assert scores.faithfulness.score == MIN_SCORE
        assert scores.relevancy is not None
        assert scores.relevancy.score == MIN_SCORE

    def test_score_not_a_number(self) -> None:
        """score 字段非数值时该指标应为 None。"""
        content = (
            '{"faithfulness": {"score": "很好", "reason": "x"}, '
            '"relevancy": {"score": 4, "reason": "x"}, '
            '"completeness": {"score": 4, "reason": "x"}, '
            '"citation_correctness": {"score": 4, "reason": "x"}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is None
        assert scores.relevancy is not None
        assert scores.relevancy.score == 4.0

    def test_score_is_none(self) -> None:
        """score 为 null 时该指标应为 None。"""
        content = (
            '{"faithfulness": {"score": null, "reason": "x"}, '
            '"relevancy": {"score": 4, "reason": "x"}, '
            '"completeness": {"score": 4, "reason": "x"}, '
            '"citation_correctness": {"score": 4, "reason": "x"}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is None
        assert scores.relevancy is not None

    def test_metric_not_a_dict(self) -> None:
        """指标字段非 dict 时该指标应为 None。"""
        content = (
            '{"faithfulness": 5, '
            '"relevancy": {"score": 4, "reason": "x"}, '
            '"completeness": {"score": 4, "reason": "x"}, '
            '"citation_correctness": {"score": 4, "reason": "x"}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is None
        assert scores.relevancy is not None

    def test_missing_reason_defaults_empty(self) -> None:
        """reason 缺失时应默认空字符串（不抛异常）。"""
        content = (
            '{"faithfulness": {"score": 5}, '
            '"relevancy": {"score": 4}, '
            '"completeness": {"score": 4}, '
            '"citation_correctness": {"score": 4}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is not None
        assert scores.faithfulness.reason == ""

    def test_reason_is_none(self) -> None:
        """reason 为 null 时应转为空字符串。"""
        content = (
            '{"faithfulness": {"score": 5, "reason": null}, '
            '"relevancy": {"score": 4, "reason": "x"}, '
            '"completeness": {"score": 4, "reason": "x"}, '
            '"citation_correctness": {"score": 4, "reason": "x"}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is not None
        assert scores.faithfulness.reason == ""

    def test_malformed_json_sets_parse_error(self) -> None:
        """非合法 JSON 应设置 parse_error 且四项评分全 None。"""
        content = "这不是 JSON"
        scores = parse_judge_response(content)
        assert scores.parse_error != ""
        assert "JSON" in scores.parse_error or "解析" in scores.parse_error
        assert scores.faithfulness is None
        assert scores.relevancy is None
        assert scores.completeness is None
        assert scores.citation_correctness is None
        assert scores.raw_response == content

    def test_empty_string_content(self) -> None:
        """空字符串响应应设置 parse_error。"""
        scores = parse_judge_response("")
        assert scores.parse_error != ""
        assert scores.faithfulness is None

    def test_json_array_not_object(self) -> None:
        """JSON 数组（非对象，且不含 {} 供正则提取）应设置 parse_error。"""
        # 注意：_extract_json_object 用 \{.*\} 正则贪婪提取首个 {} 区段，
        # 所以 [{"score": 5}] 会被提取为 {"score": 5} 并解析成功（只是无指标字段）。
        # 要测试"非对象"分支，需用不含 {} 的纯数组。
        content = "[1, 2, 3]"
        scores = parse_judge_response(content)
        assert scores.parse_error != ""
        assert scores.faithfulness is None

    def test_score_as_float_string(self) -> None:
        """score 为字符串形式数字（如 '4.5'）也应能解析为 float。"""
        content = (
            '{"faithfulness": {"score": "4.5", "reason": "x"}, '
            '"relevancy": {"score": 4, "reason": "x"}, '
            '"completeness": {"score": 4, "reason": "x"}, '
            '"citation_correctness": {"score": 4, "reason": "x"}}'
        )
        scores = parse_judge_response(content)
        assert scores.faithfulness is not None
        assert scores.faithfulness.score == 4.5

    def test_raw_response_preserved_on_success(self) -> None:
        """解析成功时 raw_response 应保留原文。"""
        content = (
            '{"faithfulness": {"score": 5, "reason": "a"}, '
            '"relevancy": {"score": 5, "reason": "b"}, '
            '"completeness": {"score": 5, "reason": "c"}, '
            '"citation_correctness": {"score": 5, "reason": "d"}}'
        )
        scores = parse_judge_response(content)
        assert scores.raw_response == content


# ---------------------------------------------------------------------------
# check_citations 测试
# ---------------------------------------------------------------------------


class TestCheckCitations:
    def test_normal_in_bounds(self) -> None:
        """正常编号（1, 2）应 in_bounds=True，无越界。"""
        contexts = _make_contexts(3)
        check = check_citations([1, 2], contexts)
        assert check.has_citation is True
        assert check.in_bounds is True
        assert check.out_of_bounds_indices == ()
        assert check.unique_citation_count == 2

    def test_out_of_bounds_high(self) -> None:
        """编号超过 contexts 长度应记越界。"""
        contexts = _make_contexts(2)
        check = check_citations([1, 3], contexts)
        assert check.has_citation is True
        assert check.in_bounds is False
        assert 3 in check.out_of_bounds_indices
        assert check.unique_citation_count == 2

    def test_out_of_bounds_zero(self) -> None:
        """编号 0 应记越界（编号从 1 开始）。"""
        contexts = _make_contexts(2)
        check = check_citations([0, 1], contexts)
        assert check.in_bounds is False
        assert 0 in check.out_of_bounds_indices

    def test_out_of_bounds_negative(self) -> None:
        """负数编号应记越界。"""
        contexts = _make_contexts(2)
        check = check_citations([-1, 1], contexts)
        assert check.in_bounds is False
        assert -1 in check.out_of_bounds_indices

    def test_empty_citations(self) -> None:
        """空引用列表应 has_citation=False，in_bounds=True（无越界）。"""
        contexts = _make_contexts(2)
        check = check_citations([], contexts)
        assert check.has_citation is False
        assert check.in_bounds is True
        assert check.out_of_bounds_indices == ()
        assert check.unique_citation_count == 0

    def test_duplicates_deduped(self) -> None:
        """重复编号应去重，unique_citation_count 只算去重后数量。"""
        contexts = _make_contexts(3)
        check = check_citations([1, 1, 1, 2], contexts)
        assert check.unique_citation_count == 2
        assert check.in_bounds is True

    def test_all_out_of_bounds(self) -> None:
        """全部越界时 in_bounds=False，但 has_citation=True。"""
        contexts = _make_contexts(2)
        check = check_citations([5, 6], contexts)
        assert check.has_citation is True
        assert check.in_bounds is False
        assert check.out_of_bounds_indices == (5, 6)

    def test_empty_contexts_with_citations(self) -> None:
        """空 contexts + 有引用编号时所有编号都越界。"""
        check = check_citations([1], [])
        assert check.has_citation is True
        assert check.in_bounds is False
        assert 1 in check.out_of_bounds_indices

    def test_empty_contexts_no_citations(self) -> None:
        """空 contexts + 无引用编号时 has_citation=False。"""
        check = check_citations([], [])
        assert check.has_citation is False
        assert check.in_bounds is True


# ---------------------------------------------------------------------------
# aggregate_judgements 测试
# ---------------------------------------------------------------------------


def _make_judgement(
    question: str = "问题",
    faith: float | None = 4.0,
    rel: float | None = 4.0,
    comp: float | None = 4.0,
    cite: float | None = 4.0,
    parse_error: str = "",
) -> AnswerJudgement:
    """构造 AnswerJudgement，便于聚合测试。"""
    return AnswerJudgement(
        question=question,
        faithfulness=MetricScore(score=faith, reason="") if faith is not None else None,
        relevancy=MetricScore(score=rel, reason="") if rel is not None else None,
        completeness=MetricScore(score=comp, reason="") if comp is not None else None,
        citation_correctness=MetricScore(score=cite, reason="") if cite is not None else None,
        citation_check=CitationCheck(
            has_citation=True,
            in_bounds=True,
            out_of_bounds_indices=(),
            unique_citation_count=1,
        ),
        raw_response="",
        parse_error=parse_error,
    )


class TestAggregateJudgements:
    def test_all_successful(self) -> None:
        """全部成功时均值正确计算。"""
        judgements = [
            _make_judgement("q1", 5.0, 4.0, 3.0, 5.0),
            _make_judgement("q2", 3.0, 5.0, 4.0, 4.0),
        ]
        result = aggregate_judgements(judgements)
        assert result.num_questions == 2
        assert result.num_parse_errors == 0
        assert result.avg_faithfulness == pytest.approx(4.0)
        assert result.avg_relevancy == pytest.approx(4.5)
        assert result.avg_completeness == pytest.approx(3.5)
        assert result.avg_citation_correctness == pytest.approx(4.5)

    def test_partial_failures_excluded_from_average(self) -> None:
        """某题某项为 None 时该项均值只统计非 None 的题。"""
        judgements = [
            _make_judgement("q1", 5.0, 4.0, 3.0, None),
            _make_judgement("q2", 3.0, None, 4.0, 4.0),
        ]
        result = aggregate_judgements(judgements)
        # faithfulness: (5+3)/2 = 4.0
        assert result.avg_faithfulness == pytest.approx(4.0)
        # relevancy: 只有 q1 有 4.0，q2 为 None 跳过 → 4.0
        assert result.avg_relevancy == pytest.approx(4.0)
        # completeness: (3+4)/2 = 3.5
        assert result.avg_completeness == pytest.approx(3.5)
        # citation: 只有 q2 有 4.0，q1 为 None 跳过 → 4.0
        assert result.avg_citation_correctness == pytest.approx(4.0)

    def test_all_failed_averages_zero(self) -> None:
        """全部评分 None 时均值应为 0.0（避免除零）。"""
        judgements = [
            _make_judgement("q1", None, None, None, None, parse_error="err1"),
            _make_judgement("q2", None, None, None, None, parse_error="err2"),
        ]
        result = aggregate_judgements(judgements)
        assert result.num_questions == 2
        assert result.num_parse_errors == 2
        assert result.avg_faithfulness == 0.0
        assert result.avg_relevancy == 0.0
        assert result.avg_completeness == 0.0
        assert result.avg_citation_correctness == 0.0

    def test_empty_list(self) -> None:
        """空列表应返回 0 均值与 0 问题数。"""
        result = aggregate_judgements([])
        assert result.num_questions == 0
        assert result.num_parse_errors == 0
        assert result.avg_faithfulness == 0.0

    def test_per_question_preserved(self) -> None:
        """per_question 应保留输入顺序与内容。"""
        judgements = [
            _make_judgement("q1", 5.0, 4.0, 3.0, 5.0),
            _make_judgement("q2", 3.0, 5.0, 4.0, 4.0),
        ]
        result = aggregate_judgements(judgements)
        assert len(result.per_question) == 2
        assert result.per_question[0].question == "q1"
        assert result.per_question[1].question == "q2"

    def test_parse_error_count(self) -> None:
        """num_parse_errors 应统计 parse_error 非空的题数。"""
        judgements = [
            _make_judgement("q1", 5.0, 4.0, 3.0, 5.0),
            _make_judgement("q2", parse_error="失败"),
            _make_judgement("q3", 4.0, 4.0, 4.0, 4.0, parse_error="部分失败"),
        ]
        result = aggregate_judgements(judgements)
        assert result.num_parse_errors == 2


# ---------------------------------------------------------------------------
# judge_answer 测试（接触 LLM，用 FakeListChatModel Mock）
# ---------------------------------------------------------------------------


class TestJudgeAnswer:
    def test_successful_judgement(self) -> None:
        """正常 LLM 响应应解析为完整 AnswerJudgement。"""
        sample = _make_sample()
        response = (
            '{"faithfulness": {"score": 5, "reason": "全部有据"}, '
            '"relevancy": {"score": 4, "reason": "切题"}, '
            '"completeness": {"score": 4, "reason": "覆盖主要要点"}, '
            '"citation_correctness": {"score": 5, "reason": "引用正确"}}'
        )
        chat_model = FakeListChatModel(responses=[response])

        judgement = judge_answer(sample, chat_model)

        assert judgement.parse_error == ""
        assert judgement.faithfulness is not None
        assert judgement.faithfulness.score == 5.0
        assert judgement.faithfulness.reason == "全部有据"
        assert judgement.relevancy is not None
        assert judgement.relevancy.score == 4.0
        assert judgement.citation_check.has_citation is True
        assert judgement.citation_check.in_bounds is True

    def test_llm_exception_sets_parse_error(self) -> None:
        """LLM 调用抛异常时应降级为四项 None + parse_error。"""
        sample = _make_sample()
        chat_model = _RaisingChatModel()

        judgement = judge_answer(sample, chat_model)

        assert judgement.parse_error != ""
        assert "judge LLM 调用失败" in judgement.parse_error
        assert judgement.faithfulness is None
        assert judgement.relevancy is None
        assert judgement.completeness is None
        assert judgement.citation_correctness is None
        # 客观引用校验仍应执行（不依赖 LLM）
        assert judgement.citation_check.has_citation is True

    def test_parse_failure_sets_parse_error(self) -> None:
        """LLM 返回非 JSON 文本时应设置 parse_error。"""
        sample = _make_sample()
        chat_model = FakeListChatModel(responses=["这不是 JSON"])

        judgement = judge_answer(sample, chat_model)

        assert judgement.parse_error != ""
        assert judgement.faithfulness is None
        assert judgement.raw_response == "这不是 JSON"

    def test_partial_parse_in_judge_answer(self) -> None:
        """部分解析（缺一项）应保留已解析项，缺失项为 None。"""
        sample = _make_sample()
        response = (
            '{"faithfulness": {"score": 5, "reason": "ok"}, '
            '"relevancy": {"score": 4, "reason": "ok"}, '
            '"completeness": {"score": 4, "reason": "ok"}}'
        )
        chat_model = FakeListChatModel(responses=[response])

        judgement = judge_answer(sample, chat_model)

        assert judgement.parse_error == ""
        assert judgement.faithfulness is not None
        assert judgement.relevancy is not None
        assert judgement.completeness is not None
        # citation_correctness 缺失，应为 None
        assert judgement.citation_correctness is None

    def test_citation_check_independent_of_llm(self) -> None:
        """引用客观校验不依赖 LLM，即使 LLM 失败也应正确反映编号。"""
        sample = _make_sample(citation_indices=[1, 5])  # 5 越界（contexts 只有 2 个）
        chat_model = _RaisingChatModel()

        judgement = judge_answer(sample, chat_model)

        assert judgement.citation_check.has_citation is True
        assert judgement.citation_check.in_bounds is False
        assert 5 in judgement.citation_check.out_of_bounds_indices

    def test_question_preserved(self) -> None:
        """judgement.question 应等于 sample.question。"""
        sample = _make_sample(question="BERT 是什么？")
        chat_model = FakeListChatModel(responses=['{"faithfulness": {"score": 5, "reason": "x"}}'])

        judgement = judge_answer(sample, chat_model)

        assert judgement.question == "BERT 是什么？"

    def test_empty_citation_indices_in_sample(self) -> None:
        """sample 无引用编号时 citation_check.has_citation=False。"""
        sample = _make_sample(citation_indices=[])
        chat_model = FakeListChatModel(
            responses=[
                '{"faithfulness": {"score": 5, "reason": "x"}, '
                '"relevancy": {"score": 4, "reason": "x"}, '
                '"completeness": {"score": 4, "reason": "x"}, '
                '"citation_correctness": {"score": 1, "reason": "无引用"}}'
            ]
        )

        judgement = judge_answer(sample, chat_model)

        assert judgement.citation_check.has_citation is False
        assert judgement.citation_correctness is not None
        assert judgement.citation_correctness.score == 1.0


# ---------------------------------------------------------------------------
# load_judge_config_from_env 测试
# ---------------------------------------------------------------------------


class TestLoadJudgeConfigFromEnv:
    def test_judge_env_overrides_llm_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JUDGE_LLM_* 应优先于 LLM_*。"""
        monkeypatch.setenv("LLM_BASE_URL", "https://api.default.com")
        monkeypatch.setenv("LLM_API_KEY", "sk-default")
        monkeypatch.setenv("LLM_MODEL", "default-model")
        monkeypatch.setenv("JUDGE_LLM_BASE_URL", "https://api.judge.com")
        monkeypatch.setenv("JUDGE_LLM_API_KEY", "sk-judge")
        monkeypatch.setenv("JUDGE_LLM_MODEL", "judge-model")

        config = load_judge_config_from_env()

        assert config.base_url == "https://api.judge.com"
        assert config.api_key == "sk-judge"
        assert config.model == "judge-model"

    def test_fallback_to_llm_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设 JUDGE_LLM_* 时应回退 LLM_*。"""
        monkeypatch.delenv("JUDGE_LLM_BASE_URL", raising=False)
        monkeypatch.delenv("JUDGE_LLM_API_KEY", raising=False)
        monkeypatch.delenv("JUDGE_LLM_MODEL", raising=False)
        monkeypatch.setenv("LLM_BASE_URL", "https://api.default.com")
        monkeypatch.setenv("LLM_API_KEY", "sk-default")
        monkeypatch.setenv("LLM_MODEL", "default-model")

        config = load_judge_config_from_env()

        assert config.base_url == "https://api.default.com"
        assert config.api_key == "sk-default"
        assert config.model == "default-model"

    def test_judge_timeout_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JUDGE_LLM_TIMEOUT 应覆盖 LLM_TIMEOUT。"""
        monkeypatch.setenv("LLM_TIMEOUT", "30.0")
        monkeypatch.setenv("JUDGE_LLM_TIMEOUT", "60.0")

        config = load_judge_config_from_env()

        assert config.timeout == 60.0

    def test_judge_max_retries_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JUDGE_LLM_MAX_RETRIES 应覆盖 LLM_MAX_RETRIES。"""
        monkeypatch.setenv("LLM_MAX_RETRIES", "2")
        monkeypatch.setenv("JUDGE_LLM_MAX_RETRIES", "5")

        config = load_judge_config_from_env()

        assert config.max_retries == 5

    def test_default_timeout_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设任何 timeout 环境变量时应用默认值。"""
        monkeypatch.delenv("JUDGE_LLM_TIMEOUT", raising=False)
        monkeypatch.delenv("LLM_TIMEOUT", raising=False)

        config = load_judge_config_from_env()

        assert config.timeout == 30.0  # DEFAULT_LLM_TIMEOUT

    def test_empty_judge_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """JUDGE_LLM_* 设为空字符串时应回退 LLM_*。"""
        monkeypatch.setenv("JUDGE_LLM_BASE_URL", "")
        monkeypatch.setenv("JUDGE_LLM_API_KEY", "")
        monkeypatch.setenv("JUDGE_LLM_MODEL", "")
        monkeypatch.setenv("LLM_BASE_URL", "https://api.default.com")
        monkeypatch.setenv("LLM_API_KEY", "sk-default")
        monkeypatch.setenv("LLM_MODEL", "default-model")

        config = load_judge_config_from_env()

        # 空字符串 falsy，应回退到 LLM_*
        assert config.base_url == "https://api.default.com"
        assert config.api_key == "sk-default"
        assert config.model == "default-model"

    def test_all_empty_returns_empty_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """所有环境变量都未设时应返回空配置（调用方据此报错）。"""
        for var in (
            "JUDGE_LLM_BASE_URL",
            "JUDGE_LLM_API_KEY",
            "JUDGE_LLM_MODEL",
            "JUDGE_LLM_TIMEOUT",
            "JUDGE_LLM_MAX_RETRIES",
            "LLM_BASE_URL",
            "LLM_API_KEY",
            "LLM_MODEL",
            "LLM_TIMEOUT",
            "LLM_MAX_RETRIES",
        ):
            monkeypatch.delenv(var, raising=False)

        config = load_judge_config_from_env()

        assert config.base_url == ""
        assert config.api_key == ""
        assert config.model == ""
        assert config.timeout == 30.0
        assert config.max_retries == 2

    def test_malformed_timeout_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """格式错误的 timeout 应回退到默认值。"""
        monkeypatch.setenv("JUDGE_LLM_TIMEOUT", "not-a-number")
        monkeypatch.delenv("LLM_TIMEOUT", raising=False)

        config = load_judge_config_from_env()

        assert config.timeout == 30.0


# ---------------------------------------------------------------------------
# 数据结构不可变性
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_answer_sample_frozen(self) -> None:
        sample = _make_sample()
        with pytest.raises(AttributeError):
            sample.question = "modified"  # type: ignore[misc]

    def test_metric_score_frozen(self) -> None:
        score = MetricScore(score=4.0, reason="x")
        with pytest.raises(AttributeError):
            score.score = 1.0  # type: ignore[misc]

    def test_citation_check_frozen(self) -> None:
        check = CitationCheck(
            has_citation=True,
            in_bounds=True,
            out_of_bounds_indices=(),
            unique_citation_count=1,
        )
        with pytest.raises(AttributeError):
            check.has_citation = False  # type: ignore[misc]

    def test_judge_scores_frozen(self) -> None:
        scores = JudgeScores(
            faithfulness=None,
            relevancy=None,
            completeness=None,
            citation_correctness=None,
            raw_response="",
        )
        with pytest.raises(AttributeError):
            scores.raw_response = "modified"  # type: ignore[misc]

    def test_answer_judgement_frozen(self) -> None:
        j = _make_judgement()
        with pytest.raises(AttributeError):
            j.question = "modified"  # type: ignore[misc]

    def test_answer_evaluation_result_frozen(self) -> None:
        result = AnswerEvaluationResult(
            per_question=[],
            avg_faithfulness=4.0,
            avg_relevancy=4.0,
            avg_completeness=4.0,
            avg_citation_correctness=4.0,
            num_questions=1,
            num_parse_errors=0,
        )
        with pytest.raises(AttributeError):
            result.num_questions = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 常量与顺序
# ---------------------------------------------------------------------------


class TestConstants:
    def test_min_max_score_values(self) -> None:
        """MIN_SCORE=1, MAX_SCORE=5（与 prompt 一致）。"""
        assert MIN_SCORE == 1
        assert MAX_SCORE == 5

    def test_judge_metrics_order(self) -> None:
        """JUDGE_METRICS 顺序应固定（faithfulness → relevancy → completeness → citation）。"""
        from research_rag.answer_evaluation import JUDGE_METRICS

        assert JUDGE_METRICS == (
            METRIC_FAITHFULNESS,
            METRIC_RELEVANCY,
            METRIC_COMPLETENESS,
            METRIC_CITATION,
        )

    def test_metric_names_match_json_keys(self) -> None:
        """指标名应与 JSON 字段名一致（parse_judge_response 依赖）。"""
        assert METRIC_FAITHFULNESS == "faithfulness"
        assert METRIC_RELEVANCY == "relevancy"
        assert METRIC_COMPLETENESS == "completeness"
        assert METRIC_CITATION == "citation_correctness"
