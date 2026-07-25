#!/bin/sh
# 阶段 11.4 容器入口脚本：先迁移数据库，再启动 API 服务。
# 用 sh 而非 bash：python:3.11-slim 默认不含 bash，sh 在所有 Debian 镜像可用。
set -e

echo "[entrypoint] Running database migrations (alembic upgrade head)..."
uv run alembic upgrade head

echo "[entrypoint] Starting API server on 0.0.0.0:8000..."
exec uv run uvicorn research_rag.api.app:create_app --factory --host 0.0.0.0 --port 8000
