"""检索评测指标与数据集加载。

依据 PROJECT_PLAN.md 第 724-730 行（阶段 7 交付物与验收）。

本模块只实现**指标计算**与**数据集加载**的纯函数，不接触真实 Embedding /
向量存储。评测脚本（``scripts/evaluate.py``）负责编排"切分→索引→检索"，
把检索结果传给本模块计算指标。这样指标逻辑可被单元测试覆盖，无需安装
``sentence-transformers``。

设计取舍（初学者向说明）：
- **只评测检索阶段**：阶段 7 的核心是"检索失败 vs 生成失败"的区分。生成阶段
  评测需消耗 LLM Token 且答案质量主观，留给后续。本模块所有指标都基于
  检索结果排序，不调用 LLM。
- **ground truth 用 ``expected_substring`` 而非 ``chunk_index``**：当参数对比
  改变 ``chunk_size`` 时，``chunk_index`` 会变化，但正确答案所在的文本片段
  内容不变。用一段独特的子串匹配，对 ``chunk_size`` / ``chunk_overlap`` 变化
  鲁棒。
- **子串匹配前做空白归一化**：PyMuPDF 提取中文时常在字间插入空格（如
  ``活 动的``），不同 ``chunk_size`` 下空格位置可能不同。归一化时移除所有
  空白字符再做 ``in`` 判断，避免空格导致误判。
- **Hit@K 的 K 取 1 和 5**：Hit@1 反映"最相关是否排第一"（直接影响 LLM
  引用质量），Hit@5 反映"召回上限"。MRR 衡量平均排名质量，比 Hit@K 更细粒度。
- ``EvaluationEntry`` / ``ExperimentConfig`` / ``MetricResult`` 用
  ``dataclass(frozen=True)``：与项目其他模块一致，不可变，避免下游意外修改。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from research_rag.embedding import RetrievalResult


# 评测指标计算用的 K 值（PROJECT_PLAN.md 第 730 行验收要求 Hit@1、Hit@5）
HIT_K_VALUES: tuple[int, ...] = (1, 5)
# 评测脚本默认检索数量（取 max(K) 保证 Hit@5 可计算）
DEFAULT_EVAL_TOP_K = 5


@dataclass(frozen=True)
class EvaluationEntry:
    """单条评测数据（question → ground truth）。

    Attributes:
        question: 评测问题文本。
        expected_page: 预期答案所在页码（1 开始，与 ``PageInfo.page_number``
            一致）。仅作记录与人工核查用，不参与匹配判定（匹配用
            ``expected_substring``）。
        expected_substring: 预期答案所在 chunk 的一段独特子串。匹配时对
            子串和 chunk 内容做空白归一化后判断包含关系，对 ``chunk_size``
            变化和 PDF 提取空格鲁棒。
        category: 问题分类（如"定义"/"方法"/"挑战"），用于分桶分析。
        note: 可选备注（人工标注说明）。
    """

    question: str
    expected_page: int
    expected_substring: str
    category: str = ""
    note: str = ""


@dataclass(frozen=True)
class ExperimentConfig:
    """单组实验参数。

    用于参数对比（PROJECT_PLAN.md 第 726 行"至少两组参数对比"）。

    Attributes:
        name: 实验名称（如 ``chunk-500-overlap-80``），用于结果表格展示。
        chunk_size: 切分片段最大字符数。
        chunk_overlap: 相邻片段重叠字符数。
        top_k: 检索返回的最相关片段数。应 >= ``max(HIT_K_VALUES)`` 以保证
            Hit@5 可计算。
        description: 实验说明（对比维度的简述）。
    """

    name: str
    chunk_size: int
    chunk_overlap: int
    top_k: int = DEFAULT_EVAL_TOP_K
    description: str = ""


@dataclass(frozen=True)
class QueryMetrics:
    """单条 query 的评测结果。

    Attributes:
        question: 问题文本。
        hit_at_1: Top-1 是否命中。
        hit_at_5: Top-5 是否命中。
        reciprocal_rank: 首个命中结果的倒数排名（无命中则为 0.0）。
        latency_ms: 本次检索的 wall-clock 耗时（毫秒）。
        first_hit_rank: 首个命中结果的排名（1 开始，无命中则为 0）。
    """

    question: str
    hit_at_1: bool
    hit_at_5: bool
    reciprocal_rank: float
    latency_ms: float
    first_hit_rank: int


@dataclass(frozen=True)
class MetricResult:
    """一组实验的汇总指标。

    Attributes:
        experiment_name: 实验名称。
        num_questions: 问题总数。
        hit_at_1: Hit@1（Top-1 命中率，0-1）。
        hit_at_5: Hit@5（Top-5 命中率，0-1）。
        mrr: 平均倒数排名（0-1）。
        avg_latency_ms: 平均检索耗时（毫秒）。
        chunk_count: 本实验切分产生的 chunk 总数（反映粒度）。
        per_query: 每条 query 的明细（用于分析失败 case）。
    """

    experiment_name: str
    num_questions: int
    hit_at_1: float
    hit_at_5: float
    mrr: float
    avg_latency_ms: float
    chunk_count: int = 0
    per_query: list[QueryMetrics] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """移除所有空白字符，用于子串匹配前的归一化。

    PyMuPDF 提取中文时常在字间插入空格（如 ``活 动的``），不同切分参数下
    空格位置可能不同。移除所有空白后做 ``in`` 判断可避免误判。

    Args:
        text: 原始文本。

    Returns:
        移除所有空白字符（空格、制表符、换行等）后的文本。
    """
    return re.sub(r"\s+", "", text)


def is_hit(result_content: str, expected_substring: str) -> bool:
    """判断单条检索结果是否命中 ground truth。

    对 ``result_content`` 和 ``expected_substring`` 做空白归一化后判断包含
    关系。``expected_substring`` 应是一段足够独特的子串（10+ 字符），确保
    只匹配正确 chunk。

    Args:
        result_content: 检索结果片段的文本内容。
        expected_substring: ground truth 子串。

    Returns:
        归一化后 ``expected_substring`` 是否为 ``result_content`` 的子串。
    """
    return normalize_text(expected_substring) in normalize_text(result_content)


def hit_at_k(
    results: Sequence[RetrievalResult],
    expected_substring: str,
    k: int,
) -> bool:
    """判断 Top-K 结果中是否有命中。

    Args:
        results: 检索结果列表（按相关度降序）。
        expected_substring: ground truth 子串。
        k: 取前 K 条结果判断。

    Returns:
        前 K 条结果中是否有任意一条命中。
    """
    return any(is_hit(result.content, expected_substring) for result in results[:k])


def reciprocal_rank(
    results: Sequence[RetrievalResult],
    expected_substring: str,
) -> float:
    """计算倒数排名（Reciprocal Rank）。

    首个命中结果的排名的倒数（排名从 1 开始）。无命中则为 0.0。

    Args:
        results: 检索结果列表（按相关度降序）。
        expected_substring: ground truth 子串。

    Returns:
        1/rank（如首个命中在第 3 位则返回 1/3），无命中返回 0.0。
    """
    for i, result in enumerate(results):
        if is_hit(result.content, expected_substring):
            return 1.0 / (i + 1)
    return 0.0


def first_hit_rank(
    results: Sequence[RetrievalResult],
    expected_substring: str,
) -> int:
    """返回首个命中结果的排名（1 开始），无命中返回 0。

    用于失败 case 分析（了解正确答案排在第几位或完全未召回）。
    """
    for i, result in enumerate(results):
        if is_hit(result.content, expected_substring):
            return i + 1
    return 0


def compute_query_metrics(
    question: str,
    results: Sequence[RetrievalResult],
    expected_substring: str,
    latency_ms: float,
) -> QueryMetrics:
    """计算单条 query 的指标。

    Args:
        question: 问题文本。
        results: 检索结果列表（按相关度降序）。
        expected_substring: ground truth 子串。
        latency_ms: 本次检索耗时（毫秒）。

    Returns:
        该 query 的 ``QueryMetrics``。
    """
    return QueryMetrics(
        question=question,
        hit_at_1=hit_at_k(results, expected_substring, 1),
        hit_at_5=hit_at_k(results, expected_substring, 5),
        reciprocal_rank=reciprocal_rank(results, expected_substring),
        latency_ms=latency_ms,
        first_hit_rank=first_hit_rank(results, expected_substring),
    )


def aggregate_metrics(
    experiment_name: str,
    per_query: Sequence[QueryMetrics],
    chunk_count: int = 0,
) -> MetricResult:
    """把多条 query 的指标汇总为实验级指标。

    Args:
        experiment_name: 实验名称。
        per_query: 每条 query 的 ``QueryMetrics``。
        chunk_count: 本实验切分产生的 chunk 总数。

    Returns:
        汇总后的 ``MetricResult``。
    """
    n = len(per_query)
    if n == 0:
        return MetricResult(
            experiment_name=experiment_name,
            num_questions=0,
            hit_at_1=0.0,
            hit_at_5=0.0,
            mrr=0.0,
            avg_latency_ms=0.0,
            chunk_count=chunk_count,
            per_query=[],
        )

    hit1 = sum(1 for q in per_query if q.hit_at_1) / n
    hit5 = sum(1 for q in per_query if q.hit_at_5) / n
    mrr = sum(q.reciprocal_rank for q in per_query) / n
    avg_lat = sum(q.latency_ms for q in per_query) / n
    return MetricResult(
        experiment_name=experiment_name,
        num_questions=n,
        hit_at_1=hit1,
        hit_at_5=hit5,
        mrr=mrr,
        avg_latency_ms=avg_lat,
        chunk_count=chunk_count,
        per_query=list(per_query),
    )


def load_dataset(path: str | Path) -> list[EvaluationEntry]:
    """从 JSON 文件加载评测数据集。

    JSON 格式：数组，每个元素含 ``question`` / ``expected_page`` /
    ``expected_substring`` / ``category``（可选）/ ``note``（可选）。

    Args:
        path: JSON 文件路径。

    Returns:
        评测数据条目列表。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: JSON 格式错误或缺少必填字段。
    """
    path = Path(path)
    if not path.exists():
        msg = f"评测数据集文件不存在: {path}"
        raise FileNotFoundError(msg)

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        msg = f"评测数据集应为 JSON 数组，收到 {type(data).__name__}"
        raise ValueError(msg)

    entries: list[EvaluationEntry] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            msg = f"第 {i + 1} 条数据应为 JSON 对象，收到 {type(item).__name__}"
            raise ValueError(msg)
        try:
            entries.append(
                EvaluationEntry(
                    question=item["question"],
                    expected_page=int(item["expected_page"]),
                    expected_substring=item["expected_substring"],
                    category=item.get("category", ""),
                    note=item.get("note", ""),
                )
            )
        except KeyError as exc:
            msg = f"第 {i + 1} 条数据缺少必填字段: {exc}"
            raise ValueError(msg) from exc

    return entries


# 默认实验配置（PROJECT_PLAN.md 第 726 行"至少两组参数对比"）
# 维度 1：chunk_size（300 / 500 / 800），控制片段粒度
# 维度 2：chunk_overlap（0 / 80 / 160），控制边界重叠
# top_k 统一取 5（= max(HIT_K_VALUES)），保证 Hit@5 可计算
DEFAULT_EXPERIMENTS: list[ExperimentConfig] = [
    ExperimentConfig(
        name="chunk-300-overlap-50",
        chunk_size=300,
        chunk_overlap=50,
        top_k=DEFAULT_EVAL_TOP_K,
        description="小片段：粒度细，chunk 数量多，单片段信息少",
    ),
    ExperimentConfig(
        name="chunk-500-overlap-0",
        chunk_size=500,
        chunk_overlap=0,
        top_k=DEFAULT_EVAL_TOP_K,
        description="中片段无重叠：对比 overlap 的影响",
    ),
    ExperimentConfig(
        name="chunk-500-overlap-80",
        chunk_size=500,
        chunk_overlap=80,
        top_k=DEFAULT_EVAL_TOP_K,
        description="基线（项目默认参数）",
    ),
    ExperimentConfig(
        name="chunk-500-overlap-160",
        chunk_size=500,
        chunk_overlap=160,
        top_k=DEFAULT_EVAL_TOP_K,
        description="中片段高重叠：对比 overlap 的影响",
    ),
    ExperimentConfig(
        name="chunk-800-overlap-100",
        chunk_size=800,
        chunk_overlap=100,
        top_k=DEFAULT_EVAL_TOP_K,
        description="大片段：粒度粗，chunk 数量少，单片段信息多",
    ),
]
