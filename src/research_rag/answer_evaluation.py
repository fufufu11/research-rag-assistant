"""答案质量评测（LLM-as-judge，阶段 9.3）。

依据 docs/ROADMAP.md 阶段 9.3：扩展评测到生成阶段，量化 LLM 答案质量。

本模块用 LLM-as-judge 对（问题、上下文、答案）三元组打分，输出四项指标：
- **忠实度（faithfulness）**：答案中每个声明是否被上下文支持，防幻觉。
- **相关性（relevancy）**：答案是否直接回答了问题。
- **完整性（completeness）**：答案是否覆盖上下文中所有相关要点。
- **引用正确性（citation_correctness）**：答案中 ``[C1]`` 标记是否正确映射到
  支持该声明的上下文（项目特色指标，呼应可溯源设计目标）。

设计取舍（初学者向说明）：
- **LLM-as-judge 自实现而非 RAGAS**：复用 ``qa_service.create_chat_model``，
  无新依赖，与项目轻量风格一致；可定制项目特色指标（引用正确性），且避免
  RAGAS 与 langchain 1.x 的版本冲突风险。
- **纯函数与编排分离**：``build_judge_prompt`` / ``parse_judge_response`` /
  ``check_citations`` / ``aggregate_judgements`` 是纯函数（不接触 LLM），
  可被单元测试覆盖，无需真实 API Key；``judge_answer`` 是编排函数，调用 LLM。
  这与 ``evaluation.py``（纯指标函数）+ ``scripts/evaluate.py``（编排）的分层一致。
- **引用正确性 = LLM 主观评分 + 服务端客观校验**：judge LLM 主观判断引用是否
  对应支持性声明；``check_citations`` 客观校验编号是否越界、是否有引用。两者
  并列呈现，避免单纯依赖 LLM 的不可靠性。
- **judge 与 generator 可不同 LLM**：通过 ``JUDGE_LLM_*`` 环境变量覆盖，避免
  同模型自评偏差；未设置时回退主 ``LLM_*`` 配置（见 ``load_judge_config_from_env``）。
- **评分用 1-5 整数分**：比 0-1 连续值更易让 LLM 稳定输出，且符合多数 LLM-as-judge
  实践。汇总时取均值，粒度足够。
- **JSON 解析鲁棒**：LLM 可能输出 markdown 代码块或多余文本，用正则提取首个
  JSON 对象；解析失败时该题记 ``parse_error`` 并降级（不影响其他题评分）。
- 数据结构用 ``dataclass(frozen=True)``：与 ``evaluation.py`` / ``qa_service.py`` 一致。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from research_rag.qa_service import (
    DEFAULT_LLM_MAX_RETRIES,
    DEFAULT_LLM_TIMEOUT,
    LlmConfig,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import BaseMessage

    from research_rag.qa_service import ContextPiece

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 评分范围：1-5 分，5 分最好。LLM 对整数评分比连续值更稳定。
MIN_SCORE = 1
MAX_SCORE = 5

# 四项指标名（用于 JSON 字段名与汇总）。
METRIC_FAITHFULNESS = "faithfulness"
METRIC_RELEVANCY = "relevancy"
METRIC_COMPLETENESS = "completeness"
METRIC_CITATION = "citation_correctness"
# 顺序固定，保证报告展示与汇总字段顺序稳定。
JUDGE_METRICS: tuple[str, ...] = (
    METRIC_FAITHFULNESS,
    METRIC_RELEVANCY,
    METRIC_COMPLETENESS,
    METRIC_CITATION,
)

# 匹配首个 JSON 对象（贪婪 + DOTALL，覆盖多行对象）。LLM 输出可能带
# ```json fence 或前后多余文本，正则提取 {...} 区段。
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnswerSample:
    """单条答案质量评测样本（judge 的输入）。

    Attributes:
        question: 评测问题文本。
        contexts: 检索并重排后喂给生成模型的上下文片段列表（按相关度降序）。
            ``[C1]`` 编号即对应此列表顺序（与 ``qa_service.build_prompt`` 一致）。
        answer_text: 生成模型给出的答案文本（含 ``[C1]`` 等引用标记原文）。
        citation_indices: 答案中解析出的引用编号列表（从 1 开始，去重保序）。
    """

    question: str
    contexts: list[ContextPiece]
    answer_text: str
    citation_indices: list[int]


@dataclass(frozen=True)
class MetricScore:
    """单项指标得分。

    Attributes:
        score: 评分（1-5）。越界分数已在解析时 clamp 到合法范围。
        reason: judge 给出的简短理由（用于失败案例分析）。
    """

    score: float
    reason: str


@dataclass(frozen=True)
class CitationCheck:
    """引用正确性的客观规则校验结果（不调 LLM）。

    与 judge LLM 的主观 ``citation_correctness`` 评分互补：客观校验编号是否
    越界、是否至少有引用，避免单纯依赖 LLM 判断。

    Attributes:
        has_citation: 答案是否至少包含一个引用标记。
        in_bounds: 所有引用编号是否都在 contexts 范围内（无越界）。
        out_of_bounds_indices: 越界编号元组（编号 <=0 或 > len(contexts)）。
        unique_citation_count: 去重后的引用编号数量。
    """

    has_citation: bool
    in_bounds: bool
    out_of_bounds_indices: tuple[int, ...]
    unique_citation_count: int


@dataclass(frozen=True)
class JudgeScores:
    """judge LLM 响应解析结果（四项评分 + 原文 + 解析错误）。

    解析失败时四个评分为 ``None`` 且 ``parse_error`` 非空，调用方据此降级处理。

    Attributes:
        faithfulness: 忠实度评分，解析失败为 ``None``。
        relevancy: 相关性评分，解析失败为 ``None``。
        completeness: 完整性评分，解析失败为 ``None``。
        citation_correctness: 引用正确性评分，解析失败为 ``None``。
        raw_response: judge LLM 的原始响应文本（用于失败案例排查）。
        parse_error: 解析错误信息（空字符串表示解析成功）。
    """

    faithfulness: MetricScore | None
    relevancy: MetricScore | None
    completeness: MetricScore | None
    citation_correctness: MetricScore | None
    raw_response: str
    parse_error: str = ""


@dataclass(frozen=True)
class AnswerJudgement:
    """单条样本的完整判定结果（judge 评分 + 客观引用校验）。

    Attributes:
        question: 评测问题文本。
        faithfulness: 忠实度评分，judge 调用失败或解析失败时为 ``None``。
        relevancy: 相关性评分。
        completeness: 完整性评分。
        citation_correctness: 引用正确性评分（judge 主观）。
        citation_check: 引用编号客观规则校验（不依赖 LLM）。
        raw_response: judge LLM 的原始响应文本。
        parse_error: 解析或调用错误信息（空字符串表示无错误）。
    """

    question: str
    faithfulness: MetricScore | None
    relevancy: MetricScore | None
    completeness: MetricScore | None
    citation_correctness: MetricScore | None
    citation_check: CitationCheck
    raw_response: str
    parse_error: str = ""


@dataclass(frozen=True)
class AnswerEvaluationResult:
    """全部样本的汇总评测结果。

    均值仅统计成功解析的题（评分为 ``None`` 的题不计入分子分母），避免
    解析失败把整体分数拉低到不符合实际的水平。

    Attributes:
        per_question: 每条样本的判定明细（用于失败案例分析）。
        avg_faithfulness: 忠实度均值（仅统计成功评分的题，0 表示无成功题）。
        avg_relevancy: 相关性均值。
        avg_completeness: 完整性均值。
        avg_citation_correctness: 引用正确性均值。
        num_questions: 问题总数。
        num_parse_errors: 解析或调用失败的题数（parse_error 非空）。
    """

    per_question: list[AnswerJudgement]
    avg_faithfulness: float
    avg_relevancy: float
    avg_completeness: float
    avg_citation_correctness: float
    num_questions: int
    num_parse_errors: int


# ---------------------------------------------------------------------------
# judge prompt 构造（纯函数）
# ---------------------------------------------------------------------------


def build_judge_prompt(
    question: str,
    contexts: Sequence[ContextPiece],
    answer_text: str,
) -> list[BaseMessage]:
    """构造 judge 评分 Prompt（SystemMessage + HumanMessage）。

    SystemMessage 定义四项指标和 1-5 分评分标准，并强制 JSON 输出格式。
    HumanMessage 包含问题、带 ``[C1]`` 编号的上下文、答案。上下文编号与
    ``qa_service.build_prompt`` 一致，保证答案中的 ``[C1]`` 与此处上下文对应。

    Args:
        question: 评测问题文本。
        contexts: 检索并重排后的上下文片段列表（顺序与 ``[C1]`` 编号对应）。
        answer_text: 生成模型的答案文本（含 ``[C1]`` 引用标记）。

    Returns:
        LangChain 消息列表，可直接传给 ``chat_model.invoke``。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    system_prompt = (
        "你是一个严谨的科研问答答案质量评测员。请对下面给出的「问题」「上下文」"
        "「答案」三元组，从四个维度评分（1-5 分，5 分最好，1 分最差），并给出"
        "简短理由。问题、上下文、答案可能是中文或英文，请用与答案相同的语言写理由。\n\n"
        "评分维度：\n"
        "1. faithfulness（忠实度）：答案中的每个声明是否都能被「上下文」支持。"
        "5 分=全部有据可依、无幻觉；3 分=部分声明无据；1 分=大量编造或与上下文矛盾。\n"
        "2. relevancy（相关性）：答案是否直接回答了「问题」。"
        "5 分=完全切题；3 分=部分回答；1 分=完全跑题。\n"
        "3. completeness（完整性）：答案是否覆盖了「上下文」中与问题相关的所有要点。"
        "5 分=完整覆盖；3 分=覆盖主要要点；1 分=严重遗漏关键信息。\n"
        "4. citation_correctness（引用正确性）：答案中的 [C1]/[C2] 等引用标记是否"
        "正确指向支持该声明的上下文片段。5 分=引用全部正确且充分；3 分=部分引用"
        "不当；1 分=无引用或引用全错。若证据不足导致答案为 [INSUFFICIENT_EVIDENCE]，"
        "该项给 1 分。\n\n"
        "输出要求：只输出一个 JSON 对象，不要任何额外文本或代码块标记。格式：\n"
        '{"faithfulness": {"score": 1, "reason": "..."}, '
        '"relevancy": {"score": 1, "reason": "..."}, '
        '"completeness": {"score": 1, "reason": "..."}, '
        '"citation_correctness": {"score": 1, "reason": "..."}}'
    )

    # 上下文带 [C1] 编号，与生成阶段 build_prompt 的编号一致，让 judge 能核对
    # 答案中的引用标记是否对应正确的上下文片段。不带文档名/页码（judge 不需要，
    # 且避免 prompt 过长）。
    context_block = "\n\n".join(f"[C{i + 1}] {ctx.content}" for i, ctx in enumerate(contexts))

    user_prompt = (
        f"问题：{question}\n\n"
        f"上下文：\n{context_block}\n\n"
        f"答案：{answer_text}\n\n"
        "请按上述格式输出 JSON 评分。"
    )

    return [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]


