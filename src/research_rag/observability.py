"""Langfuse 可观测性集成（阶段 10.1）。

依据 docs/ROADMAP.md 第 99-112 行（阶段 10.1 可观测性定义）、
docs/architecture.md 第 6 节（环境变量与配置体系）。

设计取舍（初学者向说明）：
- **环境变量开关默认关闭**：与 ``RERANKER_ENABLED`` / ``QDRANT_ENABLED`` /
  ``BM25_ENABLED`` 风格一致。本地开发、CI、测试环境无需起 Langfuse 服务即可
  运行；生产环境显式设置 ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` /
  ``LANGFUSE_HOST`` 三项后启用。
- **用 Langfuse 2.x 装饰器模式**（``@observe`` + ``langfuse_context``）：
  装饰的函数自动成为 trace（最外层）或 span（嵌套），LangChain 调用通过
  ``langfuse_context.get_current_langchain_handler()`` 获取的 callback 自动
  挂到当前 trace。未配置环境变量时 Langfuse SDK 自动 no-op，不破坏现有行为。
- **不修改 ``LlmConfig`` 与 ``create_chat_model``**：Langfuse 通过 contextvar
  自动跟踪 LangChain 调用，不需要把 callback 注入到 ``ChatOpenAI`` 构造函数，
  保持 ``LlmConfig`` 配置职责单一。
- **底层 ``answer_question`` / ``rewrite_query`` 加可选 ``run_config`` 参数**：
  透传 LangChain ``RunnableConfig``（含 ``callbacks``），让 LLM 调用挂到当前
  Langfuse trace。``run_config=None`` 时行为不变（向后兼容）。
- **trace 数据由 Langfuse 服务自管**（PostgreSQL），不污染项目 DB schema，
  无需新增 Alembic 迁移。
- **测试策略**：``is_langfuse_enabled`` 直接读环境变量，CI 未配置时返回
  ``False``，``observe`` 装饰器变成透传，``get_current_langchain_handler``
  返回 ``None``。单元测试覆盖配置解析、no-op 行为与装饰器透传。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any, TypeVar

    from langchain_core.callbacks import BaseCallbackHandler

    F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# 配置（环境变量驱动，与 RERANKER_ENABLED / QDRANT_ENABLED 风格一致）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LangfuseConfig:
    """Langfuse 客户端配置（不可变）。

    Attributes:
        public_key: Langfuse 项目公钥（``pk-lf-...``）。从环境变量
            ``LANGFUSE_PUBLIC_KEY`` 读取。
        secret_key: Langfuse 项目私钥（``sk-lf-...``）。从环境变量
            ``LANGFUSE_SECRET_KEY`` 读取。**绝不硬编码**。
        host: Langfuse 服务地址（如 ``https://langfuse.example.com``）。
            自部署时为本机或内网地址；从环境变量 ``LANGFUSE_HOST`` 读取。
    """

    public_key: str
    secret_key: str
    host: str


def load_langfuse_config_from_env() -> LangfuseConfig | None:
    """从环境变量构造 ``LangfuseConfig``，未配置完整时返回 ``None``。

    三项环境变量（``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` /
    ``LANGFUSE_HOST``）必须全部非空才返回配置，任一缺失返回 ``None``
    （表示未启用 Langfuse，调用方走 no-op 路径）。

    Returns:
        ``LangfuseConfig`` 实例或 ``None``（未启用）。
    """

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    host = os.environ.get("LANGFUSE_HOST", "").strip()
    if not (public_key and secret_key and host):
        return None
    return LangfuseConfig(public_key=public_key, secret_key=secret_key, host=host)


def is_langfuse_enabled() -> bool:
    """检查 Langfuse 是否启用（三项环境变量都非空）。

    与 ``load_langfuse_config_from_env`` 同语义，但避免每次都构造 dataclass，
    用于 ``observe`` 装饰器与 ``get_current_langchain_handler`` 的快速判断。

    Returns:
        ``True`` 表示已启用；``False`` 表示未配置（no-op）。
    """

    return load_langfuse_config_from_env() is not None


# ---------------------------------------------------------------------------
# observe 装饰器：未启用时 no-op（透传），启用时用 langfuse.decorators.observe
# ---------------------------------------------------------------------------


def observe(name: str) -> Callable[[F], F]:
    """观察装饰器工厂：把函数标记为 Langfuse trace / span。

    启用 Langfuse 时（环境变量配置完整）：委托给 ``langfuse.decorators.observe``，
    装饰的函数自动成为 trace（最外层调用）或 span（嵌套调用），LangChain 调用
    通过 ``langfuse_context.get_current_langchain_handler()`` 自动挂到当前 trace。

    未启用 Langfuse 时：返回透传装饰器（不修改函数行为），保证现有代码与测试
    不受影响。

    装饰器同时支持同步与异步函数（``langfuse.decorators.observe`` 内部处理，
    no-op 路径用 ``functools.wraps`` 保留原函数签名）。

    Args:
        name: trace / span 名称（在 Langfuse dashboard 展示）。

    Returns:
        装饰器函数。
    """

    if not is_langfuse_enabled():
        # no-op 路径：直接返回原函数，不引入 langfuse 依赖
        def decorator(func: F) -> F:
            return func

        return decorator

    # 启用路径：委托给 langfuse.decorators.observe
    # 局部导入避免模块加载时硬依赖 langfuse（未启用时也不导入）
    from langfuse.decorators import observe as _lf_observe

    return _lf_observe(name)


# ---------------------------------------------------------------------------
# LangChain callback 获取：在 @observe 装饰的函数内调用，挂到当前 trace
# ---------------------------------------------------------------------------


def get_current_langchain_handler() -> BaseCallbackHandler | None:
    """获取当前 Langfuse trace 的 LangChain callback handler。

    在 ``@observe`` 装饰的函数内调用，返回绑定到当前 trace 的 callback handler，
    传给 ``chat_model.invoke(messages, config={"callbacks": [handler]})`` 即可让
    LLM 调用自动上报到 Langfuse（输入、输出、token、耗时）。

    未启用 Langfuse 或不在 ``@observe`` 装饰的函数内调用时返回 ``None``，调用方
    应跳过 callback 透传（行为不变）。

    Returns:
        ``BaseCallbackHandler`` 实例或 ``None``。
    """

    if not is_langfuse_enabled():
        return None

    from langfuse.decorators import langfuse_context

    return langfuse_context.get_current_langchain_handler()


def flush() -> None:
    """同步刷新 Langfuse 异步上报队列。

    在应用关闭（FastAPI lifespan shutdown）或测试结束时调用，确保 trace 数据
    已发送到 Langfuse 服务。未启用时是 no-op。

    Langfuse SDK 默认异步批量上报，进程退出时未刷新会丢失最后一批 trace。
    """

    if not is_langfuse_enabled():
        return

    from langfuse.decorators import langfuse_context

    langfuse_context.flush()


def _build_run_config(
    handler: BaseCallbackHandler | None,
    extra_callbacks: Sequence[BaseCallbackHandler] | None = None,
) -> dict[str, Any] | None:
    """构造 LangChain ``RunnableConfig``（含 callbacks）。

    工具函数：把 Langfuse handler 与其他 callback 合并为 ``RunnableConfig``，
    ``handler`` 与 ``extra_callbacks`` 都为空时返回 ``None``（让 LangChain 用
    默认行为，避免空 callbacks 列表触发不必要的回调链初始化）。

    Args:
        handler: Langfuse callback handler（来自 ``get_current_langchain_handler``）。
        extra_callbacks: 其他额外 callback（可选，预留扩展）。

    Returns:
        ``{"callbacks": [...]}`` 或 ``None``。
    """

    callbacks: list[BaseCallbackHandler] = []
    if handler is not None:
        callbacks.append(handler)
    if extra_callbacks:
        callbacks.extend(extra_callbacks)
    if not callbacks:
        return None
    return {"callbacks": callbacks}


# ---------------------------------------------------------------------------
# 公开 API（供 services/qa_service.py 与测试导入）
# ---------------------------------------------------------------------------

__all__ = [
    "LangfuseConfig",
    "_build_run_config",
    "flush",
    "get_current_langchain_handler",
    "is_langfuse_enabled",
    "load_langfuse_config_from_env",
    "observe",
]
