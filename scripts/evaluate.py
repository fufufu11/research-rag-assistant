"""阶段 7 检索评测脚本：计算 Hit@1 / Hit@5 / MRR / 平均检索耗时，支持参数对比。

PROJECT_PLAN.md 第 724-730 行（阶段 7 交付物与验收）。

本脚本复用阶段 2/3 的 ``chunk_pages`` / ``index_chunks`` / ``retrieve``，
不重新实现检索。只评测**检索阶段**，不调用 LLM，不消耗 Token。

运行前需安装本地 Embedding 推理后端::

    uv sync --extra embedding

用法::

    # 验证数据集：检查每条 expected_substring 是否能在 PDF 切分结果中找到
    uv run python scripts/evaluate.py verify --pdf <path>

    # 运行全部默认实验（5 组参数对比）
    uv run python scripts/evaluate.py run --pdf <path>

    # 运行指定实验（JSON 配置文件）
    uv run python scripts/evaluate.py run --pdf <path> --config experiments.json

    # 仅运行基线实验
    uv run python scripts/evaluate.py run --pdf <path> --only chunk-500-overlap-80

退出码：
- 0：成功
- 2：缺少 sentence-transformers（未运行 ``uv sync --extra embedding``）
- 1：参数错误或 PDF 解析失败
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from research_rag.chunker import ChunkerConfig, chunk_pages
from research_rag.embedding import (
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingServiceError,
    create_embeddings,
    index_chunks,
    retrieve,
)
from research_rag.evaluation import (
    DEFAULT_EVAL_TOP_K,
    DEFAULT_EXPERIMENTS,
    ExperimentConfig,
    aggregate_metrics,
    compute_query_metrics,
    load_dataset,
    normalize_text,
)
from research_rag.pdf_parser import parse_pdf

if TYPE_CHECKING:
    from collections.abc import Sequence

    from research_rag.chunker import Chunk
    from research_rag.evaluation import EvaluationEntry, MetricResult

# 默认数据集路径（相对于项目根目录）
DEFAULT_DATASET_PATH = Path("eval/dataset.json")


def verify_dataset(
    pdf_path: Path,
    dataset_path: Path,
) -> int:
    """验证数据集：检查每条 expected_substring 是否能在默认切分结果中找到。

    Args:
        pdf_path: PDF 文件路径。
        dataset_path: 数据集 JSON 文件路径。

    Returns:
        退出码（0=成功，1=有子串未匹配）。
    """
    entries = load_dataset(dataset_path)
    print(f"已加载数据集：{len(entries)} 条")

    try:
        parse_result = parse_pdf(pdf_path)
    except Exception as exc:
        print(f"错误: PDF 解析失败: {exc}", file=sys.stderr)
        return 1

    chunks = chunk_pages(parse_result.pages, ChunkerConfig())
    print(f"PDF 切分完成：{len(chunks)} 个 chunk（默认参数）")
    print("=" * 70)

    mismatches: list[tuple[int, str]] = []
    for i, entry in enumerate(entries):
        normalized_sub = normalize_text(entry.expected_substring)
        found = any(normalized_sub in normalize_text(c.content) for c in chunks)
        status = "OK" if found else "MISS"
        if not found:
            mismatches.append((i + 1, entry.question))
        print(f"  [{status}] #{i + 1:2d} (页{entry.expected_page}) {entry.question}")

    print("=" * 70)
    if mismatches:
        print(f"验证失败：{len(mismatches)} 条子串未匹配")
        for idx, q in mismatches:
            print(f"  #{idx}: {q}")
        return 1

    print(f"验证通过：全部 {len(entries)} 条子串均可在切分结果中找到")
    return 0


def run_experiment(
    config: ExperimentConfig,
    entries: Sequence[EvaluationEntry],
    chunks: Sequence[Chunk],
    embeddings: object,
) -> MetricResult:
    """运行单组实验：索引 → 逐条检索 → 计算指标。

    Args:
        config: 实验参数。
        entries: 评测数据集。
        chunks: 已按 ``config`` 切分好的 Chunk 列表。
        embeddings: LangChain ``Embeddings`` 实例。

    Returns:
        汇总指标。
    """
    from research_rag.evaluation import QueryMetrics

    print(f"\n[{config.name}] 索引 {len(chunks)} 个 chunk...")
    store = index_chunks(chunks, embeddings)  # type: ignore[arg-type]

    top_k = max(config.top_k, DEFAULT_EVAL_TOP_K)
    per_query: list[QueryMetrics] = []
    for entry in entries:
        start = time.perf_counter()
        results = retrieve(store, entry.question, top_k=top_k)
        latency_ms = (time.perf_counter() - start) * 1000
        per_query.append(
            compute_query_metrics(
                question=entry.question,
                results=results,
                expected_substring=entry.expected_substring,
                latency_ms=latency_ms,
            )
        )

    result = aggregate_metrics(
        experiment_name=config.name,
        per_query=per_query,
        chunk_count=len(chunks),
    )
    print(
        f"  Hit@1={result.hit_at_1:.1%}  Hit@5={result.hit_at_5:.1%}  "
        f"MRR={result.mrr:.3f}  avg={result.avg_latency_ms:.1f}ms"
    )
    return result


def format_results_table(results: Sequence[MetricResult]) -> str:
    """把多组实验结果格式化为 Markdown 表格。"""
    lines = [
        "| 实验 | chunk_size | overlap | chunks | Hit@1 | Hit@5 | MRR | 平均耗时(ms) |",
        "|------|-----------|---------|--------|-------|-------|-----|-------------|",
    ]
    for r in results:
        lines.append(
            f"| {r.experiment_name} | - | - | {r.chunk_count} | "
            f"{r.hit_at_1:.1%} | {r.hit_at_5:.1%} | {r.mrr:.3f} | "
            f"{r.avg_latency_ms:.1f} |"
        )
    return "\n".join(lines)


def format_failure_analysis(results: Sequence[MetricResult]) -> str:
    """格式化失败 case 分析（Hit@5 未命中的问题）。"""
    lines = ["\n## 失败 case 分析（Hit@5 未命中）\n"]
    for r in results:
        failures = [q for q in r.per_query if not q.hit_at_5]
        if not failures:
            continue
        lines.append(f"\n### {r.experiment_name}（{len(failures)} 条未命中）\n")
        for q in failures:
            lines.append(f"- {q.question}")
    return "\n".join(lines)


def run_evaluation(
    pdf_path: Path,
    dataset_path: Path,
    experiments: Sequence[ExperimentConfig],
    only: str | None,
) -> int:
    """运行评测：解析 PDF → 按各组参数切分 → 索引检索 → 汇总指标。

    Args:
        pdf_path: PDF 文件路径。
        dataset_path: 数据集 JSON 文件路径。
        experiments: 实验配置列表。
        only: 仅运行指定名称的实验（None 表示全部）。

    Returns:
        退出码。
    """
    entries = load_dataset(dataset_path)
    print(f"已加载数据集：{len(entries)} 条")

    try:
        embeddings = create_embeddings()
    except EmbeddingServiceError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    print(f"Embedding 模型: {DEFAULT_EMBEDDING_MODEL}")

    try:
        parse_result = parse_pdf(pdf_path)
    except Exception as exc:
        print(f"错误: PDF 解析失败: {exc}", file=sys.stderr)
        return 1

    print(f"PDF: {pdf_path.name}，{parse_result.page_count} 页")
    print("=" * 70)

    selected = [e for e in experiments if only is None or e.name == only]
    if only and not selected:
        print(f"错误: 未找到名为 '{only}' 的实验", file=sys.stderr)
        return 1

    all_results: list[MetricResult] = []
    for config in selected:
        chunker_config = ChunkerConfig(
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )
        chunks = chunk_pages(parse_result.pages, chunker_config)
        result = run_experiment(config, entries, chunks, embeddings)
        all_results.append(result)

    print("\n" + "=" * 70)
    print("汇总结果\n")
    print(format_results_table(all_results))
    print(format_failure_analysis(all_results))

    return 0


def load_experiments(path: Path) -> list[ExperimentConfig]:
    """从 JSON 文件加载实验配置。

    JSON 格式：数组，每个元素含 ``name`` / ``chunk_size`` / ``chunk_overlap`` /
    ``top_k``（可选）/ ``description``（可选）。
    """
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    experiments: list[ExperimentConfig] = []
    for item in data:
        experiments.append(
            ExperimentConfig(
                name=item["name"],
                chunk_size=int(item["chunk_size"]),
                chunk_overlap=int(item["chunk_overlap"]),
                top_k=int(item.get("top_k", DEFAULT_EVAL_TOP_K)),
                description=item.get("description", ""),
            )
        )
    return experiments


def main(argv: list[str] | None = None) -> int:
    """评测脚本入口。

    Args:
        argv: 命令行参数，默认为 None 时读取 sys.argv。

    Returns:
        退出码。
    """
    parser = argparse.ArgumentParser(
        description="阶段 7 检索评测：Hit@1 / Hit@5 / MRR / 平均检索耗时 + 参数对比",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # verify 子命令
    p_verify = sub.add_parser("verify", help="验证数据集子串是否匹配 PDF 切分结果")
    p_verify.add_argument("--pdf", type=Path, required=True, help="PDF 文件路径")
    p_verify.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"数据集 JSON 路径，默认 {DEFAULT_DATASET_PATH}",
    )

    # run 子命令
    p_run = sub.add_parser("run", help="运行评测")
    p_run.add_argument("--pdf", type=Path, required=True, help="PDF 文件路径")
    p_run.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"数据集 JSON 路径，默认 {DEFAULT_DATASET_PATH}",
    )
    p_run.add_argument(
        "--config",
        type=Path,
        help="实验配置 JSON 文件（不指定则用内置默认 5 组实验）",
    )
    p_run.add_argument(
        "--only",
        type=str,
        help="仅运行指定名称的实验",
    )

    args = parser.parse_args(argv)

    if args.command == "verify":
        return verify_dataset(args.pdf, args.dataset)

    if args.command == "run":
        experiments = DEFAULT_EXPERIMENTS
        if args.config:
            experiments = load_experiments(args.config)
        return run_evaluation(args.pdf, args.dataset, experiments, args.only)

    return 1


if __name__ == "__main__":
    sys.exit(main())
