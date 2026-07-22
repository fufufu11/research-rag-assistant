"""BM25 + 向量混合检索（Hybrid Retrieval）。

依据 ``docs/ROADMAP.md`` 阶段 8.3、``docs/evaluation_report.md`` 第 6 节
（9 条 Hit@5 未命中的失败模式分析）。

设计取舍（初学者向说明）：
- **为什么需要混合检索**：向量检索（Bi-Encoder）将 query 和文档独立编码，
  对语义相似但词面不同的情况有优势，但对"关键词列表"、"数值"、"公式"类
  问题召回弱（评测显示 9 条 Hit@5 未命中里有 4 条属于这些类别）。BM25 基于
  词频和逆文档频率，对关键词精确匹配有天然优势，两者互补。
- **并行召回 + 融合，而非串行**：BM25 与向量各自召回 Top-K，取并集后融合。
  串行（向量召回 → BM25 重排）会损失 BM25 独立召回的能力，并行才能让两种
  召回的命中都进入候选池。
- **RRF 而非加权融合**：``Reciprocal Rank Fusion`` 公式 ``score = sum(1/(k+rank))``
  只依赖排名，不依赖原始分数分布。BM25 分数（0-数十）与余弦相似度（0-1）
  尺度差异大，加权融合需归一化与调参，RRF 天然鲁棒且无超参（``k=60`` 是
  原论文经典默认值）。
- **中文分词可选**：BM25 对中文必须先分词才有意义。``jieba`` 作为可选依赖
  （``chinese`` extra），未装时 fallback 到字符级切分（精度降低但可用），
  英文场景直接用 ``\\w+`` 正则切词即可。``tokenize`` 函数惰性导入 jieba。
- **不持久化 BM25 索引**：每次问答时从 DB 读取 READY 文档的 chunk 重建索引。
  理由：① 文档增删后需同步更新 BM25 索引，持久化引入一致性问题；② 当前
  规模（数百 chunk）重建 < 100ms，可接受；③ 后续阶段 10 性能优化时再加缓存。
- **融合后送入 reranker**：RRF 融合的 Top-K 结果可继续交给 Cross-Encoder
  精排，形成"BM25+向量召回 → RRF 融合 → Cross-Encoder 精排"三阶段流水线。
- **返回 ``RetrievalResult`` 兼容现有接口**：与 ``embedding.retrieve`` 返回
  类型一致，``rerank_results`` 泛型函数可直接处理，``QaService`` 不需改动
  引用映射逻辑。
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from research_rag.embedding import RetrievalResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.vectorstores import InMemoryVectorStore

    from research_rag.chunker import Chunk

# RRF 默认 k 值（原论文《Reciprocal Rank Fusion》推荐 60）
# k 越大，排名差异对融合分数的影响越平缓；k 越小，排名靠前的结果优势越大。
DEFAULT_RRF_K = 60
# 混合检索时各路召回的 Top-K（融合前的候选池大小）
# 取 top_k * 2 让两路召回有充足的候选进入融合，避免正确结果被截断
DEFAULT_RECALL_MULTIPLIER = 2
# 加权 RRF：向量检索默认 2 倍权重，BM25 默认 1 倍
# 理由：英文论文场景下向量检索（bge-small-en-v1.5）召回质量已较高，
# BM25 引入的噪声文档若与向量等权，会在 RRF 融合中挤出向量召回的好文档。
# 给向量 2 倍权重让 BM25 召回的文档需有较高排名才能进入最终 Top-K，
# 同时保留 BM25 对关键词精确匹配的补充能力（BM25 排名靠前的文档仍能胜出）。
DEFAULT_VECTOR_WEIGHT = 2.0
DEFAULT_BM25_WEIGHT = 1.0
# 判定文本是否含 CJK 字符的正则（中日韩统一表意文字 + 扩展 A 区）
# 用于决定是否尝试 jieba 分词（仅含 CJK 时 jieba 才有意义）
_CJK_PATTERN = re.compile(
    r"[\u4e00-\u9fff"  # CJK 统一表意文字
    r"\u3400-\u4dbf"  # CJK 扩展 A
    r"]"
)
# 通用分词正则：匹配连续的字母数字下划线（英文单词、数字、变量名）或单个 CJK 字符
# 作为 jieba 不可用时的 fallback
_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


class HybridRetrievalError(RuntimeError):
    """混合检索异常。

    当 BM25 索引构建失败、依赖未安装或检索失败时抛出。
    与 ``VectorStoreError`` / ``RerankerError`` 并列，归一化底层依赖错误。
    """


@dataclass(frozen=True)
class BM25Config:
    """BM25 配置。

    Attributes:
        k1: 词频饱和参数，控制词频对分数的影响上限。经典值 1.2-2.0，默认 1.5。
            增大 ``k1`` 让高频词更占优；减小则更关注词是否出现而非出现几次。
        b: 文档长度归一化参数，0-1 之间。经典值 0.75。
            ``b=1`` 完全按文档长度归一化（长文档惩罚强），``b=0`` 不归一化。
        use_jieba: 是否尝试用 jieba 分词（仅对含 CJK 字符的文本生效）。
            ``True``（默认）时惰性导入 jieba，未装时自动 fallback 到字符切分。
            纯英文场景下此参数无效（直接用 ``\\w+`` 正则切词）。
    """

    k1: float = 1.5
    b: float = 0.75
    use_jieba: bool = True


def _contains_cjk(text: str) -> bool:
    """判断文本是否含 CJK 字符（用于决定是否调用 jieba 分词）。"""

    return bool(_CJK_PATTERN.search(text))


def tokenize(text: str, use_jieba: bool = True) -> list[str]:
    """分词函数：含 CJK 字符时优先 jieba，否则用 ``\\w+`` 正则切词。

    BM25 对文本必须先分词才能计算词频。中文没有空格分隔，必须用分词工具；
    英文用空格切分即可，``\\w+`` 正则同时处理数字和下划线变量名。

    jieba 不可用时（未装 ``chinese`` extra）：
    - 含 CJK 字符的文本：fallback 到字符级切分（每个 CJK 字符是一个 token）
      —— 精度低于词级切分，但 BM25 仍可工作（单字频率仍有区分度）
    - 纯英文文本：不受影响（本就走 ``\\w+`` 路径）

    Args:
        text: 待分词文本。
        use_jieba: 是否尝试用 jieba。``False`` 时直接走 fallback 路径，
            用于测试或强制字符切分。

    Returns:
        分词后的 token 列表（保留顺序，不含空 token）。
    """
    if not text:
        return []

    # 纯英文/数字场景：直接用 \w+ 切词（jieba 对英文无意义且拖慢）
    has_cjk = _contains_cjk(text)
    if not has_cjk:
        return _WORD_PATTERN.findall(text)

    # 含 CJK 字符：尝试 jieba 词级分词
    if use_jieba:
        try:
            import jieba

            # cut_for_search 比 cut 更细粒度，适合搜索引擎场景（提升召回）
            tokens: list[str] = [t for t in jieba.cut_for_search(text) if t.strip()]
            return tokens
        except ImportError:
            # jieba 未装：fallback 到字符级切分
            pass

    # Fallback：CJK 字符按单字切，英文按连续 \w 字符合并
    # 标点/空格会打断连续 ASCII（触发 flush），避免 "attention, transformer"
    # 被错误合并为 "attentiontransformer"
    fallback_tokens: list[str] = []
    ascii_buffer: list[str] = []

    def _flush_buffer() -> None:
        if ascii_buffer:
            fallback_tokens.append("".join(ascii_buffer))
            ascii_buffer.clear()

    for char in text:
        if _CJK_PATTERN.match(char):
            _flush_buffer()
            fallback_tokens.append(char)
        elif _WORD_PATTERN.match(char):
            ascii_buffer.append(char)
        else:
            # 标点/空格：打断连续 ASCII 字母数字
            _flush_buffer()

    _flush_buffer()
    return fallback_tokens


class BM25Retriever:
    """基于 ``rank_bm25.BM25Okapi`` 的稀疏检索器。

    构造时预分词并建索引，``retrieve`` 时只需对 query 分词后查表打分。
    适合"一次建索引多次检索"场景（如评测脚本对一组 chunks 检索多个 query）。

    惰性导入 ``rank_bm25``，未装时抛 ``HybridRetrievalError``（与
    ``create_embeddings`` / ``create_reranker`` 风格一致）。
    """

    def __init__(
        self,
        chunks: Sequence[Chunk],
        config: BM25Config | None = None,
    ) -> None:
        """构建 BM25 索引。

        Args:
            chunks: 待索引的 Chunk 列表（``chunker.Chunk``，与 ``index_chunks``
                接受的类型一致）。
            config: BM25 配置，为 ``None`` 时用默认值（``k1=1.5``, ``b=0.75``）。

        Raises:
            HybridRetrievalError: ``rank_bm25`` 未安装或索引构建失败。
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError as exc:
            msg = f"无法导入 rank_bm25，请确认已安装 rank-bm25。原始错误：{exc}"
            raise HybridRetrievalError(msg) from exc

        self.config = config or BM25Config()
        self._chunks: list[Chunk] = list(chunks)

        if not self._chunks:
            # 空输入：BM25Okapi 对空语料会抛异常，这里直接存空列表，retrieve 时返回空
            self._bm25: Any = None
            return

        # 预分词：对每个 chunk 内容调用 tokenize
        try:
            tokenized_corpus = [tokenize(c.content, self.config.use_jieba) for c in self._chunks]
        except Exception as exc:
            msg = f"BM25 分词失败：{exc}"
            raise HybridRetrievalError(msg) from exc

        try:
            self._bm25 = BM25Okapi(
                tokenized_corpus,
                k1=self.config.k1,
                b=self.config.b,
            )
        except Exception as exc:
            msg = f"BM25 索引构建失败：{exc}"
            raise HybridRetrievalError(msg) from exc

    def retrieve(
        self,
        query: str,
        top_k: int = 8,
    ) -> list[RetrievalResult]:
        """对 query 检索 Top-K 相关片段。

        Args:
            query: 查询文本。
            top_k: 返回的最相关片段数。

        Returns:
            检索结果列表（按 BM25 分数降序），与 ``embedding.retrieve``
            返回类型一致（``RetrievalResult``）。空索引或无命中时返回空列表。

        Raises:
            HybridRetrievalError: 检索失败或 ``top_k`` 非正。
        """
        if top_k <= 0:
            msg = f"top_k 必须为正整数，收到 {top_k}"
            raise HybridRetrievalError(msg)

        if self._bm25 is None or not self._chunks:
            return []

        try:
            query_tokens = tokenize(query, self.config.use_jieba)
            if not query_tokens:
                # query 全是标点/空白：BM25 无法匹配，返回空
                return []
            scores = self._bm25.get_scores(query_tokens)
        except Exception as exc:
            msg = f"BM25 检索失败：{exc}"
            raise HybridRetrievalError(msg) from exc

        # scores 是 numpy.ndarray，按分数降序取 Top-K
        # 排除分数 <= 0 的结果（BM25 分数可能为 0 表示无共同词）
        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        results: list[RetrievalResult] = []
        for idx, score in indexed:
            if float(score) <= 0:
                continue
            if len(results) >= top_k:
                break
            chunk = self._chunks[idx]
            results.append(
                RetrievalResult(
                    start_page=chunk.start_page,
                    end_page=chunk.end_page,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    score=float(score),
                )
            )
        return results


