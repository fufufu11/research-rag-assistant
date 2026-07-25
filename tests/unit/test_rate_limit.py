"""API 限流模块单元测试（阶段 11.3，Issue #78）。

测试覆盖：
- ``is_rate_limit_enabled`` 环境变量解析（默认禁用、true 启用、大小写）
- ``get_rate_limit_per_minute`` / ``get_rate_limit_upload_per_minute`` 环境变量解析
  （默认值、合法值、非法值、零/负值）
- ``extract_bearer_token`` 头解析（标准 Bearer、无头、错误 scheme、空 token）
- ``get_client_ip`` IP 提取（X-Forwarded-For、直连 client.host、未知）
- ``rate_limit_key`` 限流 key 函数（认证启用按 key、认证禁用按 IP）
- 集成测试（``TestClient`` + mock service）：
  - 禁用限流时端点正常访问（向后兼容）
  - 启用限流后超频返回 429 + ``ErrorResponse`` body
  - 429 响应携带 ``Retry-After`` / ``X-RateLimit-*`` 头
  - 上传端点用更严的限流（独立配额）
  - 按 API Key 限流（认证启用场景）
  - 按 IP 限流（认证禁用场景）

测试策略：
- 纯单元测试：``monkeypatch.setenv`` / ``delenv`` 控制环境变量，构造 mock
  ``Request`` 直接调用 key 函数，断言返回值。
- 集成测试：``create_app`` + 内存 SQLite factory + ``dependency_overrides`` 替换
  service 为 ``MagicMock``，用 ``TestClient`` 发真实 HTTP 请求，验证限流行为。
- ``reset_rate_limit_storage`` 在 fixture 中调用，避免前一个测试的计数器影响后一个。
  ``limiter`` 是模块级单例，``limiter.enabled`` 跨测试共享，pytest 顺序执行，
  每个测试的 ``app`` fixture 调 ``create_app()`` → ``configure_limiter()`` 重设
  ``enabled``，保证测试间不串扰。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from research_rag.api.app import create_app
from research_rag.api.dependencies import get_document_service
from research_rag.api.rate_limit import (
    DEFAULT_RATE_LIMIT_PER_MINUTE,
    DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE,
    extract_bearer_token,
    get_client_ip,
    get_rate_limit_per_minute,
    get_rate_limit_upload_per_minute,
    is_rate_limit_enabled,
    rate_limit_key,
    reset_rate_limit_storage,
)
from research_rag.db.session import create_session_factory
from research_rag.services.document_service import DocumentService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI, Request


# ---------------------------------------------------------------------------
# 辅助函数：构造 mock Request
# ---------------------------------------------------------------------------


def _make_request(
    headers: dict[str, str] | None = None,
    client_host: str | None = "127.0.0.1",
) -> Request:
    """构造一个 mock Request，便于测试 key 函数。

    用 ``MagicMock(spec=Request)`` 限定接口，``headers`` 字典模拟请求头，
    ``client`` 用 ``MagicMock(host=...)`` 模拟直连客户端。
    """

    from fastapi import Request

    request = MagicMock(spec=Request)
    request.headers = headers or {}
    if client_host is not None:
        request.client = MagicMock()
        request.client.host = client_host
    else:
        request.client = None
    return request


# ---------------------------------------------------------------------------
# 纯单元测试：is_rate_limit_enabled
# ---------------------------------------------------------------------------


class TestIsRateLimitEnabled:
    """``RATE_LIMIT_ENABLED`` 环境变量解析。"""

    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置时默认禁用（与 11.1 认证一致，向后兼容现有 720+ 测试）。"""

        monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
        assert is_rate_limit_enabled() is False

    def test_enabled_when_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        assert is_rate_limit_enabled() is True

    def test_disabled_when_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        assert is_rate_limit_enabled() is False

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """大小写不敏感：TRUE / True 均识别。"""

        monkeypatch.setenv("RATE_LIMIT_ENABLED", "TRUE")
        assert is_rate_limit_enabled() is True
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "True")
        assert is_rate_limit_enabled() is True

    def test_disabled_when_arbitrary_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 true 值（如 yes / 1 / on）均视为禁用，避免歧义。"""

        monkeypatch.setenv("RATE_LIMIT_ENABLED", "yes")
        assert is_rate_limit_enabled() is False
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
        assert is_rate_limit_enabled() is False

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """前后空白去除：'  true  ' 视为 true。"""

        monkeypatch.setenv("RATE_LIMIT_ENABLED", "  true  ")
        assert is_rate_limit_enabled() is True


# ---------------------------------------------------------------------------
# 纯单元测试：get_rate_limit_per_minute
# ---------------------------------------------------------------------------


class TestGetRateLimitPerMinute:
    """``RATE_LIMIT_PER_MINUTE`` 环境变量解析。"""

    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置时回退到默认 60/min。"""

        monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)
        assert get_rate_limit_per_minute() == DEFAULT_RATE_LIMIT_PER_MINUTE
        assert get_rate_limit_per_minute() == 60

    def test_custom_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "100")
        assert get_rate_limit_per_minute() == 100

    def test_small_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """1/min 也可设置（边界值）。"""

        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
        assert get_rate_limit_per_minute() == 1

    def test_invalid_value_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非整数（如 abc）回退默认值，而非抛异常。"""

        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "abc")
        assert get_rate_limit_per_minute() == DEFAULT_RATE_LIMIT_PER_MINUTE

    def test_zero_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0 视为未配置，回退默认值（避免所有请求被拒）。"""

        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
        assert get_rate_limit_per_minute() == DEFAULT_RATE_LIMIT_PER_MINUTE

    def test_negative_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """负值视为未配置，回退默认值。"""

        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "-5")
        assert get_rate_limit_per_minute() == DEFAULT_RATE_LIMIT_PER_MINUTE


