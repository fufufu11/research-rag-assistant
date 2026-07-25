"""API 请求限流模块（阶段 11.3）。

依据 Issue #78 验收标准、[docs/ROADMAP.md 阶段 11.3](../../docs/ROADMAP.md#113-api-限流)。

用 ``slowapi`` 库实现按用户/IP 的请求限流，超频返回 429。
``RATE_LIMIT_ENABLED=true`` 时启用，默认禁用（开发友好，保护现有 720+ 测试）。

设计取舍（初学者向说明）：
- **slowapi 而非自实现或 Nginx**：slowapi 是 FastAPI/Starlette 兼容的限流库，
  支持按 key/IP 限流、固定窗口、内存级零外部依赖。自实现需手写并发安全、
  IP 解析、``Retry-After`` 计算，重复造轮子。Nginx ``limit_req`` 仅部署层生效，
  开发/测试环境无 Nginx，且无法按 API Key 限流（Nginx 看不到 Bearer 语义）。
- **默认禁用（``RATE_LIMIT_ENABLED=false``）**：与 11.1 认证默认禁用一致，
  而非 11.2 输入校验默认启用。理由：① 限流会影响合法高频请求（如批量评测脚本
  ``scripts/evaluate_*.py`` 跑 30 条问题、CI 测试套件 720+ 测试），默认开启会
  误伤；② 限流是防滥用，非安全漏洞，默认禁用风险可控；③ 生产部署显式
  ``RATE_LIMIT_ENABLED=true`` 启用。
- **按 API Key 优先于 IP**：公司出口 IP 共享，按 IP 限流会误伤同公司不同用户。
  认证启用时按 key 更精确。认证禁用时回退 IP（开发/调试场景）。key 函数在
  中间件层运行（早于认证依赖），只提取 token 不校验合法性——校验由 11.1
  ``verify_api_key`` 负责，无效 key 会被 401 拒绝。
- **上传端点单独更严**：``POST /api/v1/documents`` 涉及 PDF 解析+切分+Embedding+
  Qdrant 写入，单请求耗时 5-30 秒，比问答重。单独 10/min 限制（``@limiter.limit``
  装饰器覆盖默认 60/min），避免上传刷接口拖垮服务。
- **固定窗口而非滑动窗口**：slowapi 默认固定窗口，边界处可能短时双倍流量
  （窗口结束+新窗口开始各 60 次），当前规模可接受。完美精准需 Redis + 滑动窗口，YAGNI。
- **内存级而非 Redis**：单实例部署足够。多副本时 slowapi 支持 Redis 后端
  （改 ``storage_uri``），切换成本低。
- **callable default_limits**：``default_limits`` 传入 lambda 函数，在请求时
  动态求值（读环境变量），而非模块导入时固定。这样测试用 ``monkeypatch.setenv``
  即可动态调整限流频率，无需重新导入模块。
- **``Retry-After`` 头**：429 响应必须携带（HTTP 标准），告诉客户端何时重试。
  slowapi 自动计算，通过 ``_inject_headers`` 注入响应头。
- **自定义异常处理器**：捕获 ``RateLimitExceeded`` 返回 ``ErrorResponse`` JSON 体
  （与 11.1/11.2 错误格式一致），同时注入 ``Retry-After`` / ``X-RateLimit-*`` 头。
  处理器必须是同步函数（非 ``async``）——slowapi 中间件是同步的，无法调用异步处理器。
- **不引入新异常类**：捕获 ``slowapi.errors.RateLimitExceeded`` 直接返回 429
  ``JSONResponse``，与 11.2 风格一致。
- **monkey-patch ``slowapi.middleware._find_route_handler``**：FastAPI 0.139+
  将 ``app.include_router`` 注册的路由包装成 ``_IncludedRouter`` 对象，该对象
  没有 ``endpoint`` 属性，slowapi 默认实现因 ``hasattr(route, "endpoint")``
  为 False 而找不到路由处理器，导致 ``default_limits`` 不生效。本模块在导入时
  替换该函数为兼容版本：遇到 ``_IncludedRouter`` 时深入 ``original_router.routes``
  查找匹配的端点。详见 ``_patch_find_route_handler``。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import slowapi.middleware as _slowapi_middleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.routing import Match as _StarletteMatch

from research_rag.api.auth import is_auth_enabled
from research_rag.api.schemas import ErrorResponse

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any

    from fastapi import Request
    from starlette.responses import JSONResponse
    from starlette.routing import BaseRoute
    from starlette.types import Scope

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

# 默认限流频率（每分钟请求数）。可通过 RATE_LIMIT_PER_MINUTE 环境变量覆盖。
# 60/min = 每秒 1 次，满足正常问答/对话/反馈交互，防脚本循环滥用。
DEFAULT_RATE_LIMIT_PER_MINUTE = 60

# 上传端点默认限流频率（每分钟请求数）。可通过 RATE_LIMIT_UPLOAD_PER_MINUTE 覆盖。
# 10/min = 每 6 秒 1 次，比默认更严，因为上传涉及 PDF 解析+切分+索引，单请求重。
DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE = 10


# ---------------------------------------------------------------------------
# 配置读取（请求时动态求值，支持 monkeypatch 测试）
# ---------------------------------------------------------------------------


def is_rate_limit_enabled() -> bool:
    """限流是否启用。

    ``RATE_LIMIT_ENABLED`` 环境变量为 ``true``（大小写不敏感）时启用。
    未设置或其他值时禁用（开发友好，保护现有 720+ 测试不被限流误伤）。

    与 11.1 ``is_auth_enabled`` 风格一致：默认禁用，生产部署显式启用。
    """

    return os.environ.get("RATE_LIMIT_ENABLED", "").strip().lower() == "true"


def get_rate_limit_per_minute() -> int:
    """读取默认限流频率（每分钟请求数）。

    从 ``RATE_LIMIT_PER_MINUTE`` 环境变量读取，解析失败或非正数时回退到
    ``DEFAULT_RATE_LIMIT_PER_MINUTE``（60）。

    Returns:
        每分钟允许的请求数。如 60 表示 60 请求/分钟。
    """

    raw = os.environ.get("RATE_LIMIT_PER_MINUTE")
    if raw is None:
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    # 非正数视为未配置，回退默认值（避免 0 或负值导致所有请求被拒）
    if value <= 0:
        return DEFAULT_RATE_LIMIT_PER_MINUTE
    return value


def get_rate_limit_upload_per_minute() -> int:
    """读取上传端点限流频率（每分钟请求数）。

    从 ``RATE_LIMIT_UPLOAD_PER_MINUTE`` 环境变量读取，解析失败或非正数时
    回退到 ``DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE``（10）。

    上传端点比默认更严，因为 PDF 解析+切分+Embedding+Qdrant 写入单请求耗时 5-30 秒。

    Returns:
        每分钟允许的上传请求数。如 10 表示 10 上传/分钟。
    """

    raw = os.environ.get("RATE_LIMIT_UPLOAD_PER_MINUTE")
    if raw is None:
        return DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE
    if value <= 0:
        return DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE
    return value


# ---------------------------------------------------------------------------
# Key 函数：按 API Key 或客户端 IP 标识调用方
# ---------------------------------------------------------------------------


def extract_bearer_token(request: Request) -> str | None:
    """从 ``Authorization: Bearer <token>`` 头提取 token。

    在中间件层运行（早于认证依赖），只提取不校验合法性——校验由 11.1
    ``verify_api_key`` 负责。无 Authorization 头或非 Bearer scheme 时返回 ``None``。

    Args:
        request: Starlette/FastAPI 请求对象。

    Returns:
        Bearer token 字符串，或 ``None``（无凭证 / 非 Bearer scheme）。
    """

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return None
    # 标准 Bearer 格式："Bearer <token>"，大小写不敏感匹配 scheme
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def get_client_ip(request: Request) -> str:
    """提取客户端 IP 地址。

    优先取 ``X-Forwarded-For`` 首段（反向代理如 Nginx 注入的真实客户端 IP），
    回退到 ``request.client.host``（直连场景）。两者都不可用时返回 ``unknown``。

    ``X-Forwarded-For`` 格式：``client, proxy1, proxy2``，取首个（最原始的客户端）。
    注意：此值可被客户端伪造，生产环境应在 Nginx 层覆盖该头，不信任客户端传入。

    Args:
        request: Starlette/FastAPI 请求对象。

    Returns:
        客户端 IP 字符串，或 ``"unknown"``（无法确定时）。
    """

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # X-Forwarded-For: client, proxy1, proxy2 → 取首个（最原始客户端）
        first_ip = forwarded.split(",")[0].strip()
        if first_ip:
            return first_ip
    # 回退到直连客户端 IP
    if request.client is not None:
        return request.client.host
    return "unknown"


def rate_limit_key(request: Request) -> str:
    """限流 key 函数：按 API Key 或客户端 IP 标识调用方。

    优先级：
    1. **认证启用 + Bearer token 存在**：用 ``"key:<token>"`` 标识。
       公司出口 IP 共享，按 key 限流更精确，避免同公司不同用户相互影响。
    2. **认证禁用 或 无 token**：用 ``"ip:<client-ip>"`` 标识。
       开发/调试场景或匿名访问时按 IP 限流。

    注意：key 函数在中间件层运行（早于认证依赖），只提取 token 不校验合法性。
    无效 token 会被 11.1 ``verify_api_key`` 401 拒绝，但限流计数已计入该 token。
    攻击者轮换假 key 可获得每个 key 60/min 配额，但每个请求都返回 401，
    不消耗 LLM/检索资源，IP 级限流应部署在 Nginx 层（超出本 Issue 范围）。

    Args:
        request: Starlette/FastAPI 请求对象。

    Returns:
        限流标识字符串，格式 ``"key:<token>"`` 或 ``"ip:<ip>"``。
    """

    if is_auth_enabled():
        token = extract_bearer_token(request)
        if token:
            return f"key:{token}"
    return f"ip:{get_client_ip(request)}"


# ---------------------------------------------------------------------------
# Limiter 实例（模块级单例）
# ---------------------------------------------------------------------------

# default_limits 用 lambda（callable）在请求时动态求值，读环境变量。
# 这样测试用 monkeypatch.setenv 即可调整限流频率，无需重新导入模块。
# headers_enabled=True 让响应携带 X-RateLimit-Limit / X-RateLimit-Remaining /
# X-RateLimit-Reset / Retry-After 头，便于客户端感知配额。
# enabled=False 默认禁用，由 configure_limiter() 在 create_app 时按环境变量启用。
limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=[lambda: f"{get_rate_limit_per_minute()}/minute"],
    headers_enabled=True,
    enabled=False,
)


# ---------------------------------------------------------------------------
# monkey-patch: FastAPI 0.139+ _IncludedRouter 兼容
# ---------------------------------------------------------------------------


def _patch_find_route_handler() -> None:
    """替换 slowapi.middleware._find_route_handler 以兼容 FastAPI 0.139+。

    背景：FastAPI 0.139 起 ``app.include_router`` 不再把每个子路由展开到
    ``app.routes``，而是用 ``_IncludedRouter`` 包装原 ``APIRouter``。
    slowapi 0.1.9 的 ``_find_route_handler`` 只检查 ``route.endpoint`` 属性，
    ``_IncludedRouter`` 没有该属性，导致 ``default_limits`` 对所有
    ``/api/v1/*`` 端点失效（请求不被限流）。

    修复：替换为兼容版本，遇到 ``_IncludedRouter`` 时深入
    ``original_router.routes`` 用子路由的 ``matches`` 重新匹配并取 ``endpoint``。
    对普通 ``Route`` / ``APIRoute`` 保持原逻辑不变。

    通过模块导入时执行一次 ``_patch_find_route_handler()`` 全局生效。
    测试无需特殊处理（patch 在测试导入本模块时已应用）。
    """

    def _find_route_handler_compat(routes: Iterable[BaseRoute], scope: Scope) -> Any:
        handler: Any = None
        for route in routes:
            # FastAPI 0.139+: _IncludedRouter 包装原 router，需深入 original_router
            # 用 type 名匹配避免 import _IncludedRouter（私有 API，导入路径可能变）
            if type(route).__name__ == "_IncludedRouter" and hasattr(route, "original_router"):
                for sub_route in route.original_router.routes:
                    if not hasattr(sub_route, "matches"):
                        continue
                    try:
                        match, _ = sub_route.matches(scope)
                    except Exception:
                        continue
                    if match == _StarletteMatch.FULL and hasattr(sub_route, "endpoint"):
                        handler = sub_route.endpoint
                        break
                if handler is not None:
                    continue
            # 普通 Route / APIRoute（FastAPI 0.139 前或非 include_router 注册的路由）
            if not hasattr(route, "matches"):
                continue
            try:
                match, _ = route.matches(scope)
            except Exception:
                continue
            if match == _StarletteMatch.FULL and hasattr(route, "endpoint"):
                handler = route.endpoint
        return handler

    _slowapi_middleware._find_route_handler = _find_route_handler_compat


# 模块导入时执行 patch（覆盖 slowapi 原实现，对全局 limiter 中间件生效）
_patch_find_route_handler()


def configure_limiter() -> None:
    """根据环境变量配置模块级 ``limiter`` 单例。

    在 ``create_app()`` 中调用，每次创建应用时按当前 ``RATE_LIMIT_ENABLED``
    环境变量设置 ``limiter.enabled``。限流频率（``RATE_LIMIT_PER_MINUTE`` 等）
    通过 ``default_limits`` 的 callable 在请求时动态读取，无需在此配置。

    测试隔离：因 ``limiter`` 是模块级单例，``limiter.enabled`` 跨测试共享。
    pytest 顺序执行，每个测试的 ``app`` fixture 调 ``create_app()`` →
    ``configure_limiter()`` 重设 ``enabled``，保证测试间不串扰。
    限流计数器（内存存储）需用 ``reset_rate_limit_storage()`` 在 fixture 中重置。
    """

    limiter.enabled = is_rate_limit_enabled()


def reset_rate_limit_storage() -> None:
    """重置限流计数器内存存储。

    测试 fixture 调用，避免前一个测试的计数器影响后一个测试。
    生产环境无需调用（计数器随窗口自动过期）。
    """

    limiter._storage.reset()


# ---------------------------------------------------------------------------
# 429 异常处理器
# ---------------------------------------------------------------------------


def handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 限流异常处理器：返回 ``ErrorResponse`` JSON 体 + ``Retry-After`` 头。

    替换 slowapi 默认的 ``{"error": "Rate limit exceeded: ..."}`` 响应体为
    项目统一的 ``ErrorResponse(detail=...)`` 格式（与 11.1/11.2 错误一致）。
    保留 ``Retry-After`` / ``X-RateLimit-*`` 响应头（通过 ``_inject_headers`` 注入）。

    **必须是同步函数**（非 ``async``）：slowapi 中间件（``SlowAPIMiddleware``）
    是同步的，无法调用异步异常处理器——异步处理器会被 fallback 到默认 handler。

    Args:
        request: 触发限流的请求对象。``request.state.view_rate_limit`` 由中间件
            设置，包含当前窗口的限流状态（用于计算 ``Retry-After``）。
        exc: ``RateLimitExceeded`` 异常，``exc.detail`` 为限流规则描述。

    Returns:
        ``JSONResponse``：status 429，body ``{"detail": "..."}``，
        headers 含 ``Retry-After`` / ``X-RateLimit-*``。
    """

    from fastapi.responses import JSONResponse

    response: JSONResponse = JSONResponse(
        status_code=429,
        content=ErrorResponse(
            detail="请求过于频繁，已触发限流。请稍后重试（参考 Retry-After 头）。"
        ).model_dump(),
    )
    # 注入 Retry-After / X-RateLimit-* 头（与 slowapi 默认 handler 一致）
    # request.state.view_rate_limit 由 SlowAPIMiddleware 在调本处理器前设置
    # ``_inject_headers`` 修改 response.headers 并返回原 Response（类型缩窄为
    # JSONResponse），赋值用 cast 表达"返回的就是传入的 response"语义。
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        limiter._inject_headers(response, view_rate_limit)
    return response


__all__ = [
    "DEFAULT_RATE_LIMIT_PER_MINUTE",
    "DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE",
    "RateLimitExceeded",
    "SlowAPIMiddleware",
    "configure_limiter",
    "extract_bearer_token",
    "get_client_ip",
    "get_rate_limit_per_minute",
    "get_rate_limit_upload_per_minute",
    "handle_rate_limit_exceeded",
    "is_rate_limit_enabled",
    "limiter",
    "rate_limit_key",
    "reset_rate_limit_storage",
]
