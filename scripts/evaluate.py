"""阶段 7 检索评测脚本：计算 Hit@1 / Hit@5 / MRR / 平均检索耗时，支持参数对比。

本脚本复用阶段 2/3 的 ``chunk_pages`` / ``index_chunks`` / ``retrieve``，
不重新实现检索。只评测**检索阶段**，不调用 LLM，不消耗 Token。

支持两种模式：
- 单 PDF 模式（``--pdf <path>``）：向后兼容，数据集条目无 ``pdf`` 字段时使用
- 多 PDF 模式（``--pdfs-dir <dir>``）：扫描目录下所有 PDF，按数据集条目的
  ``pdf`` 字段（文件名）匹配并解析，所有 chunk 合并到同一个向量库中检索

运行前需安装本地 Embedding 推理后端::

    uv sync --extra embedding

用法::

    # 单 PDF 验证：检查每条 expected_substring 是否能在 PDF 切分结果中找到
    uv run python scripts/evaluate.py verify --pdf <path>

    # 多 PDF 验证：扫描目录，按数据集 pdf 字段匹配
    uv run python scripts/evaluate.py verify --pdfs-dir <dir>

    # 运行全部默认实验（5 组参数对比），单 PDF
    uv run python scripts/evaluate.py run --pdf <path>

    # 运行全部默认实验，多 PDF 合并库
    uv run python scripts/evaluate.py run --pdfs-dir <dir>

    # 运行指定实验（JSON 配置文件）
    uv run python scripts/evaluate.py run --pdfs-dir <dir> --config experiments.json

    # 仅运行基线实验
    uv run python scripts/evaluate.py run --pdfs-dir <dir> --only chunk-500-overlap-80

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

from research_rag.chunker import Chunk, ChunkerConfig, chunk_pages
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

    from research_rag.evaluation import EvaluationEntry, MetricResult

# 默认数据集路径（相对于项目根目录）
DEFAULT_DATASET_PATH = Path("eval/dataset.json")


def scan_pdf_directory(directory: Path) -> dict[str, Path]:
    """扫描目录下所有 PDF 文件，返回 ``{文件名: 路径}`` 字典。

    Args:
        directory: 待扫描的目录。

    Returns:
        文件名（不含路径）到完整路径的映射。
    """
    return {p.name: p for p in directory.glob("*.pdf")}


def _parse_and_chunk_pdfs(
    pdf_paths: dict[str, Path],
    config: ChunkerConfig,
) -> tuple[list[Chunk], dict[str, list[Chunk]]]:
    """解析并切分多份 PDF，返回合并列表与按文件名分组的字典。

    Args:
        pdf_paths: ``{文件名: 路径}`` 映射。
        config: 切分配置。

    Returns:
        (所有 chunk 合并列表, {文件名: 该 PDF 的 chunk 列表})。
    """
    all_chunks: list[Chunk] = []
    per_pdf: dict[str, list[Chunk]] = {}
    # 维护全局 chunk_index 连续编号（合并库场景下需要）
    global_index = 0
    for name, path in pdf_paths.items():
        try:
            parse_result = parse_pdf(path)
        except Exception as exc:
            print(f"错误: 解析 {name} 失败: {exc}", file=sys.stderr)
            raise
        # 复用 chunk_pages 但重新编号以保持全局连续
        raw_chunks = chunk_pages(parse_result.pages, config)
        reindexed: list[Chunk] = []
        for c in raw_chunks:
            reindexed.append(
                Chunk(
                    page_number=c.page_number,
                    chunk_index=global_index,
                    content=c.content,
                    char_count=c.char_count,
                )
            )
            global_index += 1
        per_pdf[name] = reindexed
        all_chunks.extend(reindexed)
    return all_chunks, per_pdf


def verify_dataset(
    pdf_paths: dict[str, Path],
    dataset_path: Path,
) -> int:
    """验证数据集：检查每条 expected_substring 是否能在对应 PDF 的切分结果中找到。

    多 PDF 模式下，按条目的 ``pdf`` 字段定位对应 PDF 的切分结果；
    单 PDF 模式下（数据集无 ``pdf`` 字段，``pdf_paths`` 用空字符串作 key），
    所有条目匹配同一份切分结果。

    Args:
        pdf_paths: ``{文件名: 路径}`` 映射。单 PDF 模式下 key 为空字符串。
        dataset_path: 数据集 JSON 文件路径。

    Returns:
        退出码（0=成功，1=有子串未匹配）。
    """
    entries = load_dataset(dataset_path)
    print(f"已加载数据集：{len(entries)} 条")

    # 按文件名分组切分（单 PDF 时 key=""）
    config = ChunkerConfig()
    per_pdf_chunks: dict[str, list[Chunk]] = {}
    for name, path in pdf_paths.items():
        try:
            parse_result = parse_pdf(path)
        except Exception as exc:
            print(f"错误: 解析 {name or path} 失败: {exc}", file=sys.stderr)
            return 1
        per_pdf_chunks[name] = chunk_pages(parse_result.pages, config)
        print(
            f"  {name or path.name}: {parse_result.page_count} 页, {len(per_pdf_chunks[name])} chunks"
        )

    print("=" * 70)

    mismatches: list[tuple[int, str]] = []
    for i, entry in enumerate(entries):
        # 单 PDF 模式（数据集无 pdf 字段）时用空字符串 key
        key = entry.pdf if entry.pdf else next(iter(pdf_paths))
        chunks = per_pdf_chunks.get(key, [])
        normalized_sub = normalize_text(entry.expected_substring)
        found = any(normalized_sub in normalize_text(c.content) for c in chunks)
        status = "OK" if found else "MISS"
        if not found:
            mismatches.append((i + 1, entry.question))
        pdf_label = f"[{entry.pdf}] " if entry.pdf else ""
        print(f"  [{status}] #{i + 1:2d} (页{entry.expected_page}) {pdf_label}{entry.question}")

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
    pdf_paths: dict[str, Path],
    dataset_path: Path,
    experiments: Sequence[ExperimentConfig],
    only: str | None,
) -> int:
    """运行评测：解析 PDF → 按各组参数切分 → 合并索引 → 检索 → 汇总指标。

    多 PDF 模式下，所有 PDF 的 chunk 合并到同一个向量库中检索，模拟用户
    上传多份文献后的真实场景；单 PDF 模式下 ``pdf_paths`` 只有一个条目。

    Args:
        pdf_paths: ``{文件名: 路径}`` 映射。单 PDF 模式下 key 为空字符串。
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
    print(f"待评测 PDF: {len(pdf_paths)} 份")
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
        # 每组实验重新切分所有 PDF（参数不同，切分结果不同）
        all_chunks, _ = _parse_and_chunk_pdfs(pdf_paths, chunker_config)
        print(f"\n[{config.name}] 共 {len(all_chunks)} chunks ({len(pdf_paths)} 份 PDF)")
        result = run_experiment(config, entries, all_chunks, embeddings)
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