# ---------------------------------------------------------------------------
# 纯单元测试：get_rate_limit_upload_per_minute
# ---------------------------------------------------------------------------


class TestGetRateLimitUploadPerMinute:
    """``RATE_LIMIT_UPLOAD_PER_MINUTE`` 环境变量解析。"""

    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置时回退到默认 10/min（比默认 60 更严）。"""

        monkeypatch.delenv("RATE_LIMIT_UPLOAD_PER_MINUTE", raising=False)
        assert get_rate_limit_upload_per_minute() == DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE
        assert get_rate_limit_upload_per_minute() == 10

    def test_custom_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RATE_LIMIT_UPLOAD_PER_MINUTE", "5")
        assert get_rate_limit_upload_per_minute() == 5

    def test_invalid_value_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非整数回退默认值。"""

        monkeypatch.setenv("RATE_LIMIT_UPLOAD_PER_MINUTE", "fast")
        assert get_rate_limit_upload_per_minute() == DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE

    def test_zero_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0 视为未配置，回退默认值。"""

        monkeypatch.setenv("RATE_LIMIT_UPLOAD_PER_MINUTE", "0")
        assert get_rate_limit_upload_per_minute() == DEFAULT_RATE_LIMIT_UPLOAD_PER_MINUTE


# ---------------------------------------------------------------------------
# 纯单元测试：extract_bearer_token
# ---------------------------------------------------------------------------


class TestExtractBearerToken:
    """``Authorization: Bearer <token>`` 头解析。"""

    def test_standard_bearer_token(self) -> None:
        request = _make_request(headers={"Authorization": "Bearer abc123"})
        assert extract_bearer_token(request) == "abc123"

    def test_case_insensitive_scheme(self) -> None:
        """scheme 大小写不敏感：bearer / BEARER 均识别。"""

        request = _make_request(headers={"Authorization": "bearer abc123"})
        assert extract_bearer_token(request) == "abc123"
        request = _make_request(headers={"Authorization": "BEARER abc123"})
        assert extract_bearer_token(request) == "abc123"

    def test_no_authorization_header(self) -> None:
        request = _make_request(headers={})
        assert extract_bearer_token(request) is None

    def test_wrong_scheme(self) -> None:
        """非 Bearer scheme（如 Basic）：返回 None。"""

        request = _make_request(headers={"Authorization": "Basic abc123"})
        assert extract_bearer_token(request) is None

    def test_empty_token(self) -> None:
        """Bearer 后为空：返回 None。"""

        request = _make_request(headers={"Authorization": "Bearer "})
        assert extract_bearer_token(request) is None

    def test_no_space_after_scheme(self) -> None:
        """格式错误（无空格分隔）：返回 None。"""

        request = _make_request(headers={"Authorization": "Bearerabc123"})
        assert extract_bearer_token(request) is None

    def test_token_with_spaces(self) -> None:
        """token 内含空格（用 ``split(' ', 1)``）：取后半段。

        极端情况：token 内含空格在标准 Bearer 中不合法，但函数不强制校验，
        交给 11.1 ``verify_api_key`` 拒绝。
        """

        request = _make_request(headers={"Authorization": "Bearer abc 123"})
        # split(" ", 1) 取首个空格后的全部内容，strip 后为 "abc 123"
        assert extract_bearer_token(request) == "abc 123"


# ---------------------------------------------------------------------------
# 纯单元测试：get_client_ip
# ---------------------------------------------------------------------------


class TestGetClientIp:
    """客户端 IP 提取。"""

    def test_direct_connection(self) -> None:
        """无 X-Forwarded-For：用 client.host。"""

        request = _make_request(headers={}, client_host="192.168.1.100")
        assert get_client_ip(request) == "192.168.1.100"

    def test_x_forwarded_for_single(self) -> None:
        """X-Forwarded-For 单 IP：取该 IP。"""

        request = _make_request(
            headers={"X-Forwarded-For": "10.0.0.1"},
            client_host="127.0.0.1",
        )
        assert get_client_ip(request) == "10.0.0.1"

    def test_x_forwarded_for_multiple(self) -> None:
        """X-Forwarded-For 多 IP：取首个（最原始客户端）。"""

        request = _make_request(
            headers={"X-Forwarded-For": "10.0.0.1, 10.0.0.2, 10.0.0.3"},
            client_host="127.0.0.1",
        )
        assert get_client_ip(request) == "10.0.0.1"

    def test_x_forwarded_for_with_whitespace(self) -> None:
        """X-Forwarded-For 首段前后空白被去除。"""

        request = _make_request(
            headers={"X-Forwarded-For": "  10.0.0.1  , 10.0.0.2"},
            client_host="127.0.0.1",
        )
        assert get_client_ip(request) == "10.0.0.1"

    def test_empty_x_forwarded_for_falls_back_to_client(self) -> None:
        """X-Forwarded-For 为空字符串：回退到 client.host。"""

        request = _make_request(
            headers={"X-Forwarded-For": ""},
            client_host="127.0.0.1",
        )
        assert get_client_ip(request) == "127.0.0.1"

    def test_no_client_returns_unknown(self) -> None:
        """client 为 None（极端场景，如 WebSocket 握手前）：返回 'unknown'。"""

        request = _make_request(headers={}, client_host=None)
        assert get_client_ip(request) == "unknown"


# ---------------------------------------------------------------------------
# 纯单元测试：rate_limit_key
# ---------------------------------------------------------------------------


class TestRateLimitKey:
    """限流 key 函数：按 API Key 或 IP 标识调用方。"""

    def test_auth_disabled_uses_ip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """认证禁用：用 ``ip:<ip>`` 标识。"""

        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        request = _make_request(headers={}, client_host="192.168.1.100")
        assert rate_limit_key(request) == "ip:192.168.1.100"

    def test_auth_enabled_with_bearer_token_uses_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """认证启用 + Bearer token 存在：用 ``key:<token>``。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        request = _make_request(
            headers={"Authorization": "Bearer my-secret"},
            client_host="192.168.1.100",
        )
        assert rate_limit_key(request) == "key:my-secret"

    def test_auth_enabled_without_token_falls_back_to_ip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """认证启用但无 token：回退 IP（如未携带凭证的请求，由 11.1 401 拒绝）。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        request = _make_request(headers={}, client_host="192.168.1.100")
        assert rate_limit_key(request) == "ip:192.168.1.100"

    def test_auth_enabled_wrong_scheme_falls_back_to_ip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """认证启用但 scheme 错误（如 Basic）：回退 IP。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        request = _make_request(
            headers={"Authorization": "Basic abc"},
            client_host="10.0.0.1",
        )
        assert rate_limit_key(request) == "ip:10.0.0.1"

    def test_uses_x_forwarded_for_when_auth_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """认证禁用 + X-Forwarded-For：用代理注入的 IP。"""

        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        request = _make_request(
            headers={"X-Forwarded-For": "203.0.113.5"},
            client_host="127.0.0.1",
        )
        assert rate_limit_key(request) == "ip:203.0.113.5"

    def test_different_tokens_different_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """不同 Bearer token 生成不同 key（按 key 隔离配额）。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        req1 = _make_request(headers={"Authorization": "Bearer key1"})
        req2 = _make_request(headers={"Authorization": "Bearer key2"})
        assert rate_limit_key(req1) != rate_limit_key(req2)
        assert rate_limit_key(req1) == "key:key1"
        assert rate_limit_key(req2) == "key:key2"


# ---------------------------------------------------------------------------
# 集成测试 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_service() -> MagicMock:
    """``MagicMock(spec=DocumentService)``：限定只能调 DocumentService 的方法。"""

    return MagicMock(spec=DocumentService)


@pytest.fixture
def app_no_rate_limit(mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """禁用限流的 app（默认场景，验证向后兼容）。

    关闭 Qdrant/Reranker 避免 lifespan 卡住，禁用认证避免 401 干扰限流测试。
    """

    monkeypatch.setenv("QDRANT_ENABLED", "false")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.delenv("API_KEY_ENABLED", raising=False)
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)

    app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
    app.dependency_overrides[get_document_service] = lambda: mock_service
    return app


@pytest.fixture
def app_rate_limit(mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """启用限流的 app（默认 60/min，可在测试函数内用 monkeypatch 覆盖）。

    每个测试前重置存储，避免前一个测试的计数器影响后一个。
    """

    monkeypatch.setenv("QDRANT_ENABLED", "false")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.delenv("API_KEY_ENABLED", raising=False)
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    # 默认 60/min（不覆盖 RATE_LIMIT_PER_MINUTE），测试函数内可降低便于验证

    reset_rate_limit_storage()
    app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
    app.dependency_overrides[get_document_service] = lambda: mock_service
    return app


@pytest.fixture
def client_no_rate_limit(app_no_rate_limit: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_no_rate_limit) as c:
        yield c


@pytest.fixture
def client_rate_limit(app_rate_limit: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_rate_limit) as c:
        yield c


# ---------------------------------------------------------------------------
# 集成测试：禁用限流（向后兼容）
# ---------------------------------------------------------------------------


class TestRateLimitDisabled:
    """限流禁用场景：所有端点正常访问，不被限流拦截。"""

    def test_allows_unlimited_requests(
        self,
        client_no_rate_limit: TestClient,
        mock_service: MagicMock,
    ) -> None:
        """禁用限流时连续请求不返回 429（向后兼容现有 720+ 测试）。"""

        mock_service.list_documents.return_value = []
        # 连续发 70 个请求（超过默认 60/min 上限），都不应被限流
        for _ in range(70):
            response = client_no_rate_limit.get("/api/v1/documents")
            assert response.status_code == 200

    def test_no_rate_limit_headers_when_disabled(
        self,
        client_no_rate_limit: TestClient,
        mock_service: MagicMock,
    ) -> None:
        """禁用限流时响应不携带 X-RateLimit-* 头。"""

        mock_service.list_documents.return_value = []
        response = client_no_rate_limit.get("/api/v1/documents")
        assert "x-ratelimit-limit" not in {k.lower() for k in response.headers}
        assert "retry-after" not in {k.lower() for k in response.headers}


# ---------------------------------------------------------------------------
# 集成测试：启用限流
# ---------------------------------------------------------------------------


class TestRateLimitEnabled:
    """限流启用场景：超频返回 429 + ErrorResponse body + Retry-After 头。"""

    def test_allows_requests_under_limit(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """启用限流时，未超频请求正常返回 200。"""

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "10")  # 10/min 足够测试

        mock_service.list_documents.return_value = []
        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            for _ in range(5):
                response = c.get("/api/v1/documents")
                assert response.status_code == 200

    def test_returns_429_when_limit_exceeded(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """超频请求返回 429，body 为 ``ErrorResponse`` 格式。"""

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")  # 3/min 便于测试

        mock_service.list_documents.return_value = []
        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            # 前 3 个请求 200
            for i in range(3):
                response = c.get("/api/v1/documents")
                assert response.status_code == 200, f"req {i + 1} 应为 200"

            # 第 4 个请求 429
            response = c.get("/api/v1/documents")
            assert response.status_code == 429
            body = response.json()
            assert "detail" in body
            assert "请求过于频繁" in body["detail"]

    def test_429_response_has_rate_limit_headers(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """429 响应携带 ``Retry-After`` / ``X-RateLimit-*`` 头。"""

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")

        mock_service.list_documents.return_value = []
        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            c.get("/api/v1/documents")  # 第 1 个 200
            response = c.get("/api/v1/documents")  # 第 2 个 429

        assert response.status_code == 429
        # Retry-After 头存在（HTTP 标准，告诉客户端何时重试）
        assert "retry-after" in {k.lower() for k in response.headers}
        # X-RateLimit-* 头存在（slowapi headers_enabled=True）
        assert "x-ratelimit-limit" in {k.lower() for k in response.headers}
        assert "x-ratelimit-remaining" in {k.lower() for k in response.headers}
        assert "x-ratelimit-reset" in {k.lower() for k in response.headers}

    def test_response_has_rate_limit_headers_when_allowed(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未超频的 200 响应也携带 X-RateLimit-* 头（便于客户端感知配额）。"""

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "10")

        mock_service.list_documents.return_value = []
        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            response = c.get("/api/v1/documents")

        assert response.status_code == 200
        assert "x-ratelimit-limit" in {k.lower() for k in response.headers}
        assert "x-ratelimit-remaining" in {k.lower() for k in response.headers}

    def test_rate_limit_applies_to_all_routers(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """限流对 ``include_router`` 注册的路由生效（验证 _IncludedRouter 兼容）。

        验证 monkey-patch ``_find_route_handler`` 能透过 FastAPI 0.139+ 的
        ``_IncludedRouter`` 包装找到 ``endpoint``，使 ``default_limits`` 生效。
        若 patch 失效，``_find_route_handler`` 返回 ``None``，``_should_exempt``
        会因 ``handler is None`` 直接放行，限流不触发，第 3 个请求仍 200。
        """

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")  # 2/min 便于触发

        mock_service.list_documents.return_value = []
        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            # 同一 IP 共享配额，第 3 个请求触发限流
            r1 = c.get("/api/v1/documents")
            r2 = c.get("/api/v1/documents")
            r3 = c.get("/api/v1/documents")

        assert r1.status_code == 200
        assert r2.status_code == 200
        # patch 生效时第 3 个请求被限流；patch 失效时仍 200（测试会失败）
        assert r3.status_code == 429, (
            f"第 3 个请求应触发限流，实际 {r3.status_code}（patch 可能未生效）"
        )


# ---------------------------------------------------------------------------
# 集成测试：上传端点单独限流
# ---------------------------------------------------------------------------


class TestUploadRateLimit:
    """上传端点 ``POST /api/v1/documents`` 用更严的限流。"""

    def test_upload_has_separate_stricter_limit(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """上传端点用 ``RATE_LIMIT_UPLOAD_PER_MINUTE``（默认 10），覆盖默认 60。

        设置默认 60/min、上传 1/min，连续上传 2 次第 2 次应 429。
        同时 GET /api/v1/documents 仍可访问（不受上传计数影响）。
        """

        from research_rag.db.models import Document, DocumentStatus

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        monkeypatch.setenv("INPUT_VALIDATION_ENABLED", "false")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "60")  # 默认宽松
        monkeypatch.setenv("RATE_LIMIT_UPLOAD_PER_MINUTE", "1")  # 上传严格

        # mock service 返回一个 Document 实例
        doc = Document(
            id=uuid.uuid4(),
            original_name="test.pdf",
            stored_name="abc.pdf",
            sha256="a" * 64,
            page_count=1,
            status=DocumentStatus.READY,
            error_message=None,
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        )
        mock_service.upload_document.return_value = doc

        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            # 第 1 次上传 201
            r1 = c.post(
                "/api/v1/documents",
                files={"file": ("test.pdf", b"%PDF-1.4 content", "application/pdf")},
            )
            assert r1.status_code == 201

            # 第 2 次上传 429（超过上传配额 1/min）
            r2 = c.post(
                "/api/v1/documents",
                files={"file": ("test2.pdf", b"%PDF-1.4 content", "application/pdf")},
            )
            assert r2.status_code == 429

            # GET /api/v1/documents 仍可访问（独立配额，未触发默认 60/min）
            mock_service.list_documents.return_value = []
            r3 = c.get("/api/v1/documents")
            assert r3.status_code == 200


# ---------------------------------------------------------------------------
# 集成测试：按 API Key 限流（认证启用场景）
# ---------------------------------------------------------------------------


class TestRateLimitByKey:
    """认证启用时按 API Key 限流，不同 key 独立配额。"""

    def test_different_keys_have_independent_limits(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """不同 API Key 用独立配额，互不影响。"""

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "key1,key2")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")  # 每 key 2/min

        mock_service.list_documents.return_value = []
        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            # key1 用 2 次（耗尽配额）
            for _ in range(2):
                r = c.get(
                    "/api/v1/documents",
                    headers={"Authorization": "Bearer key1"},
                )
                assert r.status_code == 200
            # key1 第 3 次：429
            r = c.get(
                "/api/v1/documents",
                headers={"Authorization": "Bearer key1"},
            )
            assert r.status_code == 429

            # key2 仍可用（独立配额）
            r = c.get(
                "/api/v1/documents",
                headers={"Authorization": "Bearer key2"},
            )
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# 集成测试：按 IP 限流（认证禁用场景）
# ---------------------------------------------------------------------------


class TestRateLimitByIp:
    """认证禁用时按客户端 IP 限流。"""

    def test_same_ip_shares_quota(
        self,
        mock_service: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同一 IP 连续请求共享配额，超频触发 429。"""

        monkeypatch.setenv("QDRANT_ENABLED", "false")
        monkeypatch.setenv("RERANKER_ENABLED", "false")
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")

        mock_service.list_documents.return_value = []
        reset_rate_limit_storage()
        app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
        app.dependency_overrides[get_document_service] = lambda: mock_service

        with TestClient(app) as c:
            # TestClient 默认 client.host = "testclient"（同一 IP）
            r1 = c.get("/api/v1/documents")
            r2 = c.get("/api/v1/documents")
            r3 = c.get("/api/v1/documents")

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429
