"""大模型问答与可靠引用服务。

依据 PROJECT_PLAN.md 第 701 节（阶段 4 交付物）、第 9.3 节（答案生成与引用约束）、
第 13.6 节（项目级异常清单）。

设计取舍（初学者向说明）：
- 用 LangChain 的 ``ChatOpenAI``：所有 OpenAI 兼容服务（DeepSeek、Moonshot、
  Together、本地 vLLM 等）都用同一个客户端，且与已装的 ``langchain-huggingface``
  风格一致。``ChatOpenAI`` 继承 ``BaseChatModel``，后续可直接接入 LangChain 的
  LCEL 链路（阶段 5+ 才会用到）。
- ``create_chat_model`` 是唯一接触 ``ChatOpenAI`` 的地方：
  惰性导入 ``langchain_openai``，未装时抛 ``LlmServiceError``，把"依赖缺失"
  这种底层错误归一化成业务异常（与 ``embedding.create_embeddings`` 一致）。
- 引用编号策略：模型在答案文本中用 ``[C1]``/``[C3]`` 标记引用，服务端用正则
  提取这些标记并映射到真实引用。这种"自然语言+引用标记"方式不需要模型支持
  function calling 或 JSON mode，兼容性最好；服务端映射避免了模型编造页码
  （第 9.3 节核心目标）。
- 证据不足检测：要求模型在证据不足时只输出 ``[INSUFFICIENT_EVIDENCE]``，
  服务端检测到该标记时抛 ``InsufficientEvidenceError``。API 层（阶段 5）
  捕获后可转为 422 或明确的拒绝答案（第 13.6 节"API 层负责将异常转换为
  稳定错误码"）。
- 超时和有限重试：直接传给 ``ChatOpenAI``，由底层 httpx 实现，本项目不自己
  写重试循环。默认保守值（timeout=30s、max_retries=2），可配置。
- ``ContextPiece`` 与 ``RetrievalResult`` 解耦：``RetrievalResult`` 没有
  ``document_name`` 字段（文档管理在阶段 5 才有），所以本模块定义独立的
  ``ContextPiece``，由调用方拼接（如 ``retrieval_to_context`` 辅助函数）。
- 数据结构用 ``dataclass(frozen=True)``：与 ``Chunk`` / ``RetrievalResult``
  一致，不可变，避免下游意外修改溯源信息。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import BaseMessage

    from research_rag.embedding import RetrievalResult

# ---------------------------------------------------------------------------
# 默认参数
# ---------------------------------------------------------------------------
# 默认超时 30 秒：保守值，避免本机或 CI 因网络问题长时间挂起。
# 实际生产可根据模型服务响应时间调整。
DEFAULT_LLM_TIMEOUT = 30.0
# 默认重试 2 次：保守值，避免在模型服务过载时雪崩。httpx 默认指数退避。
DEFAULT_LLM_MAX_RETRIES = 2

# 证据不足标记：要求模型在证据不足时仅输出此字符串。
# 用方括号包裹与 [C1] 风格一致，避免与正常答案混淆。
INSUFFICIENT_EVIDENCE_MARKER = "[INSUFFICIENT_EVIDENCE]"

# 引用编号正则：匹配 [C1]、[C12] 等。忽略大小写，避免模型输出 [c1] 时漏判。
# 形如 [C1] 的标记在自然语言中极少误匹配，且要求模型遵循 Prompt 约束。
_CITATION_PATTERN = re.compile(r"\[c(\d+)\]", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 异常（PROJECT_PLAN.md 第 13.6 节）
# ---------------------------------------------------------------------------


class LlmServiceError(RuntimeError):
    """LLM 服务异常。

    当模型依赖未安装、调用超时、重试耗尽或返回无法解析时抛出。
    对应 PROJECT_PLAN.md 第 13.6 节异常清单。
    """


class InsufficientEvidenceError(RuntimeError):
    """证据不足异常。

    当模型判断上下文不足以回答问题（输出 ``[INSUFFICIENT_EVIDENCE]`` 标记）时抛出。
    对应 PROJECT_PLAN.md 第 13.6 节异常清单与第 9.3 节"证据不足时说明"约束。
    """


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LlmConfig:
    """大模型客户端配置。

    Attributes:
        base_url: OpenAI 兼容服务的 Base URL（如 ``https://api.deepseek.com``）。
            为空字符串时使用 OpenAI 官方端点。
        api_key: API 密钥。从环境变量 ``LLM_API_KEY`` 读取，禁止硬编码到源码。
        model: 模型名（如 ``deepseek-chat``、``gpt-4o-mini``）。
        timeout: 单次请求超时秒数。保守默认 30 秒。
        max_retries: 失败重试次数。保守默认 2 次，由 httpx 实现指数退避。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = DEFAULT_LLM_TIMEOUT
    max_retries: int = DEFAULT_LLM_MAX_RETRIES