# ---------------------------------------------------------------------------
# 评分解析（纯函数）
# ---------------------------------------------------------------------------


def _extract_json_object(text: str) -> str:
    """从可能含 markdown fence 或多余文本的输出中提取首个 JSON 对象文本。

    LLM 常把 JSON 包在 ```json ... ``` 里，或在前后加解释性文字。正则贪婪匹配
    第一个 ``{`` 到最后一个 ``}`` 的区段。对单个 JSON 对象的场景足够。

    Args:
        text: LLM 原始输出。

    Returns:
        提取出的 JSON 对象文本；未匹配到时返回原文本（交由 ``json.loads`` 报错）。
    """
    match = _JSON_OBJECT_PATTERN.search(text)
    return match.group(0) if match else text


def _clamp_score(score: float) -> float:
    """把分数 clamp 到 [MIN_SCORE, MAX_SCORE] 范围。"""
    return max(float(MIN_SCORE), min(float(MAX_SCORE), score))


def _parse_metric(obj: dict[str, object], name: str) -> MetricScore | None:
    """从 JSON 对象解析单项指标评分。

    容错策略：score 缺失/非数值/为 None 时返回 ``None``（交由上层记 parse_error
    或降级）；reason 缺失时记空字符串。score 越界时 clamp 到合法范围而非丢弃，
    避免因 LLM 输出 6 分就整题作废。

    Args:
        obj: ``json.loads`` 得到的字典。
        name: 指标名（如 ``"faithfulness"``）。

    Returns:
        解析成功的 ``MetricScore``；该字段缺失或 score 不可解析时返回 ``None``。
    """
    item = obj.get(name)
    if not isinstance(item, dict):
        return None
    raw_score = item.get("score")
    try:
        score = float(raw_score)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    reason = item.get("reason", "")
    return MetricScore(score=_clamp_score(score), reason=str(reason) if reason is not None else "")


