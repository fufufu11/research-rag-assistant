"""阶段 9.3 答案质量评测脚本：检索 → 重排 → LLM 生成 → judge 打分 → 汇总报告。

本脚本扩展阶段 7 的检索评测到生成阶段，用 LLM-as-judge 量化答案质量
（忠实度 / 相关性 / 完整性 / 引用正确性）。复用现有 chunker / embedding /
reranker / hybrid_retriever / qa_service 组件，不依赖 DB / Qdrant 基础设施，
与 ``scripts/evaluate.py`` 的离线评测风格一致。

运行前需：
1. 安装本地 Embedding 推理后端：``uv sync --extra embedding``（中文场景额外
   ``uv sync --extra chinese`` 装 jieba）。
2. 配置 LLM 环境变量（generator 与 judge 默认用同一 LLM，可设 ``JUDGE_LLM_*``
   用不同 LLM 避免同模型自评偏差）::

   PowerShell:
       $env:LLM_BASE_URL="https://api.deepseek.com"
       $env:LLM_API_KEY="sk-..."
       $env:LLM_MODEL="deepseek-chat"
       # 可选：judge 用不同模型
       $env:JUDGE_LLM_MODEL="deepseek-reasoner"
   bash:
       export LLM_BASE_URL=https://api.deepseek.com
       export LLM_API_KEY=sk-...
       export LLM_MODEL=deepseek-chat

用法::

    # 英文论文答案质量评测（生成 docs/answer_quality_report.md）
    uv run python scripts/evaluate_answer.py run --pdfs-dir <含英文 PDF 的目录> `
        --embedding-model BAAI/bge-small-en-v1.5 `
        --reranker-model BAAI/bge-reranker-base --bm25

    # 中文论文答案质量评测（生成 docs/answer_quality_report_zh.md）
    uv run python scripts/evaluate_answer.py run --pdfs-dir <含中文 PDF 的目录> `
        --dataset eval/dataset_zh.json `
        --output docs/answer_quality_report_zh.md --bm25

    # 指定 judge 用不同 LLM（避免同模型自评偏差）
    $env:JUDGE_LLM_MODEL="deepseek-reasoner"
    uv run python scripts/evaluate_answer.py run --pdfs-dir <dir> ...

退出码：
- 0：成功
- 2：缺少 sentence-transformers 或 LLM 配置缺失（未配置 LLM_API_KEY/LLM_MODEL）
- 1：参数错误或 PDF 解析失败

安全约束：API Key 绝不硬编码，仅从环境变量读取；评测报告不含真实学号姓名。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from research_rag.answer_evaluation import (
    AnswerEvaluationResult,
    AnswerJudgement,
    AnswerSample,
    aggregate_judgements,
    judge_answer,
    load_judge_config_from_env,
)
from research_rag.chunker import Chunk, ChunkerConfig, chunk_pages
from research_rag.embedding import (
    DASHSCOPE_DEFAULT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    JINA_DEFAULT_MODEL,
    EmbeddingConfig,
    EmbeddingServiceError,
    create_embeddings,
    index_chunks,
    retrieve,
)
from research_rag.evaluation import load_dataset
from research_rag.hybrid_retriever import BM25Retriever, hybrid_retrieve
from research_rag.pdf_parser import parse_pdf
from research_rag.qa_service import (
    INSUFFICIENT_EVIDENCE_MARKER,
    ContextPiece,
    InsufficientEvidenceError,
    LlmConfig,
    LlmServiceError,
    answer_question,
    create_chat_model,
)
from research_rag.reranker import (
    DEFAULT_RERANKER_MODEL,
    BaseReranker,
    RerankerConfig,
    RerankerError,
    create_reranker,
    rerank_results,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.language_models.chat_models import BaseChatModel

    from research_rag.embedding import RetrievalResult

# 默认数据集与报告路径（相对于项目根目录）
DEFAULT_DATASET_PATH = Path("eval/dataset.json")
DEFAULT_OUTPUT_PATH = Path("docs/answer_quality_report.md")
# 生成阶段检索的默认 top_k。检索评测用 5（计算 Hit@5），生成阶段用更多上下文
# 提升答案完整性；与生产 RETRIEVAL_TOP_K=8 对齐。
DEFAULT_GENERATE_TOP_K = 8


# ---------------------------------------------------------------------------
# PDF 解析与切分
# ---------------------------------------------------------------------------


def scan_pdf_directory(directory: Path) -> dict[str, Path]:
    """扫描目录下所有 PDF 文件，返回 ``{文件名: 路径}`` 字典。"""
    return {p.name: p for p in directory.glob("*.pdf")}


def _parse_and_chunk_pdfs(
    pdf_paths: dict[str, Path],
    config: ChunkerConfig,
) -> tuple[list[Chunk], dict[int, str]]:
    """解析并切分多份 PDF，返回合并 chunk 列表与 ``chunk_index → 文档名`` 映射。

    合并库场景下 chunk_index 全局连续编号（与 ``evaluate.py`` 一致），通过返回
    的映射可在检索后还原每个 chunk 的来源文档名（``ContextPiece.document_name``）。

    Args:
        pdf_paths: ``{文件名: 路径}`` 映射。
        config: 切分配置。

    Returns:
        (所有 chunk 合并列表, {全局 chunk_index: 文档名})。
    """
    all_chunks: list[Chunk] = []
    chunk_index_to_name: dict[int, str] = {}
    global_index = 0
    for name, path in pdf_paths.items():
        try:
            parse_result = parse_pdf(path)
        except Exception as exc:
            print(f"错误: 解析 {name} 失败: {exc}", file=sys.stderr)
            raise
        raw_chunks = chunk_pages(parse_result.pages, config)
        for c in raw_chunks:
            reindexed = Chunk(
                start_page=c.start_page,
                end_page=c.end_page,
                chunk_index=global_index,
                content=c.content,
                char_count=c.char_count,
            )
            all_chunks.append(reindexed)
            chunk_index_to_name[global_index] = name
            global_index += 1
    return all_chunks, chunk_index_to_name


def _resolve_pdf_paths(pdfs_dir: Path, dataset_path: Path) -> dict[str, Path] | int:
    """扫描目录下 PDF，按数据集 ``pdf`` 字段筛选出引用的 PDF。

    Args:
        pdfs_dir: PDF 所在目录。
        dataset_path: 数据集 JSON 路径（用于确定需要哪些 PDF）。

    Returns:
        成功时返回 ``{文件名: 路径}``；失败时返回退出码。
    """
    if not pdfs_dir.is_dir():
        print(f"错误: 目录不存在: {pdfs_dir}", file=sys.stderr)
        return 1
    all_pdfs = scan_pdf_directory(pdfs_dir)
    if not all_pdfs:
        print(f"错误: 目录下未找到 PDF 文件: {pdfs_dir}", file=sys.stderr)
        return 1
    entries = load_dataset(dataset_path)
    referenced = {e.pdf for e in entries if e.pdf}
    if not referenced:
        print("错误: 数据集条目无 pdf 字段，无法定位 PDF", file=sys.stderr)
        return 1
    missing = referenced - set(all_pdfs)
    if missing:
        print(
            f"错误: 数据集引用的 PDF 在目录中未找到: {sorted(missing)}",
            file=sys.stderr,
        )
        return 1
    return {name: path for name, path in all_pdfs.items() if name in referenced}


# ---------------------------------------------------------------------------
# 生成阶段：检索 → 重排 → 调 LLM
# ---------------------------------------------------------------------------


def _results_to_contexts(
    results: Sequence[RetrievalResult],
    chunk_index_to_name: dict[int, str],
) -> list[ContextPiece]:
    """把检索结果转为 ``ContextPiece``，用 ``chunk_index`` 还原文档名。

    ``RetrievalResult`` 不含 ``document_name``，通过合并库的 ``chunk_index → 文档名``
    映射补全。``[C1]`` 编号即对应此列表顺序（与 ``answer_question`` 的 prompt 构造一致）。
    """
    contexts: list[ContextPiece] = []
    for r in results:
        contexts.append(
            ContextPiece(
                document_name=chunk_index_to_name.get(r.chunk_index, ""),
                start_page=r.start_page,
                end_page=r.end_page,
                chunk_index=r.chunk_index,
                content=r.content,
                score=r.score,
            )
        )
    return contexts


def _generate_answer(
    question: str,
    contexts: list[ContextPiece],
    generator: BaseChatModel,
) -> tuple[str, list[int], str]:
    """调 generator LLM 生成答案，返回 ``(answer_text, citation_indices, error)``。

    证据不足（``InsufficientEvidenceError``）视为合法结果：答案记为
    ``[INSUFFICIENT_EVIDENCE]``，引用为空，error 为空（交由 judge 评分）。
    LLM 调用失败（``LlmServiceError``）记 error，answer_text 为空，跳过 judge。

    Returns:
        (answer_text, citation_indices, error)。error 非空表示生成失败。
    """
    if not contexts:
        return "", [], "检索上下文为空，跳过生成"
    try:
        result = answer_question(question, contexts, generator)
    except InsufficientEvidenceError:
        # 证据不足是合法答案：模型主动拒绝，忠实度应高（未编造），
        # 完整性应低（未回答）。记为特殊答案交由 judge 评分。
        return INSUFFICIENT_EVIDENCE_MARKER, [], ""
    except LlmServiceError as exc:
        return "", [], f"生成 LLM 调用失败：{exc}"
    return result.answer_text, result.citation_indices, ""


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def _load_generator_config_from_env() -> LlmConfig:
    """从环境变量加载 generator LLM 配置（读 ``LLM_*``）。"""
    return LlmConfig(
        base_url=os.environ.get("LLM_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
        model=os.environ.get("LLM_MODEL", ""),
    )


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------


def _fmt_score(j: AnswerJudgement, attr: str) -> str:
    """格式化单项评分为展示文本（None 显示 ``-``）。"""
    val = getattr(j, attr)
    if val is None:
        return "-"
    return f"{val.score:.1f}"


def _truncate(text: str, width: int = 60) -> str:
    """截断文本到指定宽度，超出加省略号；移除换行避免破坏 markdown 表格。"""
    single = " ".join(text.split())
    return single if len(single) <= width else single[:width] + "…"


def format_answer_quality_report(
    result: AnswerEvaluationResult,
    *,
    dataset_path: Path,
    generator_model: str,
    judge_model: str,
    embedding_model: str,
    reranker_model: str,
    bm25_enabled: bool,
    cross_page: bool,
    top_k: int,
    num_insufficient: int,
) -> str:
    """把评测结果格式化为 Markdown 报告。

    报告结构对齐 ``docs/evaluation_report.md``：目标 → 方法 → 环境 → 数据集 →
    结果（汇总 + 明细）→ 失败案例 → 结论。报告不含真实学号姓名。
    """
    lines: list[str] = []
    lines.append("# 答案质量评测报告\n")
    lines.append(
        "> 本报告记录阶段 9.3 答案质量评测的方法、环境、结果与结论，"
        "用 LLM-as-judge 量化生成阶段答案质量。\n"
    )

    # 1. 评测目标
    lines.append("## 1. 评测目标\n")
    lines.append("扩展检索评测到生成阶段，量化 LLM 答案质量，回答：\n")
    lines.append("1. **忠实度**：答案是否基于检索上下文，有无幻觉？")
    lines.append("2. **相关性**：答案是否直接回答了用户问题？")
    lines.append("3. **完整性**：答案是否覆盖了上下文中的相关要点？")
    lines.append("4. **引用正确性**：答案中 `[C1]` 标记是否正确指向支持性上下文？\n")

    # 2. 评测方法
    lines.append("## 2. 评测方法\n")
    lines.append(
        "**LLM-as-judge**：用一个 LLM 对（问题、上下文、答案）三元组打分，"
        "四项指标各 1-5 分（5 分最好），并给出简短理由。引用正确性辅以服务端"
        "客观规则校验（编号是否越界、是否有引用）。\n"
    )
    lines.append("| 指标 | 含义 | 5 分 | 1 分 |")
    lines.append("|---|---|---|---|")
    lines.append("| faithfulness 忠实度 | 答案声明是否被上下文支持 | 全部有据无幻觉 | 大量编造 |")
    lines.append("| relevancy 相关性 | 答案是否回答问题 | 完全切题 | 完全跑题 |")
    lines.append("| completeness 完整性 | 是否覆盖上下文相关要点 | 完整覆盖 | 严重遗漏 |")
    lines.append(
        "| citation_correctness 引用正确性 | `[C1]` 是否正确指向支持性上下文 | 全部正确 | 无引用/全错 |"
    )
    lines.append("")
    lines.append("> 均值仅统计成功解析的题；解析失败的题不计入分子分母，避免拉低分数。\n")

    # 3. 评测环境
    lines.append("## 3. 评测环境\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| Generator LLM | `{generator_model or '(未配置)'}` |")
    lines.append(f"| Judge LLM | `{judge_model or '(未配置)'}` |")
    lines.append(f"| Embedding 模型 | `{embedding_model}` |")
    lines.append(f"| Reranker 模型 | `{reranker_model or '未启用'}` |")
    lines.append(f"| BM25 混合检索 | {'启用' if bm25_enabled else '未启用'} |")
    lines.append(f"| 跨页切分 | {'启用' if cross_page else '关闭'} |")
    lines.append(f"| 生成阶段 top_k | {top_k} |")
    lines.append(f"| 数据集 | `{dataset_path}` |")
    lines.append("| Python | 3.11 |")
    lines.append("")

    # 4. 评测数据集
    lines.append("## 4. 评测数据集\n")
    lines.append(
        f"共 {result.num_questions} 条问题，每条跑完整 RAG 流程"
        "（检索 → 重排 → LLM 生成 → judge 打分）。\n"
    )

    # 5. 评测结果
    lines.append("## 5. 评测结果\n")
    lines.append("### 5.1 汇总指标\n")
    lines.append("| 忠实度 | 相关性 | 完整性 | 引用正确性 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| {result.avg_faithfulness:.2f} | {result.avg_relevancy:.2f} | "
        f"{result.avg_completeness:.2f} | {result.avg_citation_correctness:.2f} |"
    )
    lines.append("")

    # 客观统计
    has_cite = sum(1 for j in result.per_question if j.citation_check.has_citation)
    all_in_bounds = sum(1 for j in result.per_question if j.citation_check.in_bounds)
    lines.append("### 5.2 引用客观校验统计\n")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| 含引用标记的答案数 | {has_cite} / {result.num_questions} |")
    lines.append(
        f"| 引用全部在范围内（无越界）的答案数 | {all_in_bounds} / {result.num_questions} |"
    )
    lines.append(f"| 证据不足（[INSUFFICIENT_EVIDENCE]）答案数 | {num_insufficient} |")
    lines.append(f"| judge 解析失败数 | {result.num_parse_errors} |")
    lines.append("")

    # 5.3 每条问题明细
    lines.append("### 5.3 每条问题得分\n")
    lines.append("| # | 问题 | 忠实度 | 相关性 | 完整性 | 引用正确性 | 引用校验 | 备注 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, j in enumerate(result.per_question, 1):
        cite_label = "有引用" if j.citation_check.has_citation else "无引用"
        if not j.citation_check.in_bounds:
            cite_label += f"(越界{list(j.citation_check.out_of_bounds_indices)})"
        note = j.parse_error or (
            "证据不足" if j.raw_response == "" and j.faithfulness is None else ""
        )
        lines.append(
            f"| {i} | {_truncate(j.question)} | {_fmt_score(j, 'faithfulness')} | "
            f"{_fmt_score(j, 'relevancy')} | {_fmt_score(j, 'completeness')} | "
            f"{_fmt_score(j, 'citation_correctness')} | {cite_label} | {note} |"
        )
    lines.append("")

    # 6. 失败案例分析（任一指标 <=2 或 parse_error 非空）
    low_score: list[AnswerJudgement] = []
    for j in result.per_question:
        if j.parse_error:
            low_score.append(j)
            continue
        scores = [
            s.score
            for s in (j.faithfulness, j.relevancy, j.completeness, j.citation_correctness)
            if s is not None
        ]
        if scores and min(scores) <= 2:
            low_score.append(j)

    lines.append("## 6. 失败案例分析\n")
    if not low_score:
        lines.append("无低分（任一指标 ≤ 2）或解析失败的 case。\n")
    else:
        lines.append(f"共 {len(low_score)} 条低分或失败 case（任一指标 ≤ 2 或解析失败）：\n")
        for i, j in enumerate(low_score, 1):
            lines.append(f"### 案例 {i}：{_truncate(j.question, 80)}\n")
            if j.parse_error:
                lines.append(f"**错误**：{j.parse_error}\n")
                continue
            lines.append(
                f"- 忠实度: {_fmt_score(j, 'faithfulness')} — "
                f"{j.faithfulness.reason if j.faithfulness else ''}"
            )
            lines.append(
                f"- 相关性: {_fmt_score(j, 'relevancy')} — "
                f"{j.relevancy.reason if j.relevancy else ''}"
            )
            lines.append(
                f"- 完整性: {_fmt_score(j, 'completeness')} — "
                f"{j.completeness.reason if j.completeness else ''}"
            )
            lines.append(
                f"- 引用正确性: {_fmt_score(j, 'citation_correctness')} — "
                f"{j.citation_correctness.reason if j.citation_correctness else ''}"
            )
            lines.append("")

    # 7. 结论
    lines.append("## 7. 结论与改进方向\n")
    lines.append("- **忠实度**反映幻觉控制效果，低分提示需加强 Prompt 约束或检索质量。")
    lines.append(
        "- **相关性**低分多为检索未命中（问题与上下文语义距离远），可结合检索 Hit@5 分析。"
    )
    lines.append("- **完整性**低分提示上下文信息不足或切分过细导致答案片面。")
    lines.append(
        "- **引用正确性**低分结合客观校验：无引用需加强 Prompt 引用约束；越界需检查模型编号输出。"
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主编排
# ---------------------------------------------------------------------------


def run_answer_evaluation(
    pdf_paths: dict[str, Path],
    dataset_path: Path,
    output_path: Path,
    *,
    embedding_model: str | None,
    embedding_provider: str | None,
    reranker_model: str | None,
    cross_page: bool,
    bm25_enabled: bool,
    top_k: int,
) -> int:
    """运行答案质量评测：切分 → 索引 → 逐条（检索→生成→打分）→ 汇总报告。

    Args:
        pdf_paths: ``{文件名: 路径}`` 映射。
        dataset_path: 数据集 JSON 路径。
        output_path: 报告输出路径。
        embedding_model: Embedding 模型名。
        embedding_provider: Embedding 提供方。
        reranker_model: Reranker 模型名（None 跳过重排）。
        cross_page: 是否启用跨页切分。
        bm25_enabled: 是否启用 BM25 混合检索。
        top_k: 生成阶段检索的上下文数量。

    Returns:
        退出码。
    """
    entries = load_dataset(dataset_path)
    print(f"已加载数据集：{len(entries)} 条")

    # Embedding 配置（与 evaluate.py 一致的 provider 分支）
    provider = (embedding_provider or "local").strip().lower()
    if provider == "dashscope":
        model_name = embedding_model or DASHSCOPE_DEFAULT_MODEL
        emb_config = EmbeddingConfig(provider="dashscope", model_name=model_name)
    elif provider == "jina":
        model_name = embedding_model or JINA_DEFAULT_MODEL
        emb_config = EmbeddingConfig(provider="jina", model_name=model_name)
    else:
        model_name = embedding_model or DEFAULT_EMBEDDING_MODEL
        emb_config = EmbeddingConfig(model_name=model_name)
    try:
        embeddings = create_embeddings(emb_config)
    except EmbeddingServiceError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    # Reranker
    reranker: BaseReranker | None = None
    if reranker_model is not None:
        try:
            reranker = create_reranker(RerankerConfig(model_name=reranker_model))
        except RerankerError as exc:
            print(f"错误: 创建 Reranker 失败: {exc}", file=sys.stderr)
            return 2

    # Generator LLM 配置校验
    generator_config = _load_generator_config_from_env()
    if not generator_config.api_key or not generator_config.model:
        print(
            "错误: 未配置 LLM_API_KEY / LLM_MODEL 环境变量，无法调用生成 LLM。",
            file=sys.stderr,
        )
        return 2
    generator = create_chat_model(generator_config)

    # Judge LLM（默认回退到 generator 同一配置）
    judge_config = load_judge_config_from_env()
    if not judge_config.api_key or not judge_config.model:
        print(
            "错误: 未配置 JUDGE_LLM_API_KEY/JUDGE_LLM_MODEL 或 LLM_API_KEY/LLM_MODEL，"
            "无法调用 judge LLM。",
            file=sys.stderr,
        )
        return 2
    judge = create_chat_model(judge_config)

    print(f"Embedding 模型: {model_name} (provider={provider})")
    print(f"Reranker 模型: {reranker_model or '未启用'}")
    print(f"Generator LLM: {generator_config.model}")
    print(f"Judge LLM: {judge_config.model}")
    print(f"跨页切分: {'启用' if cross_page else '关闭'}")
    print(f"BM25 混合检索: {'启用' if bm25_enabled else '未启用'}")
    print(f"生成 top_k: {top_k}")
    print(f"待评测 PDF: {len(pdf_paths)} 份")
    print("=" * 70)

    # 切分（生产默认参数 chunk-500-overlap-80 + 跨页切分）
    chunker_config = ChunkerConfig(cross_page=cross_page)
    all_chunks, chunk_index_to_name = _parse_and_chunk_pdfs(pdf_paths, chunker_config)
    print(f"共 {len(all_chunks)} chunks")
    store = index_chunks(all_chunks, embeddings)

    bm25_retriever: BM25Retriever | None = BM25Retriever(all_chunks) if bm25_enabled else None

    judgements: list[AnswerJudgement] = []
    num_insufficient = 0
    for i, entry in enumerate(entries, 1):
        # 1. 检索（向量 or BM25 混合）
        start = time.perf_counter()
        if bm25_retriever is not None:
            results = hybrid_retrieve(store, bm25_retriever, entry.question, top_k=top_k)
        else:
            results = retrieve(store, entry.question, top_k=top_k)
        # 2. 重排
        if reranker is not None:
            results = rerank_results(reranker, entry.question, results)
        latency_ms = (time.perf_counter() - start) * 1000

        # 3. 生成
        contexts = _results_to_contexts(results, chunk_index_to_name)
        answer_text, citation_indices, gen_error = _generate_answer(
            entry.question, contexts, generator
        )
        if answer_text == INSUFFICIENT_EVIDENCE_MARKER:
            num_insufficient += 1

        # 4. judge 打分（生成失败时构造错误判定，不调 judge）
        if gen_error:
            from research_rag.answer_evaluation import check_citations

            citation_check = check_citations(citation_indices, contexts)
            judgements.append(
                AnswerJudgement(
                    question=entry.question,
                    faithfulness=None,
                    relevancy=None,
                    completeness=None,
                    citation_correctness=None,
                    citation_check=citation_check,
                    raw_response="",
                    parse_error=gen_error,
                )
            )
        else:
            sample = AnswerSample(
                question=entry.question,
                contexts=contexts,
                answer_text=answer_text,
                citation_indices=citation_indices,
            )
            judgements.append(judge_answer(sample, judge))

        j = judgements[-1]
        print(
            f"  [{i}/{len(entries)}] ({latency_ms:.0f}ms) "
            f"F={_fmt_score(j, 'faithfulness')} R={_fmt_score(j, 'relevancy')} "
            f"C={_fmt_score(j, 'completeness')} Cite={_fmt_score(j, 'citation_correctness')} "
            f"{_truncate(entry.question, 40)}"
        )

    result = aggregate_judgements(judgements)

    print("\n" + "=" * 70)
    print("汇总结果\n")
    print(
        f"  忠实度={result.avg_faithfulness:.2f}  相关性={result.avg_relevancy:.2f}  "
        f"完整性={result.avg_completeness:.2f}  引用正确性={result.avg_citation_correctness:.2f}"
    )
    print(f"  证据不足: {num_insufficient}  解析失败: {result.num_parse_errors}")

    # 生成报告
    report = format_answer_quality_report(
        result,
        dataset_path=dataset_path,
        generator_model=generator_config.model,
        judge_model=judge_config.model,
        embedding_model=model_name,
        reranker_model=reranker_model or "",
        bm25_enabled=bm25_enabled,
        cross_page=cross_page,
        top_k=top_k,
        num_insufficient=num_insufficient,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\n报告已写入: {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """脚本入口。"""
    parser = argparse.ArgumentParser(
        description="阶段 9.3 答案质量评测：LLM-as-judge 评分 + 报告生成",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行答案质量评测")
    p_run.add_argument(
        "--pdfs-dir",
        type=Path,
        required=True,
        help="PDF 所在目录（多 PDF 模式，按数据集 pdf 字段匹配）",
    )
    p_run.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"数据集 JSON 路径，默认 {DEFAULT_DATASET_PATH}",
    )
    p_run.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"报告输出路径，默认 {DEFAULT_OUTPUT_PATH}",
    )
    p_run.add_argument(
        "--embedding-model",
        type=str,
        default=None,
        help="Embedding 模型名（默认生产配置；评测英文论文建议 BAAI/bge-small-en-v1.5）",
    )
    p_run.add_argument(
        "--embedding-provider",
        type=str,
        default=None,
        choices=["local", "dashscope", "jina"],
        help="Embedding 提供方：local / dashscope / jina",
    )
    p_run.add_argument(
        "--reranker-model",
        type=str,
        default=None,
        help=f"Reranker 模型名，默认不启用。推荐 {DEFAULT_RERANKER_MODEL}",
    )
    p_run.add_argument(
        "--no-cross-page",
        action="store_true",
        default=False,
        help="关闭跨页切分，退回按页独立切分",
    )
    p_run.add_argument(
        "--bm25",
        action="store_true",
        default=False,
        help="启用 BM25 混合检索（中文场景需 `uv sync --extra chinese`）",
    )
    p_run.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_GENERATE_TOP_K,
        help=f"生成阶段检索的上下文数量，默认 {DEFAULT_GENERATE_TOP_K}",
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        pdf_paths = _resolve_pdf_paths(args.pdfs_dir, args.dataset)
        if isinstance(pdf_paths, int):
            return pdf_paths
        return run_answer_evaluation(
            pdf_paths,
            args.dataset,
            args.output,
            embedding_model=args.embedding_model,
            embedding_provider=args.embedding_provider,
            reranker_model=args.reranker_model,
            cross_page=not args.no_cross_page,
            bm25_enabled=args.bm25,
            top_k=args.top_k,
        )

    return 1


if __name__ == "__main__":
    sys.exit(main())