@dataclass(frozen=True)
class ContextPiece:
    """带编号的上下文片段（用于构造 Prompt 和引用映射）。

    与 ``embedding.RetrievalResult`` 解耦：``RetrievalResult`` 没有
    ``document_name`` 字段（文档管理在阶段 5 才有），所以本模块定义独立的
    ``ContextPiece``，由调用方拼接（可参考模块级辅助函数
    ``retrieval_to_context``）。

    Attributes:
        document_name: 来源文档名（用于真实引用映射）。
        page_number: 来源页码（与 ``Chunk.page_number`` 一致）。
        chunk_index: 文档内分段序号（与 ``Chunk.chunk_index`` 一致）。
        content: 分段文本（模型可见的上下文内容）。
        score: 检索相似度分数（用于引用排序，可选）。
    """

    document_name: str
    page_number: int
    chunk_index: int
    content: str
    score: float = 0.0


@dataclass(frozen=True)
class Citation:
    """服务端映射后的真实引用（不可变）。

    模型只返回上下文编号 ``[C1]``，服务端根据编号映射到包含文档名和页码
    的真实引用，避免模型编造页码（第 9.3 节核心目标）。

    Attributes:
        document_name: 来源文档名。
        page_number: 来源页码。
        snippet: 原文片段（与 ``ContextPiece.content`` 一致）。
        score: 检索相似度分数（透传自 ``ContextPiece.score``）。
    """

    document_name: str
    page_number: int
    snippet: str
    score: float


@dataclass(frozen=True)
class AnswerWithCitations:
    """结构化答案（不可变）。

    Attributes:
        answer_text: 模型生成的答案文本（含 ``[C1]``/``[C3]`` 等引用标记）。
        citation_indices: 模型引用的上下文编号列表（去重保序，如 ``[1, 3]``）。
        citations: 服务端映射后的真实引用列表（与 ``citation_indices`` 一一对应）。
    """

    answer_text: str
    citation_indices: list[int]
    citations: list[Citation]


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------


