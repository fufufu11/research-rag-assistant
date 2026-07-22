"""检索评测指标模块单元测试。

测试覆盖（PROJECT_PLAN.md 第 724-730 行阶段 7 验收）：
- normalize_text：空白归一化
- is_hit：归一化子串匹配
- hit_at_k：Top-K 命中判定
- reciprocal_rank：倒数排名计算
- first_hit_rank：首个命中排名
- compute_query_metrics：单条 query 指标
- aggregate_metrics：实验级汇总
- load_dataset：JSON 加载与错误处理
- dataclass 不可变性
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_rag.embedding import RetrievalResult
from research_rag.evaluation import (
    DEFAULT_EVAL_TOP_K,
    DEFAULT_EXPERIMENTS,
    EvaluationEntry,
    ExperimentConfig,
    MetricResult,
    QueryMetrics,
    aggregate_metrics,
    compute_query_metrics,
    first_hit_rank,
    hit_at_k,
    is_hit,
    load_dataset,
    normalize_text,
    reciprocal_rank,
)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_result(page: int, chunk_index: int, content: str, score: float = 0.0) -> RetrievalResult:
    """构造 RetrievalResult。"""
    return RetrievalResult(
        page_number=page,
        chunk_index=chunk_index,
        content=content,
        score=score,
    )


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


class TestNormalizeText:
    def test_removes_spaces(self) -> None:
        assert normalize_text("活 动的") == "活动的"

    def test_removes_newlines_and_tabs(self) -> None:
        assert normalize_text("a\nb\tc") == "abc"

    def test_removes_all_whitespace(self) -> None:
        assert normalize_text("  a b \n c \t d  ") == "abcd"

    def test_empty_string(self) -> None:
        assert normalize_text("") == ""

    def test_no_whitespace(self) -> None:
        assert normalize_text("脑纹识别") == "脑纹识别"


# ---------------------------------------------------------------------------
# is_hit
# ---------------------------------------------------------------------------


class TestIsHit:
    def test_exact_match(self) -> None:
        assert is_hit("脑纹识别以脑电图", "脑纹识别以脑电图") is True

    def test_substring_match(self) -> None:
        assert is_hit("脑纹识别以脑电图信号为输入", "脑纹识别以脑电图") is True

    def test_normalized_match(self) -> None:
        assert is_hit("脑纹 识别 以 脑电图", "脑纹识别以脑电图") is True

    def test_no_match(self) -> None:
        assert is_hit("深度学习方法", "脑纹识别") is False

    def test_empty_substring(self) -> None:
        assert is_hit("任意内容", "") is True

    def test_empty_content(self) -> None:
        assert is_hit("", "非空子串") is False


# ---------------------------------------------------------------------------
# hit_at_k
# ---------------------------------------------------------------------------


class TestHitAtK:
    def test_hit_at_1_first_position(self) -> None:
        results = [
            _make_result(1, 0, "脑纹识别以脑电图"),
            _make_result(1, 1, "不相关内容"),
        ]
        assert hit_at_k(results, "脑纹识别以脑电图", 1) is True

    def test_hit_at_1_not_first(self) -> None:
        results = [
            _make_result(1, 0, "不相关内容"),
            _make_result(1, 1, "脑纹识别以脑电图"),
        ]
        assert hit_at_k(results, "脑纹识别以脑电图", 1) is False

    def test_hit_at_5_in_top_5(self) -> None:
        results = [
            _make_result(1, 0, "不相关"),
            _make_result(1, 1, "不相关"),
            _make_result(1, 2, "不相关"),
            _make_result(1, 3, "脑纹识别以脑电图"),
            _make_result(1, 4, "不相关"),
        ]
        assert hit_at_k(results, "脑纹识别以脑电图", 5) is True

    def test_hit_at_5_beyond_top_5(self) -> None:
        results = [
            _make_result(1, 0, "不相关"),
            _make_result(1, 1, "不相关"),
            _make_result(1, 2, "不相关"),
            _make_result(1, 3, "不相关"),
            _make_result(1, 4, "不相关"),
            _make_result(1, 5, "脑纹识别以脑电图"),
        ]
        assert hit_at_k(results, "脑纹识别以脑电图", 5) is False

    def test_hit_at_k_empty_results(self) -> None:
        assert hit_at_k([], "任何子串", 5) is False

    def test_hit_at_k_k_larger_than_results(self) -> None:
        results = [_make_result(1, 0, "脑纹识别")]
        assert hit_at_k(results, "脑纹识别", 5) is True

    def test_hit_at_k_normalized_matching(self) -> None:
        results = [_make_result(1, 0, "脑 纹 识 别")]
        assert hit_at_k(results, "脑纹识别", 1) is True


# ---------------------------------------------------------------------------
# reciprocal_rank
# ---------------------------------------------------------------------------


class TestReciprocalRank:
    def test_rank_1(self) -> None:
        results = [_make_result(1, 0, "正确内容"), _make_result(1, 1, "其他")]
        assert reciprocal_rank(results, "正确内容") == 1.0

    def test_rank_2(self) -> None:
        results = [_make_result(1, 0, "错误"), _make_result(1, 1, "正确内容")]
        assert reciprocal_rank(results, "正确内容") == 0.5

    def test_rank_3(self) -> None:
        results = [
            _make_result(1, 0, "a"),
            _make_result(1, 1, "b"),
            _make_result(1, 2, "正确内容"),
        ]
        assert reciprocal_rank(results, "正确内容") == pytest.approx(1 / 3)

    def test_no_hit(self) -> None:
        results = [_make_result(1, 0, "a"), _make_result(1, 1, "b")]
        assert reciprocal_rank(results, "正确内容") == 0.0

    def test_empty_results(self) -> None:
        assert reciprocal_rank([], "任何") == 0.0

    def test_multiple_hits_returns_first(self) -> None:
        results = [
            _make_result(1, 0, "正确"),
            _make_result(1, 1, "正确"),
        ]
        assert reciprocal_rank(results, "正确") == 1.0


# ---------------------------------------------------------------------------
# first_hit_rank
# ---------------------------------------------------------------------------


class TestFirstHitRank:
    def test_rank_1(self) -> None:
        results = [_make_result(1, 0, "正确"), _make_result(1, 1, "其他")]
        assert first_hit_rank(results, "正确") == 1

    def test_rank_3(self) -> None:
        results = [
            _make_result(1, 0, "a"),
            _make_result(1, 1, "b"),
            _make_result(1, 2, "正确"),
        ]
        assert first_hit_rank(results, "正确") == 3

    def test_no_hit(self) -> None:
        results = [_make_result(1, 0, "a")]
        assert first_hit_rank(results, "正确") == 0

    def test_empty_results(self) -> None:
        assert first_hit_rank([], "任何") == 0


# ---------------------------------------------------------------------------
# compute_query_metrics
# ---------------------------------------------------------------------------


class TestComputeQueryMetrics:
    def test_full_hit(self) -> None:
        results = [_make_result(1, 0, "正确内容")]
        m = compute_query_metrics("问题", results, "正确内容", latency_ms=10.0)
        assert m.hit_at_1 is True
        assert m.hit_at_5 is True
        assert m.reciprocal_rank == 1.0
        assert m.first_hit_rank == 1
        assert m.latency_ms == 10.0

    def test_no_hit(self) -> None:
        results = [_make_result(1, 0, "不相关")]
        m = compute_query_metrics("问题", results, "正确内容", latency_ms=5.0)
        assert m.hit_at_1 is False
        assert m.hit_at_5 is False
        assert m.reciprocal_rank == 0.0
        assert m.first_hit_rank == 0

    def test_hit_at_position_3(self) -> None:
        results = [
            _make_result(1, 0, "a"),
            _make_result(1, 1, "b"),
            _make_result(1, 2, "正确"),
        ]
        m = compute_query_metrics("问题", results, "正确", latency_ms=3.0)
        assert m.hit_at_1 is False
        assert m.hit_at_5 is True
        assert m.reciprocal_rank == pytest.approx(1 / 3)
        assert m.first_hit_rank == 3


# ---------------------------------------------------------------------------
# aggregate_metrics
# ---------------------------------------------------------------------------


class TestAggregateMetrics:
    def test_all_hits(self) -> None:
        per_query = [
            QueryMetrics("q1", True, True, 1.0, 5.0, 1),
            QueryMetrics("q2", True, True, 1.0, 6.0, 1),
        ]
        r = aggregate_metrics("test", per_query, chunk_count=10)
        assert r.experiment_name == "test"
        assert r.num_questions == 2
        assert r.hit_at_1 == 1.0
        assert r.hit_at_5 == 1.0
        assert r.mrr == 1.0
        assert r.avg_latency_ms == 5.5
        assert r.chunk_count == 10

    def test_partial_hits(self) -> None:
        per_query = [
            QueryMetrics("q1", True, True, 1.0, 5.0, 1),
            QueryMetrics("q2", False, False, 0.0, 10.0, 0),
            QueryMetrics("q3", False, True, 0.5, 7.0, 2),
        ]
        r = aggregate_metrics("test", per_query)
        assert r.num_questions == 3
        assert r.hit_at_1 == pytest.approx(1 / 3)
        assert r.hit_at_5 == pytest.approx(2 / 3)
        assert r.mrr == pytest.approx((1.0 + 0.0 + 0.5) / 3)
        assert r.avg_latency_ms == pytest.approx((5.0 + 10.0 + 7.0) / 3)

    def test_empty(self) -> None:
        r = aggregate_metrics("empty", [])
        assert r.num_questions == 0
        assert r.hit_at_1 == 0.0
        assert r.hit_at_5 == 0.0
        assert r.mrr == 0.0
        assert r.avg_latency_ms == 0.0


# ---------------------------------------------------------------------------
# load_dataset
# ---------------------------------------------------------------------------


class TestLoadDataset:
    def test_load_valid(self, tmp_path: Path) -> None:
        data = [
            {
                "question": "什么是脑纹识别？",
                "expected_page": 1,
                "expected_substring": "脑纹识别以脑电图",
                "category": "定义",
                "note": "测试",
            },
            {
                "question": "EEG是什么？",
                "expected_page": 2,
                "expected_substring": "脑电图",
            },
        ]
        path = tmp_path / "dataset.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        entries = load_dataset(path)
        assert len(entries) == 2
        assert entries[0].question == "什么是脑纹识别？"
        assert entries[0].expected_page == 1
        assert entries[0].expected_substring == "脑纹识别以脑电图"
        assert entries[0].category == "定义"
        assert entries[0].note == "测试"
        assert entries[1].category == ""
        assert entries[1].note == ""

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="评测数据集文件不存在"):
            load_dataset(tmp_path / "nonexistent.json")

    def test_not_array(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('{"key": "value"}', encoding="utf-8")
        with pytest.raises(ValueError, match="应为 JSON 数组"):
            load_dataset(path)

    def test_missing_field(self, tmp_path: Path) -> None:
        data = [{"question": "问题", "expected_page": 1}]  # 缺 expected_substring
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="缺少必填字段"):
            load_dataset(path)

    def test_load_actual_dataset(self) -> None:
        """加载实际数据集，验证至少 30 条（PROJECT_PLAN 第 726 行验收）。"""
        path = Path("eval/dataset.json")
        if not path.exists():
            pytest.skip("eval/dataset.json 不存在（可能不在项目根目录运行）")
        entries = load_dataset(path)
        assert len(entries) >= 30, f"评测数据集至少 30 条，当前 {len(entries)} 条"
        for entry in entries:
            assert entry.question
            assert entry.expected_page >= 1
            assert entry.expected_substring


# ---------------------------------------------------------------------------
# dataclass 不可变性
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_evaluation_entry_frozen(self) -> None:
        entry = EvaluationEntry("q", 1, "sub")
        with pytest.raises(AttributeError):
            entry.question = "modified"  # type: ignore[misc]

    def test_experiment_config_frozen(self) -> None:
        config = ExperimentConfig("test", 500, 80)
        with pytest.raises(AttributeError):
            config.chunk_size = 1000  # type: ignore[misc]

    def test_query_metrics_frozen(self) -> None:
        m = QueryMetrics("q", True, True, 1.0, 5.0, 1)
        with pytest.raises(AttributeError):
            m.hit_at_1 = False  # type: ignore[misc]

    def test_metric_result_frozen(self) -> None:
        r = MetricResult("test", 10, 0.5, 0.8, 0.6, 5.0)
        with pytest.raises(AttributeError):
            r.hit_at_1 = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 默认实验配置
# ---------------------------------------------------------------------------


class TestDefaultExperiments:
    def test_at_least_two_experiments(self) -> None:
        """验收要求至少两组参数对比（PROJECT_PLAN 第 726 行）。"""
        assert len(DEFAULT_EXPERIMENTS) >= 2

    def test_all_top_k_ge_5(self) -> None:
        """top_k >= 5 保证 Hit@5 可计算。"""
        for exp in DEFAULT_EXPERIMENTS:
            assert exp.top_k >= DEFAULT_EVAL_TOP_K
            assert exp.top_k >= 5

    def test_experiment_names_unique(self) -> None:
        names = [e.name for e in DEFAULT_EXPERIMENTS]
        assert len(names) == len(set(names))

    def test_includes_baseline(self) -> None:
        """包含项目默认参数（chunk_size=500, overlap=80）作为基线。"""
        baselines = [
            e for e in DEFAULT_EXPERIMENTS if e.chunk_size == 500 and e.chunk_overlap == 80
        ]
        assert len(baselines) >= 1
