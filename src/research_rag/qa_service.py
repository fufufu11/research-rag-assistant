"""大模型问答与可靠引用服务。

依据 PROJECT_PLAN.md 第 701 节（阶段 4 交付物）、第 9.3 节（答案生成与引用约束）、
第 13.6 节（项目级异常清单）。

设计取舍（初学者向说明）：
- 用 LangChain 的 ``ChatOpenAI``：所有 OpenAI 兼容服务（DeepSeek、Moonshot、
  Together、OpenAI、本地 vLLM 等）都用 ``ChatOpenAI``，继承 ``BaseChatModel``，
  ``invoke`` 接口一致，后续可直接接入 LangChain 的 LCEL 链路（阶段 5+ 才会用到）。
- ``create_chat_model`` 是唯一接触具体 LLM 客户端的地方：惰性导入
  ``langchain_openai``，未装时抛 ``LlmServiceError``，把"依赖缺失"这种底层
  错误归一化成业务异常（与 ``embedding.create_embeddings`` 一致）。
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
    from langchain_core.runnables import RunnableConfig

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
        base_url: 服务 Base URL（OpenAI 兼容端点，如
            ``https://api.deepseek.com``，空字符串用 OpenAI 官方端点）。
        api_key: API 密钥（从环境变量 ``LLM_API_KEY`` 读取，禁止硬编码）。
        model: 模型名（如 ``deepseek-chat``、``gpt-4o-mini``）。
        timeout: 单次请求超时秒数。保守默认 30 秒。
        max_retries: 失败重试次数（由 httpx 实现指数退避）。
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
        start_page: chunk 内容起始页码（与 ``Chunk.start_page`` 一致）。
        end_page: chunk 内容结束页码（与 ``Chunk.end_page`` 一致）。跨页切分时
            ``end_page > start_page``，不跨页时 ``end_page == start_page``。
        chunk_index: 文档内分段序号（与 ``Chunk.chunk_index`` 一致）。
        content: 分段文本（模型可见的上下文内容）。
        score: 检索相似度分数（用于引用排序，可选）。
    """

    document_name: str
    start_page: int
    end_page: int
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
        start_page: chunk 内容起始页码。
        end_page: chunk 内容结束页码。跨页切分时 ``end_page > start_page``，
            不跨页时 ``end_page == start_page``。
        snippet: 原文片段（与 ``ContextPiece.content`` 一致）。
        score: 检索相似度分数（透传自 ``ContextPiece.score``）。
    """

    document_name: str
    start_page: int
    end_page: int
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
    """创建 LangChain ``ChatOpenAI`` 客户端（OpenAI 兼容协议）。

    本模块唯一接触具体 LLM 客户端的地方：惰性导入 ``langchain_openai``，
    未装时抛 ``LlmServiceError``，把底层依赖错误归一化为业务异常
    （与 ``embedding.create_embeddings`` 一致）。

    超时和重试参数直接传给 ``ChatOpenAI``，由底层 httpx 实现指数退避，
    本项目不自己写重试循环。

    Args:
        config: 大模型客户端配置。

    Returns:
        LangChain ``BaseChatModel`` 实例（``ChatOpenAI``）。

    Raises:
        LlmServiceError: ``langchain_openai`` 未安装或构造失败。
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        msg = f"无法导入 langchain_openai，请确认已安装 langchain-openai。原始错误：{exc}"
        raise LlmServiceError(msg) from exc

    try:
        # 用 alias 形式传参（model/api_key/base_url/timeout 是 ChatOpenAI 字段 alias）
        # api_key 字段类型是 SecretStr（pydantic），需要显式包装，否则 mypy strict 报 arg-type
        from pydantic import SecretStr

        return ChatOpenAI(
            model=config.model,
            api_key=SecretStr(config.api_key) if config.api_key else None,
            base_url=config.base_url or None,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
    except Exception as exc:
        msg = f"创建 ChatOpenAI 客户端失败：{exc}"
        raise LlmServiceError(msg) from exc


def _format_page_range(start: int, end: int) -> str:
    """格式化页码范围展示文案。

    - ``start == end``：返回 ``"第X页"``（单页）
    - ``start != end``：返回 ``"第X-Y页"``（跨页范围）

    Args:
        start: 起始页码。
        end: 结束页码。

    Returns:
        页码范围文案。
    """
    if start == end:
        return f"第{start}页"
    return f"第{start}-{end}页"


def build_prompt(question: str, contexts: Sequence[ContextPiece]) -> list[BaseMessage]:
    """构造符合第 9.3 节约束的 Prompt（SystemMessage + HumanMessage）。

    SystemMessage 编码四条硬约束：
    1. 只能使用上下文作答；
    2. 证据不足时只输出 ``[INSUFFICIENT_EVIDENCE]``；
    3. 引用证据用 ``[C1]``/``[C3]`` 等编号标记；
    4. 不得编造文档名、页码、参考文献。

    HumanMessage 包含问题 + 带编号的上下文片段。每个片段格式为：
    ``[C1] （来自《文档名》第N页）\\n<内容>``（跨页时为 ``第X-Y页``），让模型
    明确每个编号对应的来源，但页码仅用于模型理解上下文结构，真实引用由服务端
    映射（模型不得在答案中输出页码）。

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
        f"[C{i + 1}] （来自《{ctx.document_name}》"
        f"{_format_page_range(ctx.start_page, ctx.end_page)}）\n{ctx.content}"
        for i, ctx in enumerate(contexts)
    )

    user_prompt = (
        f"问题：{question}\n\n"
        f"上下文：\n{context_block}\n\n"
        "请基于上述上下文回答问题，并在答案中用 [C1]/[C2] 等编号标记引用来源。"
    )

    return [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]


# ---------------------------------------------------------------------------
# 多轮对话：历史注入、token 估算、历史截断、查询改写（阶段 9.2）
# ---------------------------------------------------------------------------


def build_prompt_with_history(
    question: str,
    contexts: Sequence[ContextPiece],
    history: Sequence[BaseMessage],
) -> list[BaseMessage]:
    """构造带历史对话的 Prompt（阶段 9.2 多轮对话）。

    在 ``build_prompt`` 的基础上，于 SystemMessage 和当前 HumanMessage 之间
    插入历史对话消息（``HumanMessage`` / ``AIMessage`` 交替），让模型理解
    "那篇""刚才"等指代。当前轮上下文仍注入最后一条 HumanMessage，引用编号
    ``[C1]`` 只指代当前轮 contexts（每轮独立编号，避免历史轮引用混入）。

    复用 ``build_prompt`` 构造 SystemMessage 和当前 HumanMessage，保持单轮与
    多轮路径的 prompt 约束完全一致。

    Args:
        question: 当前轮用户问题。
        contexts: 当前轮检索到的上下文片段列表（按相关度降序）。
        history: 历史对话消息列表（``HumanMessage`` / ``AIMessage`` 交替），
            已由调用方截断到合适长度。空列表时等价于 ``build_prompt``。

    Returns:
        LangChain 消息列表：``[SystemMessage, *history, HumanMessage]``。
    """

    base = build_prompt(question, contexts)  # [SystemMessage, HumanMessage]
    # 在 SystemMessage 和当前 HumanMessage 之间插入历史
    return [base[0], *history, base[1]]


def _message_content(message: BaseMessage) -> str:
    """提取 ``BaseMessage`` 的文本内容（用于 token 估算）。

    LangChain 消息 ``content`` 通常是 ``str``，部分模型可能返回复杂结构
    （如 tool call），此处统一转 ``str`` 做粗估，不追求精确。
    """

    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def estimate_tokens(text: str) -> int:
    """粗估文本的 token 数（阶段 9.2 历史截断用）。

    用 ``len(text) // 3`` 粗估：中文约 1 字 ≈ 1.5 token，英文约 4 字 ≈ 1 token，
    取折中系数 3。不引入 tiktoken 依赖，精度足够用于截断阈值判断。

    Args:
        text: 待估文本。

    Returns:
        估算 token 数，至少为 1（避免空消息被忽略导致截断失效）。
    """

    return max(1, len(text) // 3)


# 默认历史截断参数（阶段 9.2）：
# - 保留最近 5 轮（10 条消息），覆盖验收要求的 3 轮指代场景并留余量。
# - token 上限 4000，给当前轮上下文和答案生成留出 LLM 上下文窗口空间
#   （常见模型 8k/16k/32k 窗口，4000 历史占比合理）。
DEFAULT_MAX_HISTORY_TURNS = 5
DEFAULT_MAX_HISTORY_TOKENS = 4000


def truncate_history_messages(
    history: Sequence[BaseMessage],
    *,
    max_turns: int = DEFAULT_MAX_HISTORY_TURNS,
    max_tokens: int = DEFAULT_MAX_HISTORY_TOKENS,
) -> list[BaseMessage]:
    """截断历史消息（阶段 9.2 轮数 + token 双重保护）。

    先按轮数粗筛（保留最近 ``max_turns`` 轮，一轮 = user + assistant = 2 条），
    再按 token 数精裁（从最老消息开始裁，直到总 token 数 ≤ ``max_tokens``）。
    双重保护避免：① 历史无限增长；② 单条超长消息撑爆 LLM 上下文窗口。

    Args:
        history: 历史消息列表（``HumanMessage`` / ``AIMessage`` 交替，按时间升序）。
        max_turns: 保留的最大轮数（一轮 = 2 条消息）。
        max_tokens: 历史消息总 token 数上限。

    Returns:
        截断后的历史消息列表（按时间升序），可能为空。
    """

    # 1. 按轮数粗筛：保留最近 max_turns * 2 条
    max_messages = max_turns * 2
    truncated = list(history[-max_messages:]) if max_messages > 0 else []

    # 2. 按 token 数精裁：从最老开始裁，直到总 token 数 ≤ max_tokens
    total_tokens = sum(estimate_tokens(_message_content(m)) for m in truncated)
    while total_tokens > max_tokens and truncated:
        oldest = truncated.pop(0)
        total_tokens -= estimate_tokens(_message_content(oldest))

    return truncated


def rewrite_query(
    question: str,
    history: Sequence[BaseMessage],
    chat_model: BaseChatModel,
    run_config: RunnableConfig | None = None,
) -> str:
    """用历史对话改写当前问题（阶段 9.2 查询改写）。

    把"那篇""刚才"等指代解析为独立问题，提升多轮检索质量。用 LLM 根据历史
    对话和当前问题生成一个独立的、可脱离上下文理解的问题，再用改写后的问题
    做检索（历史参与检索）。

    设计取舍：
    - 改写失败时回退到原 ``question``，不阻塞问答流程（降级为不改写检索）。
    - 用同一个 ``chat_model``（不引入第二个 LLM 客户端配置，降低复杂度）。
    - 无历史时直接返回原问题（首轮无需改写）。

    Args:
        question: 当前轮用户问题（可能含指代）。
        history: 历史对话消息列表（已截断）。
        chat_model: LangChain ``BaseChatModel`` 实例。
        run_config: LangChain ``RunnableConfig``（可选，阶段 10.1 引入）。
            透传给 ``chat_model.invoke``，用于注入 Langfuse callback handler。
            ``None`` 时行为不变（向后兼容）。

    Returns:
        改写后的独立问题。改写失败或无历史时返回原 ``question``。
    """

    if not history:
        # 无历史无需改写（首轮问题已是独立问题）
        return question

    from langchain_core.messages import HumanMessage, SystemMessage

    system_prompt = (
        "你是一个查询改写助手。根据历史对话，把用户当前问题改写为一个独立的、"
        "可脱离上下文理解的完整问题。要求：\n"
        '1. 解析"那篇""刚才"等指代为具体实体（如论文标题、方法名）。\n'
        "2. 若当前问题已是独立问题，原样输出。\n"
        "3. 只输出改写后的问题，不要任何解释或前缀。"
    )

    # 构造历史对话摘要（用于改写参考）
    history_text = "\n".join(
        f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {_message_content(m)}"
        for m in history
    )

    user_prompt = f"历史对话：\n{history_text}\n\n当前问题：{question}\n\n改写后的问题："

    try:
        response = chat_model.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
            config=run_config,
        )
    except Exception:
        # 改写失败回退到原问题，不阻塞问答（降级为不改写检索）
        return question

    content = getattr(response, "content", None)
    if not isinstance(content, str) or not content.strip():
        return question

    return content.strip()


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
                start_page=ctx.start_page,
                end_page=ctx.end_page,
                snippet=ctx.content,
                score=ctx.score,
            )
        )
    return citations


def _invoke_and_parse(
    chat_model: BaseChatModel,
    messages: Sequence[BaseMessage],
    contexts: Sequence[ContextPiece],
    run_config: RunnableConfig | None = None,
) -> AnswerWithCitations:
    """调用 LLM 并解析答案（``answer_question`` 与多轮路径共享的内部逻辑）。

    执行：``invoke`` → 类型守卫 → 证据不足检测 → 引用编号解析 → 引用映射。
    抽取此函数让单轮（``answer_question``）和多轮（``answer_with_messages``）
    路径共享同一套检测与解析逻辑，避免行为分叉。

    Args:
        chat_model: LangChain ``BaseChatModel`` 实例。
        messages: 已构造的 LangChain 消息列表。
        contexts: 检索到的上下文片段列表（用于引用映射，非空）。
        run_config: LangChain ``RunnableConfig``（可选，阶段 10.1 引入）。
            透传给 ``chat_model.invoke``，用于注入 Langfuse callback handler
            等。``None`` 时行为不变（向后兼容）。

    Returns:
        结构化答案（答案文本 + 引用编号 + 真实引用列表）。

    Raises:
        LlmServiceError: 模型调用失败或返回无法解析。
        InsufficientEvidenceError: 模型判定证据不足（输出 ``[INSUFFICIENT_EVIDENCE]``）。
    """

    try:
        response = chat_model.invoke(messages, config=run_config)
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


def answer_question(
    question: str,
    contexts: Sequence[ContextPiece],
    chat_model: BaseChatModel,
    run_config: RunnableConfig | None = None,
) -> AnswerWithCitations:
    """完整问答流程：构造 Prompt → 调用 LLM → 解析引用 → 映射真实引用。

    Args:
        question: 用户问题。
        contexts: 检索到的上下文片段列表（按相关度降序，非空）。
        chat_model: LangChain ``BaseChatModel`` 实例（如 ``ChatOpenAI``）。
            测试时可传入 ``FakeListChatModel`` 等假模型 Mock。
        run_config: LangChain ``RunnableConfig``（可选，阶段 10.1 引入）。
            透传给 ``_invoke_and_parse``，用于注入 Langfuse callback handler。
            ``None`` 时行为不变（向后兼容）。

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
    return _invoke_and_parse(chat_model, messages, contexts, run_config=run_config)


def answer_with_messages(
    messages: Sequence[BaseMessage],
    contexts: Sequence[ContextPiece],
    chat_model: BaseChatModel,
    run_config: RunnableConfig | None = None,
) -> AnswerWithCitations:
    """用预先构造的 messages 调用 LLM（阶段 9.2 多轮对话路径）。

    与 ``answer_question`` 的区别：调用方预先用
    ``build_prompt_with_history`` 构造带历史对话的 messages，本函数只负责
    调用 LLM 和解析引用。共享 ``_invoke_and_parse`` 保证检测与解析逻辑一致。

    Args:
        messages: 已构造的 LangChain 消息列表（含 SystemMessage + 历史 + 当前
            HumanMessage）。
        contexts: 当前轮检索到的上下文片段列表（用于引用映射，非空）。
        chat_model: LangChain ``BaseChatModel`` 实例。
        run_config: LangChain ``RunnableConfig``（可选，阶段 10.1 引入）。
            透传给 ``_invoke_and_parse``，用于注入 Langfuse callback handler。
            ``None`` 时行为不变（向后兼容）。

    Returns:
        结构化答案（答案文本 + 引用编号 + 真实引用列表）。

    Raises:
        LlmServiceError: 上下文为空、模型调用失败或返回无法解析。
        InsufficientEvidenceError: 模型判定证据不足（输出 ``[INSUFFICIENT_EVIDENCE]``）。
    """
    if not contexts:
        msg = "上下文为空，无法构造问答 Prompt。"
        raise LlmServiceError(msg)

    return _invoke_and_parse(chat_model, messages, contexts, run_config=run_config)


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
            start_page=r.start_page,
            end_page=r.end_page,
            chunk_index=r.chunk_index,
            content=r.content,
            score=r.score,
        )
        for r in results
    ]