def create_chat_model(config: LlmConfig) -> BaseChatModel:
    """创建 OpenAI 兼容的 LangChain ChatModel 客户端。

    这是本模块唯一接触 ``ChatOpenAI`` 的地方。惰性导入 ``langchain_openai``：
    未装时抛 ``LlmServiceError``，把底层依赖错误归一化为业务异常（与
    ``embedding.create_embeddings`` 一致）。

    超时和重试参数直接传给 ``ChatOpenAI``，由底层 httpx 实现指数退避，
    本项目不自己写重试循环。

    Args:
        config: 大模型客户端配置。

    Returns:
        LangChain ``ChatOpenAI`` 实例（继承 ``BaseChatModel``）。

    Raises:
        LlmServiceError: ``langchain-openai`` 未安装或构造失败。
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        msg = f"无法导入 langchain_openai，请确认已安装 langchain-openai。原始错误：{exc}"
        raise LlmServiceError(msg) from exc

    try:
        # 用 alias 形式传参（model/api_key/base_url/timeout 是 ChatOpenAI 字段 alias）
        return ChatOpenAI(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url or None,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
    except Exception as exc:
        msg = f"创建 ChatOpenAI 客户端失败：{exc}"
        raise LlmServiceError(msg) from exc


def build_prompt(question: str, contexts: Sequence[ContextPiece]) -> list[BaseMessage]:
    """构造符合第 9.3 节约束的 Prompt（SystemMessage + HumanMessage）。

    SystemMessage 编码四条硬约束：
    1. 只能使用上下文作答；
    2. 证据不足时只输出 ``[INSUFFICIENT_EVIDENCE]``；
    3. 引用证据用 ``[C1]``/``[C3]`` 等编号标记；
    4. 不得编造文档名、页码、参考文献。

    HumanMessage 包含问题 + 带编号的上下文片段。每个片段格式为：
    ``[C1] （来自《文档名》第N页）\\n<内容>``，让模型明确每个编号对应的来源，
    但页码仅用于模型理解上下文结构，真实引用由服务端映射（模型不得在答案中
    输出页码）。

    Args:
        question: 用户问题。
        contexts: 检索到的上下文片段列表（按相关度降序）。

    Returns:
        LangChain 消息列表，可直接传给 ``chat_model.invoke``。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    system_prompt = (
        "你是一个严谨的科研文献问答助手。请严格遵循以下规则：\n"
        "1. 只能使用下方「上下文」中的内容回答问题，不得引入任何外部知识。\n"
        "2. 如果上下文中没有足够证据回答问题，必须仅输出"
        f"{INSUFFICIENT_EVIDENCE_MARKER}"
        "，不得猜测或编造。\n"
        "3. 引用证据时使用上下文编号标记，例如 [C1]、[C3]。可同时引用多个编号。\n"
        "4. 不得自行编造文档名、页码、作者或参考文献。"
    )

    context_block = "\n\n".join(
        f"[C{i + 1}] （来自《{ctx.document_name}》第{ctx.page_number}页）\n{ctx.content}"
        for i, ctx in enumerate(contexts)
    )

    user_prompt = (
        f"问题：{question}\n\n"
        f"上下文：\n{context_block}\n\n"
        "请基于上述上下文回答问题，并在答案中用 [C1]/[C2] 等编号标记引用来源。"
    )

    return [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]


def parse_citation_indices(text: str) -> list[int]:
    """从答案文本中提取引用编号（去重保序）。

    匹配 ``[C1]``、``[C3]``、``[c12]`` 等标记，返回编号列表。重复编号只保留
    第一次出现的位置（如 ``[C1]...[C1]`` 返回 ``[1]``），保持模型引用顺序。

    Args:
        text: 模型生成的答案文本。

    Returns:
        引用编号列表（如 ``[1, 3]``）。无引用时返回空列表。

    Examples:
        >>> parse_citation_indices("根据 [C1] 和 [C3] 可知...")
        [1, 3]
        >>> parse_citation_indices("没有引用")
        []
        >>> parse_citation_indices("[C1]...[C1]")
        [1]
    """
    seen: set[int] = set()
    indices: list[int] = []
    for match in _CITATION_PATTERN.finditer(text):
        idx = int(match.group(1))
        if idx not in seen:
            seen.add(idx)
            indices.append(idx)
    return indices


