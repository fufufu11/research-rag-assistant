"""API Key 认证鉴权单元测试（阶段 11.1，Issue #74）。

测试覆盖：
- ``is_auth_enabled`` / ``_get_valid_api_keys`` 环境变量解析（大小写、空白、多 key）
- ``verify_api_key`` 依赖：禁用放行 / 启用+无凭证 401 / 启用+无效 401 /
  启用+有效通过 / 启用+空 key 集合 401 / 启用+多 key 任一命中 / 启用+错误 scheme 401
- 集成测试（``TestClient`` + mock ``DocumentService``）：禁用可访问 /
  启用 401 / 启用+无效 401 / 启用+有效 200 / 启用+空 key 集合 401 / 启用+多 key 任一 200

测试策略：
- 纯单元测试：用 ``monkeypatch.setenv`` / ``delenv`` 控制 ``API_KEY_ENABLED`` 与
  ``API_KEYS``，直接调用 ``verify_api_key``，构造 ``HTTPAuthorizationCredentials``。
- 集成测试：``create_app`` + 内存 SQLite factory + ``dependency_overrides`` 替换
  ``get_document_service`` 为 ``MagicMock``，用 ``TestClient`` 发真实 HTTP 请求，
  验证认证依赖对所有 ``/api/v1/*`` 端点生效。``verify_api_key`` 是请求级依赖，
  每次请求读环境变量，``monkeypatch`` 在测试函数内设置即可生效。

注：原 ``ApiClient`` 携带 API Key 的测试已随 Streamlit UI 层删除移除（ADR 0005），
前端 ``ApiClient`` 的等价测试在 ``frontend/src/api/client.test.ts``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from research_rag.api.app import create_app
from research_rag.api.auth import _get_valid_api_keys, is_auth_enabled, verify_api_key
from research_rag.api.dependencies import get_document_service
from research_rag.db.session import create_session_factory
from research_rag.services.document_service import DocumentService

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi import FastAPI


# ---------------------------------------------------------------------------
# 纯单元测试：is_auth_enabled
# ---------------------------------------------------------------------------


class TestIsAuthEnabled:
    """``API_KEY_ENABLED`` 环境变量解析。"""

    def test_disabled_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        assert is_auth_enabled() is False

    def test_disabled_when_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY_ENABLED", "false")
        assert is_auth_enabled() is False

    def test_enabled_when_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY_ENABLED", "true")
        assert is_auth_enabled() is True

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY_ENABLED", "TRUE")
        assert is_auth_enabled() is True

    def test_disabled_when_arbitrary_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 true 值（如 yes/1/on）均视为禁用，避免歧义。"""

        monkeypatch.setenv("API_KEY_ENABLED", "yes")
        assert is_auth_enabled() is False


# ---------------------------------------------------------------------------
# 纯单元测试：_get_valid_api_keys
# ---------------------------------------------------------------------------


