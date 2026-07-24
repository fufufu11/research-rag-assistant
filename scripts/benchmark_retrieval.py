"""阶段 10.3 检索阶段 P95 基准脚本。

依据 Issue #63 验收标准与 ADR 0002（P95 验收口径限定为检索阶段）。

**计时范围**：仅 ``QaService._retrieve_hybrid``（BM25 建索引 + BM25 检索 +
Qdrant/InMemory 向量检索 + RRF 融合），**不含 reranker 与 LLM 生成**。
理由：端到端延迟中 LLM 生成占 80%+，检索优化无法移动端到端 P95 50%。
详见 ``docs/adr/0002-retrieval-stage-p95-metric.md``。

**两遍跑法**：
- 冷启动（cache miss）：首次调用，BM25 索引缓存未命中，构建索引后检索
- 热命中（cache hit）：第二次调用，缓存命中，跳过索引构建
对比两遍 P95 可观察 BM25 索引缓存的收益。

**路径选择**：
- 默认 InMemory 路径（无需 Qdrant，便于本地快速验证 BM25 缓存效果）
- ``--qdrant`` 切到 Qdrant 生产路径（并发检索在此路径生效，需先启动 Qdrant）

**基线对比**：
- ``--no-cache`` 禁用 BM25 缓存（模拟 main 分支行为），用于在同一分支跑基线
- 默认（带缓存）跑优化值，P95 降幅 = (baseline - optimized) / baseline

运行前需安装本地 Embedding 推理后端::

    uv sync --extra embedding

中文场景需额外安装 jieba（BM25 分词）::

    uv sync --extra chinese

用法::

    # InMemory 路径（默认），英文数据集
    uv run python scripts/benchmark_retrieval.py --pdfs-dir eval/pdfs

    # Qdrant 生产路径（需先启动 Qdrant）
    uv run python scripts/benchmark_retrieval.py --pdfs-dir eval/pdfs --qdrant

    # 中文数据集
    uv run python scripts/benchmark_retrieval.py \\
        --pdfs-dir eval/pdfs/zh \\
        --dataset eval/dataset_zh.json \\
        --embedding-model BAAI/bge-small-zh-v1.5

    # 禁用 BM25 缓存跑基线（模拟 main 分支）
    uv run python scripts/benchmark_retrieval.py --pdfs-dir eval/pdfs --no-cache

退出码：
- 0：成功
- 2：缺少 sentence-transformers 或 rank-bm25
- 1：参数错误或 PDF 解析失败
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from research_rag.chunker import Chunk as ChunkerChunk
from research_rag.chunker import ChunkerConfig, chunk_pages
from research_rag.db.models import Base, Chunk, Document, DocumentStatus
from research_rag.embedding import (
    DEFAULT_EMBEDDING_MODEL,
    EmbeddingConfig,
    EmbeddingServiceError,
    create_embeddings,
)
from research_rag.hybrid_retriever import BM25IndexCache
from research_rag.pdf_parser import parse_pdf
from research_rag.qa_service import LlmConfig
from research_rag.services.qa_service import QaService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.embeddings import Embeddings


def _percentile(data: list[float], p: float) -> float:
    """计算第 p 百分位（0-100），线性插值（与 numpy.percentile 默认方法一致）。

    Args:
        data: 样本列表（无需预先排序）。
        p: 百分位（0-100），如 50 表示中位数，95 表示 P95。

    Returns:
        百分位值。空列表返回 0.0。
    """

    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    k = (p / 100.0) * (n - 1)
    f = int(k)
    c = k - f
    if f + 1 < n:
        return sorted_data[f] + c * (sorted_data[f + 1] - sorted_data[f])
    return sorted_data[f]


def _format_stats(label: str, latencies_ms: list[float]) -> str:
    """把延迟列表格式为一行统计摘要。"""

    if not latencies_ms:
        return f"| {label} | - | - | - | - |"
    p50 = _percentile(latencies_ms, 50)
    p95 = _percentile(latencies_ms, 95)
    p99 = _percentile(latencies_ms, 99)
    avg = sum(latencies_ms) / len(latencies_ms)
    return f"| {label} | {p50:.1f} | {p95:.1f} | {p99:.1f} | {avg:.1f} | {len(latencies_ms)} |"


def _load_dataset(dataset_path: Path) -> list[str]:
    """加载数据集，返回问题列表。

    Args:
        dataset_path: 数据集 JSON 文件路径（结构同 ``eval/dataset.json``）。

    Returns:
        问题字符串列表。
    """

    with dataset_path.open(encoding="utf-8") as f:
        entries = json.load(f)
    return [entry["question"] for entry in entries]


def _make_doc(
    session: Session,
    name: str,
    chunker_chunks: Sequence[ChunkerChunk],
) -> Document:
    """构造并持久化一个 Document 及其 Chunks（从 chunker.Chunk 列表）。

    与 ``tests/unit/test_qa_orchestration.py::_make_doc`` 结构一致，但接受
    chunker.Chunk 列表（含 start_page/end_page/chunk_index/content/char_count）。

    Args:
        session: SQLAlchemy Session。
        name: 文档名（original_name）。
        chunker_chunks: chunker.Chunk 列表（由 ``chunk_pages`` 产出）。

    Returns:
        持久化后的 ``Document``。
    """

    page_count = max((c.end_page for c in chunker_chunks), default=1)
    doc = Document(
        original_name=name,
        stored_name=f"{name}.stored",
        sha256=hashlib.sha256(name.encode()).hexdigest(),
        page_count=page_count,
        status=DocumentStatus.READY,
    )
    session.add(doc)
    session.flush()
    for c in chunker_chunks:
        session.add(
            Chunk(
                document_id=doc.id,
                start_page=c.start_page,
                end_page=c.end_page,
                chunk_index=c.chunk_index,
                content=c.content,
                char_count=c.char_count,
            )
        )
    session.flush()
    return doc


def _parse_and_index_pdfs(
    pdf_paths: dict[str, Path],
    session: Session,
    config: ChunkerConfig,
) -> list[Document]:
    """解析并切分多份 PDF，创建 Document/Chunk 记录到 DB。

    Args:
        pdf_paths: ``{文件名: 路径}`` 映射。
        session: SQLAlchemy Session。
        config: 切分配置。

    Returns:
        已持久化的 ``Document`` 列表（全部 READY）。
    """

    docs: list[Document] = []
    global_index = 0
    for name, path in pdf_paths.items():
        try:
            parse_result = parse_pdf(path)
        except Exception as exc:
            print(f"错误: 解析 {name} 失败: {exc}", file=sys.stderr)
            raise
        raw_chunks = chunk_pages(parse_result.pages, config)
        # 重新编号保持全局连续（与 scripts/evaluate.py 一致）
        reindexed: list[ChunkerChunk] = []
        for c in raw_chunks:
            reindexed.append(
                ChunkerChunk(
                    start_page=c.start_page,
                    end_page=c.end_page,
                    chunk_index=global_index,
                    content=c.content,
                    char_count=c.char_count,
                )
            )
            global_index += 1
        doc = _make_doc(session, name, reindexed)
        docs.append(doc)
        print(f"  {name}: {parse_result.page_count} 页, {len(reindexed)} chunks")
    return docs


def _build_qa_service(
    session: Session,
    embeddings: Embeddings,
    bm25_cache: BM25IndexCache | None,
    vector_store: object | None,
) -> QaService:
    """构造 QaService 实例，bm25_enabled=True 走 _retrieve_hybrid 路径。

    Args:
        session: SQLAlchemy Session（已建表，含 READY 文档）。
        embeddings: LangChain Embeddings 实例（InMemory 路径用）。
        bm25_cache: BM25 索引缓存，``None`` 表示禁用缓存（基线模式）。
        vector_store: QdrantVectorStore 实例（Qdrant 路径用），``None`` 走 InMemory。

    Returns:
        ``QaService`` 实例（bm25_enabled=True）。
    """

    # LlmConfig 留空：基准脚本不调用 LLM，create_chat_model 不会被触发
    llm_config = LlmConfig(base_url="", api_key="", model="")
    return QaService(
        session,
        llm_config,
        embeddings=embeddings,
        bm25_enabled=True,
        bm25_cache=bm25_cache,
        vector_store=vector_store,  # type: ignore[arg-type]
    )


def _run_pass(
    service: QaService,
    docs: Sequence[Document],
    questions: Sequence[str],
    top_k: int,
    label: str,
) -> list[float]:
    """对每个问题调用 ``_retrieve_hybrid`` 并计时，返回延迟列表（毫秒）。

    Args:
        service: ``QaService`` 实例。
        docs: READY 文档列表。
        questions: 问题列表。
        top_k: 检索返回的最相关片段数。
        label: 本次 pass 的标签（用于日志打印）。

    Returns:
        延迟列表（毫秒），长度与 ``questions`` 一致。
    """

    print(f"\n[{label}] 检索 {len(questions)} 个问题（top_k={top_k}）...")
    latencies: list[float] = []
    for i, question in enumerate(questions, 1):
        start = time.perf_counter()
        service._retrieve_hybrid(docs, question, top_k)
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
        if i % 10 == 0 or i == len(questions):
            print(f"  [{label}] {i}/{len(questions)} 完成")
    return latencies


def main(argv: list[str] | None = None) -> int:
    """基准脚本入口。

    Args:
        argv: 命令行参数，默认 None 时读取 ``sys.argv``。

    Returns:
        退出码（0=成功，2=缺依赖，1=参数错误）。
    """

    parser = argparse.ArgumentParser(
        description="阶段 10.3 检索阶段 P95 基准脚本（计时 _retrieve_hybrid）。",
    )
    parser.add_argument(
        "--pdfs-dir",
        type=Path,
        required=True,
        help="PDF 所在目录，扫描全部 .pdf 文件按数据集 pdf 字段匹配",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("eval/dataset.json"),
        help="数据集 JSON 路径（默认 eval/dataset.json）",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=8,
        help="检索返回的最相关片段数（默认 8）",
    )
    parser.add_argument(
        "--qdrant",
        action="store_true",
        help="使用 Qdrant 生产路径（并发检索在此路径生效，需先启动 Qdrant）",
    )
    parser.add_argument(
        "--embedding-model",
        type=str,
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"Embedding 模型名（默认 {DEFAULT_EMBEDDING_MODEL}）",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="禁用 BM25 索引缓存（基线模式，模拟 main 分支行为）",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="切分片段大小（默认 500）",
    )
    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=80,
        help="切分重叠字符数（默认 80）",
    )
    args = parser.parse_args(argv)

    # 1. 加载数据集
    if not args.dataset.exists():
        print(f"错误: 数据集不存在: {args.dataset}", file=sys.stderr)
        return 1
    questions = _load_dataset(args.dataset)
    print(f"已加载数据集：{len(questions)} 条问题（{args.dataset}）")

    # 2. 扫描 PDF 目录
    if not args.pdfs_dir.exists():
        print(f"错误: PDF 目录不存在: {args.pdfs_dir}", file=sys.stderr)
        return 1
    pdf_paths = {p.name: p for p in args.pdfs_dir.glob("*.pdf")}
    if not pdf_paths:
        print(f"错误: {args.pdfs_dir} 下无 PDF 文件", file=sys.stderr)
        return 1
    print(f"待索引 PDF：{len(pdf_paths)} 份")

    # 3. 创建 Embedding（InMemory 路径必需；Qdrant 路径也需要写入向量）
    emb_config = EmbeddingConfig(model_name=args.embedding_model)
    try:
        embeddings = create_embeddings(emb_config)
    except EmbeddingServiceError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    # 4. 解析 + 切分 PDF + 写入内存 SQLite
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session: Session = session_factory()

    chunker_config = ChunkerConfig(chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)
    print(f"切分配置: chunk_size={args.chunk_size}, overlap={args.chunk_overlap}")
    docs = _parse_and_index_pdfs(pdf_paths, session, chunker_config)
    print(f"已索引 {len(docs)} 份文档到内存 SQLite")

    # 5. 可选：创建 QdrantVectorStore 并 upsert chunks
    vector_store: object | None = None
    if args.qdrant:
        try:
            from research_rag.vector_store import (
                create_vector_store,
                get_qdrant_config,
                upsert_chunks,
            )
        except ImportError as exc:
            print(f"错误: 无法导入 vector_store 模块：{exc}", file=sys.stderr)
            return 2

        try:
            qdrant_config = get_qdrant_config()
            vector_store = create_vector_store(qdrant_config, embeddings)
        except Exception as exc:
            print(f"错误: 创建 QdrantVectorStore 失败：{exc}", file=sys.stderr)
            return 2

        # upsert 所有文档的 chunks 到 Qdrant
        for doc in docs:
            chunks = session.query(Chunk).filter_by(document_id=doc.id).all()
            if chunks:
                upsert_chunks(vector_store, doc.id, doc.original_name, chunks)
        print(f"已 upsert {len(docs)} 份文档的 chunks 到 Qdrant")

    # 6. 构造 QaService（bm25_enabled=True，按 --no-cache 决定是否注入缓存）
    bm25_cache: BM25IndexCache | None = None if args.no_cache else BM25IndexCache()
    service = _build_qa_service(session, embeddings, bm25_cache, vector_store)

    # 7. 跑两遍：冷启动（cache miss）+ 热命中（cache hit）
    # 注意：--no-cache 模式下两遍都是 cache miss（每次重建），用于基线对比
    cold_label = "cold (no-cache)" if args.no_cache else "cold (cache-miss)"
    cold_latencies = _run_pass(service, docs, questions, args.top_k, cold_label)

    hot_label = "hot (no-cache)" if args.no_cache else "hot (cache-hit)"
    hot_latencies = _run_pass(service, docs, questions, args.top_k, hot_label)

    # 8. 输出统计
    path_label = "Qdrant" if args.qdrant else "InMemory"
    cache_label = "disabled" if args.no_cache else "enabled"
    print("\n" + "=" * 70)
    print(f"基准结果 | 路径={path_label} | BM25 缓存={cache_label} | top_k={args.top_k}")
    print("=" * 70)
    print("| Pass | P50 (ms) | P95 (ms) | P99 (ms) | Avg (ms) | Count |")
    print("|------|----------|----------|----------|----------|-------|")
    print(_format_stats(cold_label, cold_latencies))
    print(_format_stats(hot_label, hot_latencies))

    # 9. 清理
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()

    return 0


if __name__ == "__main__":
    sys.exit(main())