def parse_judge_response(content: str) -> JudgeScores:
    """解析 judge LLM 的 JSON 响应为四项评分。

    解析流程：提取 JSON 对象 → ``json.loads`` → 逐项解析 ``_parse_metric``。
    任何步骤失败都返回四个 ``None`` 评分 + ``parse_error``，不抛异常（让调用方
    据此降级处理，不影响其他题）。

    部分解析成功（如四项中三项有效）是允许的：缺失项记 ``None``，汇总时跳过。

    Args:
        content: judge LLM 的原始响应文本。

    Returns:
        ``JudgeScores``：四项评分（解析失败的为 ``None``）+ 原文 + parse_error。
    """
    raw = content
    try:
        json_str = _extract_json_object(content)
        obj = json.loads(json_str)
    except (json.JSONDecodeError, ValueError) as exc:
        return JudgeScores(
            faithfulness=None,
            relevancy=None,
            completeness=None,
            citation_correctness=None,
            raw_response=raw,
            parse_error=f"JSON 解析失败：{exc}",
        )

    if not isinstance(obj, dict):
        return JudgeScores(
            faithfulness=None,
            relevancy=None,
            completeness=None,
            citation_correctness=None,
            raw_response=raw,
            parse_error="响应不是 JSON 对象",
        )

    return JudgeScores(
        faithfulness=_parse_metric(obj, METRIC_FAITHFULNESS),
        relevancy=_parse_metric(obj, METRIC_RELEVANCY),
        completeness=_parse_metric(obj, METRIC_COMPLETENESS),
        citation_correctness=_parse_metric(obj, METRIC_CITATION),
        raw_response=raw,
    )


