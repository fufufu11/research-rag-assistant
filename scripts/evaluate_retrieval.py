"""最小评测脚本：验证 Embedding + 向量检索端到端流程。

PROJECT_PLAN.md 第 693 节（阶段 3 交付物）、第 699 节（验收）。

本脚本使用真实 Embedding 模型（``BAAI/bge-small-zh-v1.5``），运行前需安装
推理后端::

    uv sync --extra embedding

用法::

    # demo 模式：用内置示例文本验证端到端流程（无需 PDF）
    uv run python scripts/evaluate_retrieval.py --demo

    # PDF 模式：解析真实 PDF 并对指定问题检索
    uv run python scripts/evaluate_retrieval.py --pdf <path> \\
        --question "注意力机制是什么？" \\
        --question "如何优化神经网络参数？"

退出码：
- 0：成功
- 2：缺少 sentence-transformers（未运行 ``uv sync --extra embedding``）
- 1：参数错误（argparse 默认行为）

注意：脚本只展示检索结果，不计算"准确率"等指标——那需要标注数据集，
属于后续工作。这里只验证"索引→检索→排序"流程端到端可用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from research_rag.chunker import Chunk, ChunkerConfig, chunk_pages
from research_rag.embedding import (
    DEFAULT_TOP_K,
    EmbeddingServiceError,
    create_embeddings,
    index_chunks,
    retrieve,
)
from research_rag.pdf_parser import parse_pdf

# demo 模式内置的示例片段（模拟科研文献的 chunk）
# 不使用真实 PDF 也能验证 Embedding + 检索流程
DEMO_CHUNKS: list[Chunk] = [
    Chunk(
        page_number=1,
        chunk_index=0,
        content=(
            "注意力机制（Attention Mechanism）是深度学习中用于建模序列依赖关系的核心方法。"
            "它通过计算查询（Query）与键（Key）之间的相似度，对值（Value）加权求和，"
            "使模型能够聚焦于输入序列中的重要部分。"
        ),
        char_count=96,
    ),
    Chunk(
        page_number=1,
        chunk_index=1,
        content=(
            "梯度下降是优化神经网络参数的基本方法。通过计算损失函数对参数的偏导数，"
            "沿梯度反方向更新参数，逐步最小化损失。学习率控制每次更新的步长，"
            "过大导致震荡，过小导致收敛缓慢。"
        ),
        char_count=94,
    ),
    Chunk(
        page_number=2,
        chunk_index=2,
        content=(
            "余弦相似度通过测量两个向量夹角的余弦值来衡量它们的相似性。"
            "在向量检索中，余弦相似度常用于比较查询向量与文档向量的语义相关性，"
            "值域为 [-1, 1]，值越大表示方向越一致、语义越相关。"
        ),
        char_count=97,
    ),
    Chunk(
        page_number=2,
        chunk_index=3,
        content=(
            "Transformer 架构由编码器和解码器堆叠组成，完全基于自注意力机制，"
            "摒弃了循环和卷积结构。位置编码为模型注入序列顺序信息，"
            "多头注意力使模型能在不同表示子空间中联合关注信息。"
        ),
        char_count=92,
    ),
]

# demo 模式内置的示例问题
DEMO_QUESTIONS: list[str] = [
    "注意力机制是什么？",
    "如何优化神经网络的参数？",
    "余弦相似度有什么作用？",
    "Transformer 的架构特点是什么？",
]


def _print_results(question: str, results: list) -> None:  # type: ignore[type-arg]
    """打印单个问题的检索结果。"""
    print(f"\n问题: {question}")
    print("-" * 60)
    if not results:
        print("  （无检索结果）")
        return
    for i, r in enumerate(results, 1):
        preview = r.content[:80].replace("\n", " ")
        print(f"  [{i}] 页码={r.page_number} 片段={r.chunk_index} 分数={r.score:.4f}")
        print(f"      内容: {preview}...")
    print("-" * 60)


def main(argv: list[str] | None = None) -> int:
    """评测脚本入口。

    Args:
        argv: 命令行参数，默认为 None 时读取 sys.argv。

    Returns:
        退出码。
    """
    parser = argparse.ArgumentParser(
        description="最小评测脚本：验证 Embedding + 向量检索端到端流程。",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--demo",
        action="store_true",
        help="使用内置示例文本验证流程（无需 PDF）",
    )
    mode.add_argument(
        "--pdf",
        type=Path,
        help="解析指定 PDF 文件并检索",
    )
    parser.add_argument(
        "--question",
        action="append",
        default=[],
        help="检索问题（可重复指定多个；demo 模式下忽略，使用内置问题）",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Top-K 检索数量，默认 {DEFAULT_TOP_K}",
    )
    args = parser.parse_args(argv)

    # 创建 Embedding（唯一接触真实模型的地方）
    try:
        embeddings = create_embeddings()
    except EmbeddingServiceError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    # 准备 chunks
    if args.demo:
        chunks = DEMO_CHUNKS
        questions = DEMO_QUESTIONS
        print(f"[demo 模式] 使用 {len(chunks)} 个内置示例片段，{len(questions)} 个内置问题")
    else:
        pdf_path: Path = args.pdf
        if not pdf_path.exists():
            print(f"错误: 文件不存在: {pdf_path}", file=sys.stderr)
            return 1
        questions = args.question
        if not questions:
            print("错误: PDF 模式下至少需要指定一个 --question", file=sys.stderr)
            return 1

        try:
            parse_result = parse_pdf(pdf_path)
        except Exception as exc:
            print(f"错误: PDF 解析失败: {exc}", file=sys.stderr)
            return 1

        chunks = chunk_pages(parse_result.pages, ChunkerConfig())
        print(
            f"[PDF 模式] {pdf_path.name}：{parse_result.page_count} 页，切分为 {len(chunks)} 个片段"
        )

    if not chunks:
        print("错误: 没有可索引的文本片段", file=sys.stderr)
        return 1

    # 索引 → 检索
    print(f"正在索引 {len(chunks)} 个片段...")
    store = index_chunks(chunks, embeddings)

    print(f"模型: BAAI/bge-small-zh-v1.5 | Top-K: {args.top_k}")
    print("=" * 60)

    for question in questions:
        results = retrieve(store, question, top_k=args.top_k)
        _print_results(question, results)

    print("\n评测完成。请人工检查检索结果是否合理。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
