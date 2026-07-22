"""Embedding 适配器与向量检索。

依据 PROJECT_PLAN.md 第 693 节（阶段 3 交付物）、第 9.2 节（Embedding 设计）、
第 13.6 节（异常清单）。

设计取舍（初学者向说明）：
- 用 LangChain 的 ``Embeddings`` 抽象 + ``InMemoryVectorStore``：
  PROJECT_PLAN 第 5.1 节明确"直接使用 LangChain 构建 RAG 流程"，不手写余弦
  相似度。``InMemoryVectorStore`` 内部用 NumPy 算余弦相似度，分数越高越相关，
  已按相关度降序返回，我们直接用。
- ``create_embeddings`` 是唯一接触真实模型的地方：
  惰性导入 ``HuggingFaceEmbeddings``，未装 ``sentence-transformers`` 时抛
  ``EmbeddingServiceError``，把"依赖缺失"这种底层错误归一化成业务异常，
  上层不用关心是 ImportError 还是 RuntimeError。
- ``sentence-transformers`` 放在可选 extra ``embedding``：
  本地推理要拉 torch（约数百 MB），CI 只跑 ``uv sync --extra dev`` 用
  FakeEmbeddings 测试，不必装 torch，保持 CI 轻量。
- ``index_chunks`` / ``retrieve`` 采用依赖注入（参数接受 ``Embeddings``）：
  测试时注入确定性 ``FakeEmbeddings``（按字符哈希生成稳定向量），既不依赖
  真实模型，又能验证"索引→检索→排序"的完整流程。
- ``RetrievalResult`` 用 ``dataclass(frozen=True)``：与 ``Chunk`` / ``PageInfo``
  一致，不可变，避免下游意外修改溯源信息。
- 元数据只保留 ``start_page`` / ``end_page`` 和 ``chunk_index``：这是溯源
  所需的最小信息，内容通过 ``content`` 字段返回，不重复存到 metadata。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from langchain_core.vectorstores import InMemoryVectorStore

    from research_rag.chunker import Chunk

# 默认 Embedding 模型（PROJECT_PLAN.md 第 9.2 节、.env.example）
# bge-small-zh-v1.5：中文优化、维度 512、体积小（约 100MB），适合学习与原型
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
# 默认 Top-K（PROJECT_PLAN.md 第 9.2 节：top_k 过大引入噪声、过小漏召回，8 是经验值）
DEFAULT_TOP_K = 8


class EmbeddingServiceError(RuntimeError):
    """Embedding 服务异常。

    当模型加载失败、依赖未安装或向量化失败时抛出。
    对应 PROJECT_PLAN.md 第 13.6 节异常清单。
    """


class VectorStoreError(RuntimeError):
    """向量存储异常。

    当索引或检索失败时抛出。对应 PROJECT_PLAN.md 第 13.6 节异常清单。
    """


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding 配置。

    Attributes:
        model_name: HuggingFace 模型名，默认 ``BAAI/bge-small-zh-v1.5``。
    """

    model_name: str = DEFAULT_EMBEDDING_MODEL


@dataclass(frozen=True)
class RetrievalResult:
    """单条检索结果（不可变）。

    Attributes:
        start_page: chunk 内容起始页码（与 ``Chunk.start_page`` 一致），用于溯源。
        end_page: chunk 内容结束页码（与 ``Chunk.end_page`` 一致）。跨页切分时
            ``end_page > start_page``，不跨页时 ``end_page == start_page``。
        chunk_index: 文档内分段序号（与 ``Chunk.chunk_index`` 一致），用于溯源。
        content: 分段文本。
        score: 余弦相似度分数，越高越相关（``InMemoryVectorStore`` 语义）。
    """

    start_page: int
    end_page: int
    chunk_index: int
    content: str
    score: float