class TestGetValidApiKeys:
    """``API_KEYS`` 环境变量解析（逗号分隔 + 空白去除）。"""

    @pytest.fixture(autouse=True)
    def _clear_api_keys_file_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """清理 ``API_KEYS_FILE`` 后缀变量（阶段 11.6 切片 C：docker secrets 支持）。"""

        monkeypatch.delenv("API_KEYS_FILE", raising=False)

    def test_single_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEYS", "key1")
        assert _get_valid_api_keys() == {"key1"}

    def test_multiple_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEYS", "key1,key2,key3")
        assert _get_valid_api_keys() == {"key1", "key2", "key3"}

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEYS", " key1 , key2 , key3 ")
        assert _get_valid_api_keys() == {"key1", "key2", "key3"}

    def test_empty_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("API_KEYS", raising=False)
        assert _get_valid_api_keys() == set()

    def test_empty_when_only_separators(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEYS", " , , ")
        assert _get_valid_api_keys() == set()

    def test_reads_from_file_when_file_var_set(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``API_KEYS_FILE`` 指向文件时优先读文件内容（逗号分隔多 key）。

        阶段 11.6 切片 C：docker secrets 通过 ``_FILE`` 后缀挂载密钥文件，
        文件内容支持逗号分隔多 key（与环境变量格式一致）。
        """

        keys_file = tmp_path / "api_keys.txt"
        keys_file.write_text("key-from-file-1,key-from-file-2\n", encoding="utf-8")

        monkeypatch.setenv("API_KEYS_FILE", str(keys_file))
        # 即使环境变量也设置了，_FILE 优先
        monkeypatch.setenv("API_KEYS", "env-key-should-be-ignored")

        assert _get_valid_api_keys() == {"key-from-file-1", "key-from-file-2"}

    def test_single_key_from_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """``API_KEYS_FILE`` 文件只含一个 key 时返回单元素集合。"""

        keys_file = tmp_path / "api_keys.txt"
        keys_file.write_text("single-key\n", encoding="utf-8")

        monkeypatch.setenv("API_KEYS_FILE", str(keys_file))
        assert _get_valid_api_keys() == {"single-key"}

    def test_falls_back_to_env_when_file_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """``API_KEYS_FILE`` 指向不存在文件时 fallback ``API_KEYS`` 环境变量。"""

        nonexistent = tmp_path / "does-not-exist.txt"
        monkeypatch.setenv("API_KEYS_FILE", str(nonexistent))
        monkeypatch.setenv("API_KEYS", "env-fallback-key")

        assert _get_valid_api_keys() == {"env-fallback-key"}


# ---------------------------------------------------------------------------
# 纯单元测试：verify_api_key
# ---------------------------------------------------------------------------


def _creds(token: str, scheme: str = "Bearer") -> HTTPAuthorizationCredentials:
    """构造 ``HTTPAuthorizationCredentials`` 辅助函数。"""

    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)


class TestVerifyApiKey:
    """``verify_api_key`` 依赖函数的各分支。"""

    def test_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("API_KEY_ENABLED", raising=False)
        assert verify_api_key(credentials=None) is None

    def test_disabled_ignores_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """禁用认证时即使携带凭证也直接放行。"""

        monkeypatch.setenv("API_KEY_ENABLED", "false")
        assert verify_api_key(credentials=_creds("anything")) is None

    def test_enabled_no_keys_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """启用认证但 API_KEYS 为空：安全失败，全部 401。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "")
        with pytest.raises(HTTPException) as exc:
            verify_api_key(credentials=_creds("key1"))
        assert exc.value.status_code == 401
        assert "未配置" in exc.value.detail

    def test_enabled_no_credentials_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "key1")
        with pytest.raises(HTTPException) as exc:
            verify_api_key(credentials=None)
        assert exc.value.status_code == 401
        assert "未提供" in exc.value.detail

    def test_enabled_invalid_token_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "key1")
        with pytest.raises(HTTPException) as exc:
            verify_api_key(credentials=_creds("wrong"))
        assert exc.value.status_code == 401
        assert "无效" in exc.value.detail

    def test_enabled_valid_token_returns_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "key1")
        assert verify_api_key(credentials=_creds("key1")) == "key1"

    def test_enabled_multiple_keys_any_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """多 key 场景：任一命中即通过。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "key1,key2,key3")
        assert verify_api_key(credentials=_creds("key2")) == "key2"

    def test_enabled_wrong_scheme_returns_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 Bearer scheme（如 Basic）返回 401。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "key1")
        with pytest.raises(HTTPException) as exc:
            verify_api_key(credentials=_creds("key1", scheme="Basic"))
        assert exc.value.status_code == 401

    def test_www_authenticate_header_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """401 响应携带 WWW-Authenticate: Bearer 头，符合 RFC 7235。"""

        monkeypatch.setenv("API_KEY_ENABLED", "true")
        monkeypatch.setenv("API_KEYS", "key1")
        with pytest.raises(HTTPException) as exc:
            verify_api_key(credentials=None)
        assert exc.value.headers == {"WWW-Authenticate": "Bearer"}


# ---------------------------------------------------------------------------
# 集成测试：TestClient + mock DocumentService
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_service() -> MagicMock:
    """``MagicMock(spec=DocumentService)``：限定只能调 DocumentService 的方法。"""

    return MagicMock(spec=DocumentService)


@pytest.fixture
def app(mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """创建应用实例：内存 SQLite factory + override service 依赖。

    默认禁用认证（``API_KEY_ENABLED`` 未设），与现有测试风格一致。启用认证的
    测试在测试函数内用 ``monkeypatch.setenv`` 设置。
    """

    # 关闭 Qdrant/Reranker 避免 lifespan 尝试加载真实组件
    monkeypatch.setenv("QDRANT_ENABLED", "false")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.delenv("API_KEY_ENABLED", raising=False)

    app = create_app(session_factory=create_session_factory("sqlite:///:memory:"))
    app.dependency_overrides[get_document_service] = lambda: mock_service
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """``TestClient``：用 ``with`` 触发 lifespan。"""

    with TestClient(app) as c:
        yield c


def _enable_auth(monkeypatch: pytest.MonkeyPatch, keys: str) -> None:
    """启用认证并配置 API_KEYS 的辅助函数。"""

    monkeypatch.setenv("API_KEY_ENABLED", "true")
    monkeypatch.setenv("API_KEYS", keys)


def test_disabled_auth_allows_access_without_credentials(
    client: TestClient, mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """禁用认证时，无 Authorization 头也能正常访问端点（向后兼容）。"""

    monkeypatch.delenv("API_KEY_ENABLED", raising=False)
    mock_service.list_documents.return_value = []

    response = client.get("/api/v1/documents")

    assert response.status_code == 200


def test_enabled_auth_no_credentials_returns_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启用认证后，无 Authorization 头返回 401。"""

    _enable_auth(monkeypatch, "secret-key")

    response = client.get("/api/v1/documents")

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_enabled_auth_invalid_key_returns_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启用认证后，无效 key 返回 401。"""

    _enable_auth(monkeypatch, "secret-key")

    response = client.get(
        "/api/v1/documents",
        headers={"Authorization": "Bearer wrong-key"},
    )

    assert response.status_code == 401


def test_enabled_auth_valid_key_allows_access(
    client: TestClient, mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启用认证后，有效 key 正常访问端点。"""

    _enable_auth(monkeypatch, "secret-key")
    mock_service.list_documents.return_value = []

    response = client.get(
        "/api/v1/documents",
        headers={"Authorization": "Bearer secret-key"},
    )

    assert response.status_code == 200


