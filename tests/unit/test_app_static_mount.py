"""阶段 T8：后端 /web 静态文件托管 + SPA fallback 测试。

覆盖 Issue #131 验收标准：
- frontend/dist 存在时挂载 /web StaticFiles + 根路径返回 index.html
- frontend/dist 不存在时跳过挂载（开发环境或纯 API 部署）
- /api/v1/* 路由不被 SPA fallback 拦截
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path


def _build_dist(tmp_path: Path) -> Path:
    """创建最小可用的前端构建产物。"""
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    index_html = dist_dir / "index.html"
    index_html.write_text(
        "<!doctype html><html><head><title>Test</title></head>"
        '<body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "main.js").write_text("console.log('test');", encoding="utf-8")

    return dist_dir


def test_dist_exists_mounts_spa_routes(tmp_path: Path) -> None:
    """create_app 通过真实生产挂载逻辑提供 SPA 与静态资源。"""
    from research_rag.api.app import create_app

    app = create_app(frontend_dist_dir=_build_dist(tmp_path))
    client = TestClient(app)

    # GET / 返回 index.html
    response = client.get("/")
    assert response.status_code == 200
    assert "Test" in response.text

    # GET /assets/main.js 返回静态文件
    response = client.get("/assets/main.js")
    assert response.status_code == 200
    assert "console.log" in response.text

    # GET /some-spa-route 返回 index.html（SPA fallback）
    response = client.get("/some-spa-route")
    assert response.status_code == 200
    assert "Test" in response.text

    # GET /web 下的 client-side route 也必须 fallback 到 index.html
    response = client.get("/web/some-spa-route")
    assert response.status_code == 200
    assert "Test" in response.text

    # GET /api/v1/xxx 不被 SPA fallback 拦截，返回 404
    response = client.get("/api/v1/nonexistent")
    assert response.status_code == 404


def test_dist_missing_skips_mounting(tmp_path: Path) -> None:
    """frontend/dist 不存在时 create_app 保持纯 API 模式。"""
    from research_rag.api.app import create_app

    app = create_app(frontend_dist_dir=tmp_path / "missing")
    client = TestClient(app)

    assert client.get("/").status_code == 404
    assert app.title == "Research RAG Assistant"