def create_embeddings(config: EmbeddingConfig | None = None) -> Embeddings:
    """创建 LangChain Embedding 适配器（本地 HuggingFace 模型）。

    这是本模块唯一接触真实模型的地方。惰性导入 ``HuggingFaceEmbeddings``：
    未装 ``langchain-huggingface`` 或 ``sentence-transformers`` 时抛
    ``EmbeddingServiceError``，把底层依赖错误归一化为业务异常。

    Args:
        config: Embedding 配置，为 ``None`` 时使用默认值。

    Returns:
        LangChain ``Embeddings`` 实例。

    Raises:
        EmbeddingServiceError: 依赖未安装或模型加载失败。
    """
    if config is None:
        config = EmbeddingConfig()

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError as exc:
        msg = f"无法导入 langchain_huggingface，请确认已安装 langchain-huggingface。原始错误：{exc}"
        raise EmbeddingServiceError(msg) from exc

    try:
        return HuggingFaceEmbeddings(model_name=config.model_name)
    except ImportError as exc:
        # HuggingFaceEmbeddings.__init__ 惰性导入 sentence_transformers
        msg = (
            "加载 HuggingFace Embedding 失败：缺少 sentence-transformers。"
            "请运行 `uv sync --extra embedding` 安装推理后端。"
            f"原始错误：{exc}"
        )
        raise EmbeddingServiceError(msg) from exc
    except Exception as exc:
        msg = f"加载 HuggingFace Embedding 失败：{exc}"
        raise EmbeddingServiceError(msg) from exc


def index_chunks(
    chunks: Sequence[Chunk],
    embeddings: Embeddings,
) -> InMemoryVectorStore:
    """把 Chunk 列表索引到内存向量存储。

    每个 Chunk 转换为 LangChain ``Document``，``start_page`` / ``end_page`` 和
    ``chunk_index`` 存入 ``metadata`` 用于溯源。``chunk_index`` 在文档内
    唯一，可作为引用编号的基础。

    Args:
        chunks: 切分后的 Chunk 列表（``chunk_pages`` 的返回值）。
        embeddings: LangChain ``Embeddings`` 实例（真实或 Fake 均可）。

    Returns:
        已索引的 ``InMemoryVectorStore``，可直接传给 ``retrieve``。

    Raises:
        VectorStoreError: 索引失败（如向量化异常）。
    """
    from langchain_core.documents import Document
    from langchain_core.vectorstores import InMemoryVectorStore

    documents: list[Document] = [
        Document(
            page_content=chunk.content,
            metadata={
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "chunk_index": chunk.chunk_index,
            },
        )
        for chunk in chunks
    ]

    store = InMemoryVectorStore(embedding=embeddings)
    if not documents:
        # 空列表直接返回空 store，避免底层对空输入的行为差异
        return store

    try:
        store.add_documents(documents)
    except Exception as exc:
        msg = f"索引 Chunk 到向量存储失败：{exc}"
        raise VectorStoreError(msg) from exc

    return store


def retrieve(
    store: InMemoryVectorStore,
    query: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievalResult]:
    """从向量存储中检索 Top-K 相关片段。

    Args:
        store: 已索引的 ``InMemoryVectorStore``（``index_chunks`` 的返回值）。
        query: 查询文本。
        top_k: 返回的最相关片段数，默认 8。

    Returns:
        检索结果列表，按余弦相似度降序（分数越高越相关）。
        若 store 为空则返回空列表。

    Raises:
        VectorStoreError: ``top_k`` 非正或检索失败。
    """
    if top_k <= 0:
        msg = f"top_k 必须为正整数，收到 {top_k}"
        raise VectorStoreError(msg)

    try:
        results: list[tuple[Document, float]] = store.similarity_search_with_score(query, k=top_k)
    except Exception as exc:
        msg = f"向量检索失败：{exc}"
        raise VectorStoreError(msg) from exc

    return [
        RetrievalResult(
            start_page=doc.metadata["start_page"],
            end_page=doc.metadata["end_page"],
            chunk_index=doc.metadata["chunk_index"],
            content=doc.page_content,
            score=score,
        )
        for doc, score in results
    ]
