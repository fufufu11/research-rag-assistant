"""快速验证 BM25 索引缓存的性能收益（不加载 Embedding 模型）。

直接测 BM25Retriever 构建时间 vs 缓存命中时间，证明阶段 10.3 的核心优化有效。
完整 P95 验收需跑 scripts/benchmark_retrieval.py（需 Embedding 模型 + Qdrant）。
"""

from __future__ import annotations

import time
import uuid

from research_rag.chunker import Chunk
from research_rag.hybrid_retriever import BM25IndexCache, BM25Retriever

# 模拟真实场景：3 份文档 × ~130 chunks = 394 chunks（与 eval/pdfs 一致）
CHUNK_COUNT = 394
QUERY = "What is the attention mechanism in Transformer?"


def _make_chunks(n: int) -> list[Chunk]:
    """生成 n 个测试 chunk（模拟论文切分结果）。"""
    chunks = []
    for i in range(n):
        chunks.append(
            Chunk(
                start_page=i // 50 + 1,
                end_page=i // 50 + 1,
                chunk_index=i,
                content=f"这是第 {i} 个 chunk 的内容，包含一些关于深度学习和注意力机制的文本。"
                f"Chunk {i} discusses neural networks and transformer architecture. "
                f"Attention mechanism allows models to focus on relevant parts. "
                f"Section {i} covers convolutional layers and feature extraction.",
                char_count=200,
            )
        )
    return chunks


def main() -> int:
    chunks = _make_chunks(CHUNK_COUNT)
    doc_ids = [uuid.uuid4() for _ in range(3)]

    print(f"测试场景：{len(doc_ids)} 份文档, {len(chunks)} 个 chunk")
    print(f"查询：{QUERY}")
    print("=" * 70)

    # 基线：每次重建 BM25 索引（模拟 main 分支 --no-cache）
    print("\n[基线] 每次重建 BM25 索引（模拟 main 分支）...")
    baseline_latencies = []
    for _ in range(10):
        start = time.perf_counter()
        retriever = BM25Retriever(chunks)
        retriever.retrieve(QUERY, top_k=8)
        elapsed_ms = (time.perf_counter() - start) * 1000
        baseline_latencies.append(elapsed_ms)
    print("  10 次检索，每次重建索引")

    # 优化值：BM25 索引缓存（阶段 10.3）
    print("\n[优化] BM25 索引缓存（阶段 10.3）...")
    cache = BM25IndexCache()

    # 第一次：cache miss，构建索引
    start = time.perf_counter()
    retriever_cold = cache.get_or_build(doc_ids, chunks)
    retriever_cold.retrieve(QUERY, top_k=8)
    cold_ms = (time.perf_counter() - start) * 1000
    print(f"  冷启动（cache miss，构建索引）：{cold_ms:.1f}ms")

    # 后续 9 次：cache hit，跳过索引构建
    optimized_latencies = [cold_ms]
    for _ in range(9):
        start = time.perf_counter()
        retriever_hot = cache.get_or_build(doc_ids, chunks)
        retriever_hot.retrieve(QUERY, top_k=8)
        elapsed_ms = (time.perf_counter() - start) * 1000
        optimized_latencies.append(elapsed_ms)
    print("  10 次检索，第 1 次 cache miss，后续 9 次 cache hit")

    # 计算分位数
    def percentile(data: list[float], p: float) -> float:
        s = sorted(data)
        n = len(s)
        if n == 1:
            return s[0]
        k = (p / 100.0) * (n - 1)
        f = int(k)
        c = k - f
        if f + 1 < n:
            return s[f] + c * (s[f + 1] - s[f])
        return s[f]

    # 统计
    print("\n" + "=" * 70)
    print(f"{'Pass':<25} {'P50(ms)':<10} {'P95(ms)':<10} {'P99(ms)':<10} {'Avg(ms)':<10}")
    print("-" * 70)

    b_p50 = percentile(baseline_latencies, 50)
    b_p95 = percentile(baseline_latencies, 95)
    b_p99 = percentile(baseline_latencies, 99)
    b_avg = sum(baseline_latencies) / len(baseline_latencies)
    print(f"{'基线(每次重建)':<25} {b_p50:<10.1f} {b_p95:<10.1f} {b_p99:<10.1f} {b_avg:<10.1f}")

    o_p50 = percentile(optimized_latencies, 50)
    o_p95 = percentile(optimized_latencies, 95)
    o_p99 = percentile(optimized_latencies, 99)
    o_avg = sum(optimized_latencies) / len(optimized_latencies)
    print(f"{'优化(BM25缓存)':<25} {o_p50:<10.1f} {o_p95:<10.1f} {o_p99:<10.1f} {o_avg:<10.1f}")

    # P95 降幅
    p95_reduction = (b_p95 - o_p95) / b_p95 * 100
    print("-" * 70)
    print(f"BM25 索引缓存 P95 降幅：{p95_reduction:.1f}%（{b_p95:.1f}ms → {o_p95:.1f}ms）")

    if p95_reduction >= 50:
        print("✅ 验收通过：P95 降幅 ≥ 50%")
    else:
        print("⚠️  P95 降幅 < 50%（InMemory 路径，含 retrieve 开销；Qdrant 生产路径效果更显著）")

    # 额外：纯索引构建时间对比
    print("\n" + "=" * 70)
    print("[纯索引构建时间对比]")

    start = time.perf_counter()
    BM25Retriever(chunks)
    build_ms = (time.perf_counter() - start) * 1000
    print(f"  BM25 索引构建：{build_ms:.1f}ms（基线每次都付这个开销）")

    start = time.perf_counter()
    cache.get_or_build(doc_ids, chunks)  # 已缓存，命中
    hit_ms = (time.perf_counter() - start) * 1000
    print(f"  缓存命中查询：{hit_ms:.2f}ms（优化后跳过构建）")
    print(f"  单次节省：{build_ms - hit_ms:.1f}ms")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
