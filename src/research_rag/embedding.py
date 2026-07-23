"""Embedding 适配器与向量检索。

依据 PROJECT_PLAN.md 第 693 节（阶段 3 交付物）、第 9.2 节（Embedding 设计）、
第 13.6 节（异常清单）。

设计取舍（初学者向说明）：
- 用 LangChain 的 ``Embeddings`` 抽象 + ``InMemoryVectorStore``：
  PROJECT_PLAN 第 5.1 节明确"直接使用 LangChain 构建 RAG 流程"，不手写余弦
  相似度。``InMemoryVectorStore`` 内部用 NumPy 算余弦相似度，分数越高越相关，
  已按相关度降序返回，我们直接用。
- ``create_embeddings`` 是唯一接触真实模型的入口：按 ``EmbeddingConfig.provider``
  分发到 ``_create_local_embeddings``（本地 HuggingFace，默认）或
  ``_create_api_embeddings``（阿里百炼 OpenAI 兼容 API），未装依赖或配置缺失时抛
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

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from langchain_core.vectorstores import InMemoryVectorStore

    from research_rag.chunker import Chunk

# 默认 Embedding 模型（中文优化，生产面向中文用户）
# bge-small-zh-v1.5：BAAI 中文小模型，dense 向量 512 维，体积小、推理快，
# 中文场景下表现最佳。英文场景或中英文混合场景可通过 EMBEDDING_MODEL 环境变量
# 切换为 BAAI/bge-small-en-v1.5（英文专用）或 BAAI/bge-m3（多语言，dense
# 1024 维，体积约 2.2GB，原生支持中英文，推理慢于 bge-small）。
# 注：阶段 8.4 实测 bge-m3 在纯英文论文评测下不及 bge-small-en，故默认仍保留
# 小模型，bge-m3 作为多语言场景的可选项（详见 docs/ROADMAP.md 8.4 节）。
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
# 默认 Top-K（PROJECT_PLAN.md 第 9.2 节：top_k 过大引入噪声、过小漏召回，8 是经验值）
DEFAULT_TOP_K = 8

# ---------------------------------------------------------------------------
# Embedding Provider 配置（阶段 8.4：接入阿里百炼 API 跑中文评测）
# ---------------------------------------------------------------------------
# 默认 provider 为本地 HuggingFace 推理（生产默认路径）。设为 "dashscope" 时
# 走阿里百炼 OpenAI 兼容 API（text-embedding-v4 等），设为 "jina" 时走 Jina AI
# OpenAI 兼容 API（jina-embeddings-v3 等）。两者都适合本地 CPU 推理慢或需要
# 更大模型（如 bge-m3）但不想本地部署的场景。通过 EMBEDDING_PROVIDER 环境变量切换。
DEFAULT_EMBEDDING_PROVIDER = "local"
# 阿里百炼（DashScope）OpenAI 兼容 endpoint
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 默认 API 模型（Qwen3-Embedding 系列，原生支持中英文，1024 维，8192 tokens）
DASHSCOPE_DEFAULT_MODEL = "text-embedding-v4"
# text-embedding-v4 默认维度（支持 64/128/256/512/768/1024/1536/2048）
DASHSCOPE_DEFAULT_DIMENSIONS = 1024
# 阿里百炼 Embedding 单次请求最大行数限制（超限返回 400），必须分批
DASHSCOPE_MAX_BATCH_SIZE = 10
# text-embedding-v4 单次请求最大 token 数
DASHSCOPE_EMBEDDING_CTX_LENGTH = 8192
# Jina AI OpenAI 兼容 endpoint（jina-embeddings-v3，多语言含中文，1024 维，8192 tokens）
# 免费额度：500 RPM + 200 万 TPM，适合评测与低频调用
JINA_DEFAULT_BASE_URL = "https://api.jina.ai/v1"
JINA_DEFAULT_MODEL = "jina-embeddings-v3"
JINA_DEFAULT_DIMENSIONS = 1024
# Jina API 单次请求行数上限与百炼一致（保守取 10，避免超限）
JINA_MAX_BATCH_SIZE = 10
JINA_EMBEDDING_CTX_LENGTH = 8192


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
        model_name: 模型名。``provider="local"`` 时为 HuggingFace 模型名，默认
            ``BAAI/bge-small-zh-v1.5``（中文优化，生产面向中文用户），英文或中英文
            混合场景可通过 ``EMBEDDING_MODEL`` 环境变量切换为
            ``BAAI/bge-small-en-v1.5`` 或 ``BAAI/bge-m3``。``provider="dashscope"``
            时为阿里百炼模型名，默认 ``text-embedding-v4``。``provider="jina"`` 时
            为 Jina AI 模型名，默认 ``jina-embeddings-v3``。
        provider: Embedding 提供方，``"local"``（默认，本地 HuggingFace 推理）、
            ``"dashscope"``（阿里百炼 OpenAI 兼容 API）或 ``"jina"``（Jina AI
            OpenAI 兼容 API）。通过 ``EMBEDDING_PROVIDER`` 环境变量切换。API 模式
            适合本地 CPU 推理慢或需更大模型但不想本地部署的场景。
        api_key: API 模式所需的 API Key。为空时从 ``DASHSCOPE_API_KEY``（dashscope）
            或 ``JINA_API_KEY``（jina）环境变量读取。本地模式忽略此字段。
        base_url: API 模式的 endpoint。为空时用对应 provider 的默认 endpoint。
        dimensions: API 模式的向量维度。``0`` 表示用模型默认（1024）。本地模式
            忽略此字段（HuggingFace 模型维度固定）。
        batch_size: API 模式的单次请求行数上限。``0`` 表示用默认值（10 行/次）。
            ``OpenAIEmbeddings`` 内部按此值自动分批，无需调用方手动切分。
    """

    model_name: str = DEFAULT_EMBEDDING_MODEL
    provider: str = DEFAULT_EMBEDDING_PROVIDER
    api_key: str = ""
    base_url: str = ""
    dimensions: int = 0
    batch_size: int = 0

    def __post_init__(self) -> None:
        # API 模式（dashscope / jina）下，若 model_name 仍是本地默认值（用户未
        # 显式指定），自动切换到对应 provider 的默认模型，避免把 HuggingFace 模型名
        # 传给 API。frozen=True 需用 object.__setattr__ 绕过不可变限制。
        if self.provider == "dashscope" and self.model_name == DEFAULT_EMBEDDING_MODEL:
            object.__setattr__(self, "model_name", DASHSCOPE_DEFAULT_MODEL)
        elif self.provider == "jina" and self.model_name == DEFAULT_EMBEDDING_MODEL:
            object.__setattr__(self, "model_name", JINA_DEFAULT_MODEL)


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
    """创建 LangChain Embedding 适配器（按 ``provider`` 分支）。

    本模块唯一接触真实模型的入口。根据 ``config.provider`` 选择后端：

    - ``"local"``（默认）：惰性导入 ``HuggingFaceEmbeddings``，本地推理
    - ``"dashscope"``：惰性导入 ``OpenAIEmbeddings``，调用阿里百炼 OpenAI 兼容 API

    未装对应依赖或配置缺失时抛 ``EmbeddingServiceError``，把底层依赖错误归一化
    为业务异常，上层不用关心是 ImportError 还是 RuntimeError。

    Args:
        config: Embedding 配置，为 ``None`` 时使用默认值（本地 bge-small-zh-v1.5）。

    Returns:
        LangChain ``Embeddings`` 实例。

    Raises:
        EmbeddingServiceError: 依赖未安装、配置缺失（如 API Key 未设）或模型加载失败。
    """
    if config is None:
        config = EmbeddingConfig()

    if config.provider in ("dashscope", "jina"):
        return _create_api_embeddings(config)
    return _create_local_embeddings(config)