def map_citations(
    indices: Sequence[int],
    contexts: Sequence[ContextPiece],
) -> list[Citation]:
    """根据引用编号映射到真实引用（文档名、页码、原文片段）。

    编号从 1 开始（与 Prompt 中 ``[C1]`` 一致）。无效编号（超出上下文范围）
    静默跳过，不抛异常：模型偶尔会输出越界编号，跳过比让整个请求失败更鲁棒。
    重复编号只映射一次（与 ``parse_citation_indices`` 去重策略一致）。

    Args:
        indices: 引用编号列表（``parse_citation_indices`` 的返回值）。
        contexts: 构造 Prompt 时使用的上下文片段列表（顺序与编号对应）。

    Returns:
        真实引用列表（与 ``indices`` 一一对应，越界编号已跳过）。
    """
    citations: list[Citation] = []
    seen: set[int] = set()
    for idx in indices:
        if idx <= 0 or idx > len(contexts):
            continue
        if idx in seen:
            continue
        seen.add(idx)
        ctx = contexts[idx - 1]
        citations.append(
            Citation(
                document_name=ctx.document_name,
                page_number=ctx.page_number,
                snippet=ctx.content,
                score=ctx.score,
            )
        )
    return citations


def answer_question(
    question: str,
    contexts: Sequence[ContextPiece],
    chat_model: BaseChatModel,
) -> AnswerWithCitations:
    """完整问答流程：构造 Prompt → 调用 LLM → 解析引用 → 映射真实引用。

    Args:
        question: 用户问题。
        contexts: 检索到的上下文片段列表（按相关度降序，非空）。
        chat_model: LangChain ``BaseChatModel`` 实例（如 ``ChatOpenAI``）。
            测试时可传入 ``FakeListChatModel`` 等假模型 Mock。

    Returns:
        结构化答案（答案文本 + 引用编号 + 真实引用列表）。

    Raises:
        LlmServiceError: 上下文为空、模型调用失败或返回无法解析。
        InsufficientEvidenceError: 模型判定证据不足（输出 ``[INSUFFICIENT_EVIDENCE]``）。
    """
    if not contexts:
        msg = "上下文为空，无法构造问答 Prompt。"
        raise LlmServiceError(msg)

    messages = build_prompt(question, contexts)

    try:
        response = chat_model.invoke(messages)
    except InsufficientEvidenceError:
        raise
    except Exception as exc:
        msg = f"调用大模型失败：{exc}"
        raise LlmServiceError(msg) from exc

    # BaseChatModel.invoke 返回 AIMessage，content 为字符串。
    # 不同 LangChain 版本的类型标注略有差异，用 isinstance 做运行时保护。
    content = getattr(response, "content", None)
    if not isinstance(content, str):
        msg = f"大模型返回的内容不是字符串：{type(response).__name__}"
        raise LlmServiceError(msg)

    # 证据不足检测（在解析引用之前，避免误把 [INSUFFICIENT_EVIDENCE] 当成引用）
    if INSUFFICIENT_EVIDENCE_MARKER in content:
        msg = "上下文证据不足以回答该问题。"
        raise InsufficientEvidenceError(msg)

    citation_indices = parse_citation_indices(content)
    citations = map_citations(citation_indices, contexts)

    return AnswerWithCitations(
        answer_text=content,
        citation_indices=citation_indices,
        citations=citations,
    )


# ---------------------------------------------------------------------------
# 辅助函数：从 RetrievalResult 拼接 ContextPiece
# ---------------------------------------------------------------------------


def retrieval_to_context(
    results: Sequence[RetrievalResult],
    document_name: str,
) -> list[ContextPiece]:
    """把 ``embedding.RetrievalResult`` 列表转换为 ``ContextPiece`` 列表。

    阶段 3 的 ``RetrievalResult`` 没有 ``document_name`` 字段（文档管理在
    阶段 5 才有），需要调用方提供文档名。多文档场景下应分别调用本函数再合并。

    Args:
        results: ``embedding.retrieve`` 的返回值。
        document_name: 这些检索结果所属的文档名。

    Returns:
        ``ContextPiece`` 列表，顺序与 ``results`` 一致。
    """
    return [
        ContextPiece(
            document_name=document_name,
            page_number=r.page_number,
            chunk_index=r.chunk_index,
            content=r.content,
            score=r.score,
        )
        for r in results
    ]
