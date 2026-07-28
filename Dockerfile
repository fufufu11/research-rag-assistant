# 阶段 11.4 Docker Compose 一键部署
#
# Node 构建 React SPA；Python 3.11-slim + uv 运行 API 并同源托管 dist。
# 默认装 embedding + chinese extra（生产需本地 Embedding 推理 + 中文分词）。
#
# 构建：docker compose build  或  docker build -t rrag-api .
# 运行：见 docker-compose.yml

FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim-bookworm

# 安装 uv（官方推荐多阶段复制，无需 curl 安装）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 系统依赖：
# - libgomp1：torch CPU 推理（sentence-transformers）需要
# - curl：容器健康检查（HEALTHCHECK）用
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖清单（利用 Docker 层缓存：源码改动不触发依赖重装）
COPY pyproject.toml uv.lock ./

# 安装依赖到项目 .venv（含 embedding + chinese extra，生产需本地推理 + 中文分词）
# --frozen：严格按 lock 文件，不更新
# --no-install-project：只装依赖，不装项目自身（项目代码后续复制）
ENV UV_COMPILE_BYTECODE=1
RUN uv sync --frozen --no-install-project --extra embedding --extra chinese

# 复制应用源码与迁移脚本
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# 创建数据目录（SQLite fallback + 上传文件落盘）
RUN mkdir -p /app/data/uploads

# 暴露 API 端口
EXPOSE 8000

# 健康检查（API 文档列表端点，200 视为健康）
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/documents || exit 1

# entrypoint：先 alembic migrate 再启动 uvicorn
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 创建非 root 用户并 chown /app（阶段 11.6 切片 B #98）
# - groupadd/useradd：创建 UID 65532 的 app 用户（distroless 标准，不与宿主机普通用户冲突）
# - chown -R app:app /app：让非 root 用户可读 .venv/源码 + 可写 data/uploads
#   必须在所有 COPY 之后执行，确保 entrypoint.sh 也被 chown
RUN groupadd -r app && useradd -r -g app -u 65532 app && chown -R app:app /app

# 以非 root 用户运行
USER 65532

ENTRYPOINT ["/app/entrypoint.sh"]