def test_enabled_auth_empty_keys_returns_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启用认证但 API_KEYS 为空：安全失败，即使带凭证也 401。"""

    _enable_auth(monkeypatch, "")

    response = client.get(
        "/api/v1/documents",
        headers={"Authorization": "Bearer any-key"},
    )

    assert response.status_code == 401


def test_enabled_auth_multiple_keys_any_works(
    client: TestClient, mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """多 key 场景：用其中一个 key 能正常访问。"""

    _enable_auth(monkeypatch, "key1,key2,key3")
    mock_service.list_documents.return_value = []

    response = client.get(
        "/api/v1/documents",
        headers={"Authorization": "Bearer key2"},
    )

    assert response.status_code == 200


def test_auth_applies_to_all_routers(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """认证对所有 /api/v1/* 端点生效（documents/queries/conversations/feedback）。"""

    _enable_auth(monkeypatch, "secret-key")

    # 不带凭证访问不同路由，都应返回 401（而非 404/422/500）
    endpoints = [
        ("GET", "/api/v1/documents"),
        ("GET", "/api/v1/conversations"),
        ("GET", "/api/v1/feedback"),
        ("POST", "/api/v1/queries"),
    ]
    for method, path in endpoints:
        response = client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} 应返回 401，实际 {response.status_code}"
        )


def test_disabled_auth_applies_to_all_routers(
    client: TestClient, mock_service: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """禁用认证时所有端点可访问（不被认证拦截，返回各自的业务状态码）。"""

    monkeypatch.delenv("API_KEY_ENABLED", raising=False)
    mock_service.list_documents.return_value = []

    # GET 端点应返回 200（非 401）
    response = client.get("/api/v1/documents")
    assert response.status_code == 200