# ---------------------------------------------------------------------------
# 引用正确性客观校验（纯函数，不调 LLM）
# ---------------------------------------------------------------------------


def check_citations(
    citation_indices: Sequence[int],
    contexts: Sequence[ContextPiece],
) -> CitationCheck:
    """客观规则校验引用编号（不调 LLM）。

    与 judge LLM 的主观 ``citation_correctness`` 评分互补：客观校验编号是否
    越界、是否至少有引用，提供不依赖 LLM 的基线信号。

    Args:
        citation_indices: 答案中解析出的引用编号列表（可能含重复）。
        contexts: 生成时使用的上下文片段列表（编号从 1 开始，与列表顺序对应）。

    Returns:
        ``CitationCheck``：是否有引用、是否全部在范围内、越界编号、去重数量。
    """
    # 去重保序（与 qa_service.parse_citation_indices 的去重策略一致）
    seen: set[int] = set()
    unique: list[int] = []
    for idx in citation_indices:
        if idx not in seen:
            seen.add(idx)
            unique.append(idx)

    out_of_bounds = tuple(idx for idx in unique if idx <= 0 or idx > len(contexts))
    return CitationCheck(
        has_citation=len(unique) > 0,
        in_bounds=not out_of_bounds,
        out_of_bounds_indices=out_of_bounds,
        unique_citation_count=len(unique),
    )


# ---------------------------------------------------------------------------
# judge 编排（接触 LLM）
# ---------------------------------------------------------------------------


def _judgement_from_failure(
    sample: AnswerSample,
    citation_check: CitationCheck,
    raw_response: str,
    parse_error: str,
) -> AnswerJudgement:
    """构造失败题的 ``AnswerJudgement``（四项评分全 None）。"""
    return AnswerJudgement(
        question=sample.question,
        faithfulness=None,
        relevancy=None,
        completeness=None,
        citation_correctness=None,
        citation_check=citation_check,
        raw_response=raw_response,
        parse_error=parse_error,
    )


def judge_answer(sample: AnswerSample, chat_model: BaseChatModel) -> AnswerJudgement:
    """对单条样本调用 judge LLM 并组装完整判定。

    流程：构造 prompt → 客观引用校验 → 调用 judge LLM → 解析评分 → 组装。
    任何异常（LLM 调用失败、返回非字符串、解析失败）都降级为四项 ``None`` 评分
    + ``parse_error``，不抛异常（保证单题失败不影响其他题评测）。

    Args:
        sample: 待评测样本（问题、上下文、答案、引用编号）。
        chat_model: judge 用的 LangChain ``BaseChatModel`` 实例。

    Returns:
        ``AnswerJudgement``：四项评分（失败时为 ``None``）+ 客观引用校验 + 原文。
    """
    messages = build_judge_prompt(sample.question, sample.contexts, sample.answer_text)
    citation_check = check_citations(sample.citation_indices, sample.contexts)

    try:
        response = chat_model.invoke(messages)
    except Exception as exc:
        return _judgement_from_failure(
            sample, citation_check, raw_response="", parse_error=f"judge LLM 调用失败：{exc}"
        )

    content = getattr(response, "content", None)
    if not isinstance(content, str):
        return _judgement_from_failure(
            sample, citation_check, raw_response="", parse_error="judge LLM 返回非字符串"
        )

    scores = parse_judge_response(content)
    return AnswerJudgement(
        question=sample.question,
        faithfulness=scores.faithfulness,
        relevancy=scores.relevancy,
        completeness=scores.completeness,
        citation_correctness=scores.citation_correctness,
        citation_check=citation_check,
        raw_response=scores.raw_response,
        parse_error=scores.parse_error,
    )


