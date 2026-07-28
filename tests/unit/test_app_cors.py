"""FastAPI CORS behavior for development and same-origin production."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from research_rag.api.app import create_app

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/v1/documents",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


def test_default_development_cors_allows_vite_origin() -> None:
    """The default app supports the Vite development server."""
    response = _preflight(TestClient(create_app()), "http://localhost:5173")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_empty_cors_origins_disables_cross_origin_access() -> None:
    """Production can explicitly disable CORS for same-origin deployment."""
    response = _preflight(
        TestClient(create_app(cors_origins=[])),
        "http://localhost:5173",
    )

    assert "access-control-allow-origin" not in response.headers


def test_production_environment_disables_development_cors(
    monkeypatch: MonkeyPatch,
) -> None:
    """The production entry point is same-origin without caller configuration."""
    monkeypatch.setenv("APP_ENV", "production")

    response = _preflight(TestClient(create_app()), "http://localhost:5173")

    assert "access-control-allow-origin" not in response.headers
