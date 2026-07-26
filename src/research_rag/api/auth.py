"""API Key 认证依赖（阶段 11.1）。

依据 Issue #74 验收标准、[docs/ROADMAP.md 阶段 11.1](../../docs/ROADMAP.md#111-认证鉴权)。

用 FastAPI ``HTTPBearer`` 从 ``Authorization: Bearer <key>`` 提取 token，
与环境变量 ``API_KEYS`` 配置的有效 key 集合比对。``API_KEY_ENABLED=true``
时启用，默认禁用（开发友好，向后兼容现有测试）。

设计取舍（初学者向说明）：
- **HTTPBearer 而非 APIKeyHeader**：用标准 ``Authorization: Bearer <key>``
  格式，与未来 JWT 切换后的 Bearer 格式一致，前端请求头不用改。``auto_error=False``
  让我们手动返回 401（HTTPBearer 默认返回 403，与 ROADMAP 验收「未认证 401」不符）。
- **开关默认禁用**：参考项目已有的 Langfuse no-op 优先模式（``LANGFUSE_*``
  三项非空才启用）。``API_KEY_ENABLED`` 未设或非 ``true`` 时 ``verify_api_key``
  直接放行返回 ``None``，保证现有 620+ 测试零改动、本地开发零配置。生产部署
  显式 ``API_KEY_ENABLED=true`` 启用。
- **多 key 支持**：``API_KEYS`` 逗号分隔，支持不同客户端（UI、脚本、外部服务）
  各自一个 key。泄露时只轮换受影响的那一个，无需全局更换。实现成本与单 key
  相同（``set`` 比对）。
- **``secrets.compare_digest`` 恒定时间比对**：防止通过响应耗时差异逐字符
  猜测 key 的时序攻击。逐个 key 比对，任一命中即通过。
- **启用但未配置 key 时安全失败**：``API_KEY_ENABLED=true`` 但 ``API_KEYS``
  为空时，所有请求 401。避免「以为启用了认证但实际无人能通过」的配置静默错误
  变成「认证形同虚设」。
- **不引入 User 表**：API Key 是服务级凭证，环境变量管理即可。后续用户注册
  登录系统 + JWT 作为独立 Issue（本次预留 HTTPBearer 格式兼容，切换成本低）。
- **依赖注入而非中间件**：用 ``Depends(verify_api_key)`` 挂到 ``app.include_router``
  的 ``dependencies`` 参数，集中在一处管理，所有 ``/api/v1/*`` 端点自动生效。
  未来某些端点（如健康检查）可选择性豁免。
"""

from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from research_rag.secrets import get_secret

# HTTPBearer 从 Authorization: Bearer <token> 提取 credentials。
# auto_error=False：无 Authorization 头或格式错误时不自动抛 403，
# 改由 verify_api_key 手动返回 401（与 ROADMAP 验收「未认证 401」一致）。
_bearer_scheme = HTTPBearer(auto_error=False)


def is_auth_enabled() -> bool:
    """认证是否启用。

    ``API_KEY_ENABLED`` 环境变量为 ``true``（大小写不敏感）时启用。
    未设置或其他值时禁用（开发友好，向后兼容现有测试）。
    """

    return os.environ.get("API_KEY_ENABLED", "").strip().lower() == "true"


def _get_valid_api_keys() -> set[str]:
    """从 ``API_KEYS`` 读取有效 key 集合。

    通过 ``get_secret`` 读取，支持 ``API_KEYS_FILE`` 后缀挂载 docker secrets
    （阶段 11.6 切片 C）。文件内容或环境变量值均为逗号分隔，自动去除空白。
    空字符串或未设置时返回空集合。启用认证但集合为空时，``verify_api_key``
    会拒绝所有请求（安全失败）。
    """

    raw = get_secret("API_KEYS") or ""
    return {k.strip() for k in raw.split(",") if k.strip()}


def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str | None:
    """校验 API Key 依赖。

    - **禁用认证**（``API_KEY_ENABLED`` 非 true）：直接放行，返回 ``None``。
    - **启用认证**：
      - ``API_KEYS`` 为空：401（安全失败，避免认证形同虚设）。
      - 无 ``Authorization`` 头或非 Bearer scheme：401。
      - token 不在有效集合：401。
      - token 命中：返回 token 字符串（供后续日志/限流标识调用方，本阶段未使用）。

    Args:
        credentials: ``HTTPBearer`` 从请求头提取的凭证。``auto_error=False``
            时无头或格式错误为 ``None``。

    Returns:
        禁用认证时返回 ``None``；启用且校验通过时返回 token 字符串。

    Raises:
        HTTPException: 401 —— 启用认证但凭证缺失/无效/未配置 key。
    """

    if not is_auth_enabled():
        return None

    valid_keys = _get_valid_api_keys()
    if not valid_keys:
        # 启用认证但未配置任何 key：安全失败，全部 401。
        # 避免管理员以为启用了认证，实际无人能通过却静默放行。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证已启用但未配置有效 API Key（API_KEYS 为空），请联系管理员",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭证：请在请求头携带 Authorization: Bearer <api-key>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 恒定时间比对，防时序攻击：逐个 key 与 token 比对，任一命中即通过。
    token = credentials.credentials
    if not any(secrets.compare_digest(token, k) for k in valid_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token