# ---------------------------------------------------------------------------
# 汇总（纯函数）
# ---------------------------------------------------------------------------


def _avg(values: Sequence[float]) -> float:
    """计算均值，空列表返回 0.0。"""
    return sum(values) / len(values) if values else 0.0


def aggregate_judgements(
    judgements: Sequence[AnswerJudgement],
) -> AnswerEvaluationResult:
    """把多条判定汇总为整体评测结果。

    均值仅统计成功解析的题（评分为 ``None`` 的题不计入分子分母），避免
    解析失败把整体分数拉低到不符合实际的水平。

    Args:
        judgements: 每条样本的 ``AnswerJudgement``。

    Returns:
        ``AnswerEvaluationResult``：含均值统计、问题总数、解析失败数。
    """
    faith_scores = [j.faithfulness.score for j in judgements if j.faithfulness is not None]
    rel_scores = [j.relevancy.score for j in judgements if j.relevancy is not None]
    comp_scores = [j.completeness.score for j in judgements if j.completeness is not None]
    cite_scores = [
        j.citation_correctness.score for j in judgements if j.citation_correctness is not None
    ]

    return AnswerEvaluationResult(
        per_question=list(judgements),
        avg_faithfulness=_avg(faith_scores),
        avg_relevancy=_avg(rel_scores),
        avg_completeness=_avg(comp_scores),
        avg_citation_correctness=_avg(cite_scores),
        num_questions=len(judgements),
        num_parse_errors=sum(1 for j in judgements if j.parse_error),
    )


# ---------------------------------------------------------------------------
# 配置加载（从环境变量）
# ---------------------------------------------------------------------------


def _parse_float(value: str, default: float) -> float:
    """把字符串解析为 float，空串或格式错误返回 default。"""
    if not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_int(value: str, default: int) -> int:
    """把字符串解析为 int，空串或格式错误返回 default。"""
    if not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def load_judge_config_from_env() -> LlmConfig:
    """从环境变量加载 judge LLM 配置。

    优先读 ``JUDGE_LLM_*`` 环境变量（允许 judge 用与 generator 不同的 LLM，
    避免同模型自评偏差）；未设置时回退到主 ``LLM_*`` 配置（与 generator 同一 LLM）。

    环境变量：
        - ``JUDGE_LLM_BASE_URL`` / ``JUDGE_LLM_API_KEY`` / ``JUDGE_LLM_MODEL``：
          judge 专属配置（可选）。未设置时回退 ``LLM_BASE_URL`` / ``LLM_API_KEY``
          / ``LLM_MODEL``。
        - ``JUDGE_LLM_TIMEOUT`` / ``JUDGE_LLM_MAX_RETRIES``：超时与重试（可选），
          回退 ``LLM_TIMEOUT`` / ``LLM_MAX_RETRIES``，再回退模块默认值。

    Returns:
        ``LlmConfig``：可直接传给 ``qa_service.create_chat_model``。
    """
    base_url = os.environ.get("JUDGE_LLM_BASE_URL") or os.environ.get("LLM_BASE_URL", "")
    api_key = os.environ.get("JUDGE_LLM_API_KEY") or os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("JUDGE_LLM_MODEL") or os.environ.get("LLM_MODEL", "")
    timeout = _parse_float(
        os.environ.get("JUDGE_LLM_TIMEOUT", "") or os.environ.get("LLM_TIMEOUT", ""),
        DEFAULT_LLM_TIMEOUT,
    )
    max_retries = _parse_int(
        os.environ.get("JUDGE_LLM_MAX_RETRIES", "") or os.environ.get("LLM_MAX_RETRIES", ""),
        DEFAULT_LLM_MAX_RETRIES,
    )
    return LlmConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        max_retries=max_retries,
    )
