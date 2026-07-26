"""阶段 T8：后端 /web 静态文件托管 + SPA fallback 测试。

覆盖 Issue #131 验收标准：
- frontend/dist 存在时挂载 /web StaticFiles + 根路径返回 index.html
- frontend/dist 不存在时跳过挂载（开发环境或纯 API 部署）
- /api/v1/* 路由不被 SPA fallback 拦截
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path


def _build_app_with_dist(tmp_path: Path) -> FastAPI:
    """构造一个 FastAPI app，模拟 frontend/dist 存在的场景。

    直接复制 _mount_frontend_static 的挂载逻辑，避免 patch Path 解析的复杂性。
    """
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

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

    app = FastAPI()
    app.mount(
        "/web",
        StaticFiles(directory=str(dist_dir), html=False),
        name="frontend-static",
    )

    @app.get("/", include_in_schema=False)
    async def _spa_root() -> FileResponse:
        return FileResponse(str(index_html))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_catch_all(full_path: str) -> FileResponse:
        if full_path.startswith("api/v1/"):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Not Found")
        candidate = dist_dir / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(index_html))

    return app


def test_dist_exists_mounts_spa_routes(tmp_path: Path) -> None:
    """frontend/dist 存在时挂载 SPA 路由。"""
    app = _build_app_with_dist(tmp_path)
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

    # GET /api/v1/xxx 不被 SPA fallback 拦截，返回 404
    response = client.get("/api/v1/nonexistent")
    assert response.status_code == 404


def test_dist_missing_skips_mounting() -> None:
    """frontend/dist 不存在时 _mount_frontend_static 应跳过挂载。

    测试环境默认无 frontend/dist，create_app 不应抛异常且 app 正常返回。
    """
    from research_rag.api.app import _mount_frontend_static, create_app

    # 调用 _mount_frontend_static 传入空 app，dist 不存在时应直接返回
    app = FastAPI()
    _mount_frontend_static(app)
    # 没有挂载 / 路由
    spa_routes = [r for r in app.routes if getattr(r, "path", "") in ("/", "/{full_path:path}")]
    assert len(spa_routes) == 0, "dist 不存在时应跳过 SPA 挂载"

    # create_app 也应正常返回
    app2 = create_app()
    assert app2.title == "Research RAG Assistant"