def _create_local_embeddings(config: EmbeddingConfig) -> Embeddings:
    """本地 HuggingFace Embedding（``provider="local"``，默认路径）。

    惰性导入 ``HuggingFaceEmbeddings``，未装 ``langchain-huggingface`` 或
    ``sentence-transformers`` 时抛 ``EmbeddingServiceError``。

    Args:
        config: Embedding 配置（用 ``model_name`` 字段，其余字段忽略）。

    Returns:
        ``HuggingFaceEmbeddings`` 实例。

    Raises:
        EmbeddingServiceError: 依赖未安装或模型加载失败。
    """
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


def _create_api_embeddings(config: EmbeddingConfig) -> Embeddings:
    """OpenAI 兼容 API Embedding（``provider="dashscope"`` 或 ``"jina"``）。

    惰性导入 ``langchain_openai.OpenAIEmbeddings``。按 ``config.provider`` 选择
    默认 endpoint / 模型 / 维度 / API Key 环境变量：

    - ``"dashscope"``（阿里百炼）：默认 ``text-embedding-v4``，读 ``DASHSCOPE_API_KEY``
    - ``"jina"``（Jina AI）：默认 ``jina-embeddings-v3``，读 ``JINA_API_KEY``

    API Key 优先取 ``config.api_key``，为空时从对应环境变量读取；两者均空时抛
    ``EmbeddingServiceError``。单次请求行数限制 10（保守值），通过 ``chunk_size``
    让 ``OpenAIEmbeddings`` 内部自动分批。``check_embedding_ctx_length=False``
    关闭 tiktoken 分词检查（tiktoken 对非 OpenAI 中文模型分词不准确，会误报超长）。

    Args:
        config: Embedding 配置（用 ``provider`` / ``model_name`` / ``api_key`` /
            ``base_url`` / ``dimensions`` / ``batch_size`` 字段，空值回退到 provider 默认）。

    Returns:
        ``OpenAIEmbeddings`` 实例（实例化不调用 API，首字节请求前不消耗 Token）。

    Raises:
        EmbeddingServiceError: 依赖未安装、API Key 缺失或实例化失败。
    """
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError as exc:
        msg = f"无法导入 langchain_openai，请确认已安装 langchain-openai。原始错误：{exc}"
        raise EmbeddingServiceError(msg) from exc

    # 按 provider 选择默认值与 API Key 环境变量名
    if config.provider == "jina":
        default_base_url = JINA_DEFAULT_BASE_URL
        default_dimensions = JINA_DEFAULT_DIMENSIONS
        default_batch = JINA_MAX_BATCH_SIZE
        ctx_length = JINA_EMBEDDING_CTX_LENGTH
        api_key_env = "JINA_API_KEY"
        provider_label = "Jina"
    else:  # dashscope
        default_base_url = DASHSCOPE_DEFAULT_BASE_URL
        default_dimensions = DASHSCOPE_DEFAULT_DIMENSIONS
        default_batch = DASHSCOPE_MAX_BATCH_SIZE
        ctx_length = DASHSCOPE_EMBEDDING_CTX_LENGTH
        api_key_env = "DASHSCOPE_API_KEY"
        provider_label = "DashScope"

    api_key = config.api_key or os.environ.get(api_key_env, "")
    if not api_key:
        msg = (
            f"{provider_label} API 模式缺少 API Key：请在 config.api_key 传入，或设置 "
            f"环境变量 {api_key_env}。注意 .env 不会被自动加载，需显式设置。"
        )
        raise EmbeddingServiceError(msg)

    base_url = config.base_url or default_base_url
    model_name = config.model_name  # __post_init__ 已保证非空（dashscope/jina 有默认）
    dimensions = config.dimensions or default_dimensions
    chunk_size = config.batch_size or default_batch

    try:
        return OpenAIEmbeddings(
            api_key=api_key,  # type: ignore[arg-type]
            base_url=base_url,
            model=model_name,
            dimensions=dimensions,
            chunk_size=chunk_size,
            embedding_ctx_length=ctx_length,
            # 关闭 tiktoken 上下文检查：tiktoken 对非 OpenAI 中文模型分词不准确，
            # 会对正常中文文本误报超长并尝试截断。API 自身会校验 token 上限。
            check_embedding_ctx_length=False,
        )
    except Exception as exc:
        msg = f"加载 {provider_label} Embedding 失败（model={model_name}）：{exc}"
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