def _resolve_pdf_paths(args: argparse.Namespace) -> dict[str, Path] | int:
    """根据命令行参数解析 PDF 路径字典。

    ``--pdfs-dir`` 与 ``--pdf`` 二选一：前者扫描目录下所有 PDF（多 PDF 模式），
    后者使用单个 PDF（单 PDF 模式，向后兼容）。

    Returns:
        成功时返回 ``{文件名: 路径}`` 字典（单 PDF 模式下 key 为空字符串）；
        失败时返回退出码。
    """
    if args.pdfs_dir:
        if not args.pdfs_dir.is_dir():
            print(f"错误: 目录不存在: {args.pdfs_dir}", file=sys.stderr)
            return 1
        all_pdfs = scan_pdf_directory(args.pdfs_dir)
        if not all_pdfs:
            print(f"错误: 目录下未找到 PDF 文件: {args.pdfs_dir}", file=sys.stderr)
            return 1
        # 只保留数据集引用的 PDF（按文件名匹配）
        entries = load_dataset(args.dataset)
        referenced = {e.pdf for e in entries if e.pdf}
        if not referenced:
            print(
                "错误: 数据集条目无 pdf 字段，无法用 --pdfs-dir 模式；请改用 --pdf <path>",
                file=sys.stderr,
            )
            return 1
        missing = referenced - set(all_pdfs)
        if missing:
            print(
                f"错误: 数据集引用的 PDF 在目录中未找到: {sorted(missing)}",
                file=sys.stderr,
            )
            return 1
        return {name: path for name, path in all_pdfs.items() if name in referenced}
    if args.pdf:
        return {"": args.pdf}
    # 不应该到这里（argparse required 保证）
    return 1


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
    pdf_group_v = p_verify.add_mutually_exclusive_group(required=True)
    pdf_group_v.add_argument("--pdf", type=Path, help="单个 PDF 文件路径")
    pdf_group_v.add_argument(
        "--pdfs-dir",
        type=Path,
        help="PDF 所在目录（多 PDF 模式，按数据集 pdf 字段匹配）",
    )
    p_verify.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"数据集 JSON 路径，默认 {DEFAULT_DATASET_PATH}",
    )

    # run 子命令
    p_run = sub.add_parser("run", help="运行评测")
    pdf_group_r = p_run.add_mutually_exclusive_group(required=True)
    pdf_group_r.add_argument("--pdf", type=Path, help="单个 PDF 文件路径")
    pdf_group_r.add_argument(
        "--pdfs-dir",
        type=Path,
        help="PDF 所在目录（多 PDF 模式，所有 chunk 合并到一个向量库）",
    )
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

    pdf_paths = _resolve_pdf_paths(args)
    if isinstance(pdf_paths, int):
        return pdf_paths

    if args.command == "verify":
        return verify_dataset(pdf_paths, args.dataset)

    if args.command == "run":
        experiments = DEFAULT_EXPERIMENTS
        if args.config:
            experiments = load_experiments(args.config)
        return run_evaluation(pdf_paths, args.dataset, experiments, args.only)

    return 1


if __name__ == "__main__":
    sys.exit(main())