def rrf_fusion(
    vector_results: Sequence[RetrievalResult],
    bm25_results: Sequence[RetrievalResult],
    k: int = DEFAULT_RRF_K,
    top_k: int | None = None,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[RetrievalResult]:
    """Reciprocal Rank Fusion（RRF）融合两路检索结果。

    公式：``fused_score(doc) = sum(weight_i / (k + rank_i(doc)))``，其中
    ``rank_i(doc)`` 是 doc 在第 i 路结果中的排名（从 1 开始），
    ``weight_i`` 是该路的权重因子。仅出现在某一路的结果，另一路贡献 0。

    RRF 只依赖排名，不依赖原始分数分布，对 BM25 分数和余弦相似度的尺度差异
    天然鲁棒，无需归一化。加权 RRF 允许给召回质量更高的一路更大权重，
    减少另一路噪声文档的干扰（``hybrid_retrieve`` 默认向量 2 倍权重）。

    Args:
        vector_results: 向量检索结果列表（按相关度降序）。
        bm25_results: BM25 检索结果列表（按相关度降序）。
        k: RRF 平滑参数，默认 60（原论文推荐值）。``k`` 越大，排名差异影响越平缓。
        top_k: 融合后返回的最相关片段数。``None`` 返回全部融合结果（仅排序）。
        vector_weight: 向量检索的权重因子（默认 1.0）。``hybrid_retrieve`` 传入
            ``DEFAULT_VECTOR_WEIGHT=2.0``，让向量召回占更大比重。
        bm25_weight: BM25 检索的权重因子（默认 1.0）。

    Returns:
        融合后的 ``RetrievalResult`` 列表，按融合分数降序。``score`` 字段更新为
        RRF 分数（与原始 BM25 / 余弦分数不同尺度）。两路都没有命中的文档
        不会出现在结果中。

    Note:
        用 ``content`` 字段作为文档唯一键（同一 chunk 在两路结果中 content 相同）。
        若同一 content 在两路结果中排名不同，分别累加贡献。
    """
    if not vector_results and not bm25_results:
        return []

    # content → (累计 RRF 分数, 代表 RetrievalResult)
    # 代表 RetrievalResult 优先用 vector_results 的（保留余弦 score 语义更直观）
    # 但若仅 BM25 召回，则用 BM25 的（score 为 BM25 分数）
    scores: dict[str, float] = {}
    representatives: dict[str, RetrievalResult] = {}

    for rank, result in enumerate(vector_results):
        if result.content not in scores:
            scores[result.content] = 0.0
            representatives[result.content] = result
        scores[result.content] += vector_weight / (k + rank + 1)

    for rank, result in enumerate(bm25_results):
        if result.content not in scores:
            scores[result.content] = 0.0
            representatives[result.content] = result
        scores[result.content] += bm25_weight / (k + rank + 1)

    # 按融合分数降序
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if top_k is not None and top_k > 0:
        sorted_items = sorted_items[:top_k]

    return [
        dataclasses.replace(representatives[content], score=score)
        for content, score in sorted_items
    ]


def hybrid_retrieve(
    store: InMemoryVectorStore,
    bm25_retriever: BM25Retriever,
    query: str,
    top_k: int = 8,
    rrf_k: int = DEFAULT_RRF_K,
    recall_multiplier: int = DEFAULT_RECALL_MULTIPLIER,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    bm25_weight: float = DEFAULT_BM25_WEIGHT,
) -> list[RetrievalResult]:
    """混合检索：BM25 + 向量并行召回 + 加权 RRF 融合。

    两路各自召回 ``top_k * recall_multiplier`` 条候选，取并集后用加权 RRF 融合，
    最终返回 ``top_k`` 条。多召回是为了让两路的命中都能进入融合候选池，
    避免正确结果在某一路排名靠后被截断。

    默认给向量检索 2 倍权重（``DEFAULT_VECTOR_WEIGHT=2.0``），因为英文论文场景
    下向量检索召回质量已较高，BM25 独有召回的噪声文档若等权会挤出向量好文档。
    BM25 仍能补充关键词精确匹配（排名靠前的 BM25 文档仍能进入 Top-K）。

    Args:
        store: 已索引的 ``InMemoryVectorStore``（``index_chunks`` 的返回值）。
        bm25_retriever: 已建好索引的 ``BM25Retriever`` 实例。
        query: 查询文本。
        top_k: 最终返回的最相关片段数。
        rrf_k: RRF 平滑参数，默认 60。
        recall_multiplier: 召回倍数，``recall_k = top_k * recall_multiplier``。
        vector_weight: 向量检索权重（默认 2.0）。
        bm25_weight: BM25 检索权重（默认 1.0）。

    Returns:
        融合后的 ``RetrievalResult`` 列表，按 RRF 分数降序，长度 <= ``top_k``。

    Raises:
        HybridRetrievalError: BM25 检索失败。
        VectorStoreError: 向量检索失败或 ``top_k`` 非正。
    """
    from research_rag.embedding import retrieve as vector_retrieve

    recall_k = max(top_k * recall_multiplier, top_k)
    vector_results = vector_retrieve(store, query, top_k=recall_k)
    bm25_results = bm25_retriever.retrieve(query, top_k=recall_k)

    return rrf_fusion(
        vector_results,
        bm25_results,
        k=rrf_k,
        top_k=top_k,
        vector_weight=vector_weight,
        bm25_weight=bm25_weight,
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_bm25_config() -> BM25Config:
    """从环境变量构造 ``BM25Config``。

    环境变量（.env.example）：
    - ``BM25_K1``：词频饱和参数（float，默认 1.5）
    - ``BM25_B``：文档长度归一化参数（float，默认 0.75）
    - ``BM25_USE_JIEBA``：是否用 jieba 分词（"true"/"false"，默认 "true"）
    """
    k1 = _parse_float_env("BM25_K1", 1.5)
    b = _parse_float_env("BM25_B", 0.75)
    use_jieba = os.environ.get("BM25_USE_JIEBA", "true").strip().lower() != "false"
    return BM25Config(k1=k1, b=b, use_jieba=use_jieba)


def _parse_float_env(name: str, default: float) -> float:
    """从环境变量解析 float，格式错误时回退到默认值。"""
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def is_bm25_enabled() -> bool:
    """检查环境变量 ``BM25_ENABLED`` 是否启用混合检索。

    ``BM25_ENABLED=true`` 时启用，其他值或未设置时关闭（保持向后兼容）。
    """
    return os.environ.get("BM25_ENABLED", "false").strip().lower() == "true"
