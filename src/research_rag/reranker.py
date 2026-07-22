"""BGE Reranker 重排序模块（Cross-Encoder 两阶段检索）。

本模块实现阶段 8 检索质量优化的第一项：引入 Cross-Encoder 对向量检索
Top-K 结果重排序，提升 Hit@1 和 MRR。

设计取舍（初学者向说明）：
- **两阶段检索**：Bi-Encoder（向量检索）速度快但精度有限，适合从全库召回
  Top-K 候选；Cross-Encoder（Reranker）将 query+document 联合编码，精度更高
  但速度慢，适合对少量候选精排。两者串联是 RAG 系统的标准做法。
- **``BaseReranker`` Protocol 而非 ABC**：与 LangChain ``Embeddings`` 抽象不同，
  本模块用 ``Protocol``（鸭子类型）定义接口，Fake 实现不需要继承任何基类，
  只需实现 ``rerank`` 方法即可。测试注入更轻量。
- **``CrossEncoderReranker`` 是唯一接触真实模型的地方**：惰性导入
  ``sentence_transformers.CrossEncoder``，未装 ``sentence-transformers`` 时抛
  ``RerankerError``，把依赖缺失归一化为业务异常（与 ``create_embeddings`` /
  ``create_chat_model`` 一致）。
- **``rerank_results`` 泛型辅助函数**：接受任何含 ``content`` 和 ``score``
  属性的 frozen dataclass（``RetrievalResult`` / ``QdrantSearchResult`` /
  ``ContextPiece``），用 ``dataclasses.replace`` 创建新实例并更新 score。
  这样调用方不需要手写"取 content → 重排 → 回填 score"的样板代码。
- **重排不截断**：``rerank`` 方法的 ``top_k`` 参数可选，``None`` 时返回全部
  候选（仅重排序）。是否截断由调用方决定，保持 reranker 职责单一。
- **score 语义变化**：向量检索的 score 是余弦相似度（0-1），重排后 score 是
  Cross-Encoder 的相关性分数（可为任意实数）。调用方不应假设 score 范围不变。
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

# 默认 Reranker 模型
# bge-reranker-base：中英文支持，Cross-Encoder 架构，约 278MB
# 替代方案：bge-reranker-v2-m3（多语言，更大）、bge-reranker-large（更高精度）
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"


class RerankerError(RuntimeError):
    """Reranker 服务异常。

    当模型加载失败、依赖未安装或推理失败时抛出。
    """


@dataclass(frozen=True)
class RerankerConfig:
    """Reranker 配置。

    Attributes:
        model_name: HuggingFace 模型名，默认 ``BAAI/bge-reranker-base``。
    """

    model_name: str = DEFAULT_RERANKER_MODEL


@runtime_checkable
class BaseReranker(Protocol):
    """Reranker 抽象接口（鸭子类型 Protocol）。

    生产环境用 ``CrossEncoderReranker``（sentence-transformers CrossEncoder），
    测试时注入 Fake 实现即可，不需要继承本 Protocol（鸭子类型）。

    实现 ``rerank`` 方法即可满足本 Protocol。
    """

    def rerank(
        self,
        query: str,
        contents: Sequence[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """对 ``(query, content)`` 对评分，返回按分数降序的 ``(原始索引, 分数)`` 列表。

        Args:
            query: 用户查询文本。
            contents: 候选文档内容列表（与检索结果一一对应）。
            top_k: 仅返回前 K 条。``None`` 表示返回全部（仅重排序不截断）。

        Returns:
            ``(原始索引, 重排分数)`` 列表，按分数降序排列。
            空输入返回空列表。
        """
        ...


# ---------------------------------------------------------------------------
# 可重排结果协议（用于 rerank_results 泛型约束）
# ---------------------------------------------------------------------------


@runtime_checkable
class _Rerankable(Protocol):
    """可重排结果协议：含 ``content`` 和 ``score`` 属性。

    ``RetrievalResult`` / ``QdrantSearchResult`` / ``ContextPiece`` 均满足
    本协议（都是含这两个属性的 frozen dataclass）。
    """

    content: str
    score: float


# TypeVar bound 到 _Rerankable，使 rerank_results 返回与输入相同的类型
_R = TypeVar("_R", bound="_Rerankable")


# ---------------------------------------------------------------------------
# CrossEncoder 实现
# ---------------------------------------------------------------------------


class CrossEncoderReranker:
    """基于 sentence-transformers CrossEncoder 的 BGE Reranker 实现。

    ``CrossEncoder`` 将 ``(query, document)`` 对联合编码并输出相关性分数，
    精度高于 Bi-Encoder（独立编码），但速度慢（需对每个候选单独推理）。
    适合对向量检索召回的 Top-K 候选精排。

    惰性导入 ``sentence_transformers``，未装时抛 ``RerankerError``。
    """

    def __init__(self, model_name: str = DEFAULT_RERANKER_MODEL) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            msg = (
                "无法导入 sentence_transformers.CrossEncoder，请运行 "
                "`uv sync --extra embedding` 安装推理后端。"
                f"原始错误：{exc}"
            )
            raise RerankerError(msg) from exc

        try:
            # sentence_transformers.CrossEncoder 无 type stubs，用 Any 存储
            self._model: Any = CrossEncoder(model_name)
        except Exception as exc:
            msg = f"加载 CrossEncoder 模型失败（model={model_name}）：{exc}"
            raise RerankerError(msg) from exc

    def rerank(
        self,
        query: str,
        contents: Sequence[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """对 ``(query, content)`` 对评分，返回按分数降序的 ``(索引, 分数)`` 列表。

        Args:
            query: 用户查询文本。
            contents: 候选文档内容列表。
            top_k: 仅返回前 K 条。``None`` 返回全部。

        Returns:
            ``(原始索引, 重排分数)`` 列表，按分数降序。

        Raises:
            RerankerError: 推理失败。
        """
        if not contents:
            return []

        pairs = [(query, content) for content in contents]

        try:
            raw_scores = self._model.predict(pairs)
        except Exception as exc:
            msg = f"CrossEncoder 推理失败：{exc}"
            raise RerankerError(msg) from exc

        # CrossEncoder.predict 返回 numpy.ndarray，逐元素转为 Python float
        scores = [float(s) for s in raw_scores]

        # 按分数降序排列，保留原始索引
        indexed = list(enumerate(scores))
        indexed.sort(key=lambda x: x[1], reverse=True)

        if top_k is not None and top_k > 0:
            indexed = indexed[:top_k]

        return [(idx, score) for idx, score in indexed]


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_reranker_config() -> RerankerConfig:
    """从环境变量构造 ``RerankerConfig``。

    环境变量（.env.example）：
    - ``RERANKER_MODEL``：Reranker 模型名
    """

    return RerankerConfig(
        model_name=os.environ.get("RERANKER_MODEL", DEFAULT_RERANKER_MODEL),
    )


def create_reranker(config: RerankerConfig | None = None) -> BaseReranker:
    """创建 Reranker 实例（惰性加载 CrossEncoder）。

    本函数是创建 Reranker 的唯一入口。惰性导入 ``sentence_transformers``，
    未装时抛 ``RerankerError``。

    Args:
        config: Reranker 配置，为 ``None`` 时使用默认值。

    Returns:
        ``BaseReranker`` 实例（``CrossEncoderReranker``）。

    Raises:
        RerankerError: 依赖未安装或模型加载失败。
    """
    if config is None:
        config = RerankerConfig()

    return CrossEncoderReranker(model_name=config.model_name)


def create_reranker_if_enabled() -> BaseReranker | None:
    """根据环境变量决定是否创建 Reranker（best-effort）。

    ``RERANKER_ENABLED`` 为 ``"true"`` 时尝试创建，创建失败返回 ``None``
    （不阻断应用启动）。其他值或未设置时返回 ``None``。

    Returns:
        ``BaseReranker`` 实例或 ``None``。
    """

    enabled = os.environ.get("RERANKER_ENABLED", "false").strip().lower()
    if enabled != "true":
        return None

    try:
        return create_reranker(get_reranker_config())
    except RerankerError:
        return None


# ---------------------------------------------------------------------------
# 泛型辅助函数：重排结果列表
# ---------------------------------------------------------------------------


def rerank_results(
    reranker: BaseReranker,
    query: str,
    results: Sequence[_R],
    top_k: int | None = None,
) -> list[_R]:
    """对检索结果列表重排，返回按重排分数降序的新列表（更新 score）。

    泛型函数，接受任何含 ``content`` 和 ``score`` 属性的 frozen dataclass
    （``RetrievalResult`` / ``QdrantSearchResult`` / ``ContextPiece``）。
    用 ``dataclasses.replace`` 创建新实例并更新 score，原列表不被修改。

    Args:
        reranker: ``BaseReranker`` 实例（真实或 Fake 均可）。
        query: 用户查询文本。
        results: 检索结果列表（任意含 ``content`` 和 ``score`` 的 dataclass）。
        top_k: 仅返回前 K 条。``None`` 返回全部（仅重排序不截断）。

    Returns:
        重排后的结果列表（与输入类型一致，score 已更新为重排分数）。
        空输入返回空列表。
    """
    if not results:
        return []

    contents = [r.content for r in results]
    scored = reranker.rerank(query, contents, top_k)

    # _R bound 到 _Rerankable Protocol，mypy 无法证明它是 dataclass，
    # 故 dataclasses.replace 的 _DataclassT 类型变量不匹配。
    # 实际调用方传入的 RetrievalResult/QdrantSearchResult/ContextPiece 都是
    # frozen dataclass，replace 只更新 score 字段，运行时安全。
    return [
        dataclasses.replace(results[idx], score=score)  # type: ignore[type-var]
        for idx, score in scored
    ]
