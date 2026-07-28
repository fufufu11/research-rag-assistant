# Frontend Dockerfile：独立构建 React SPA 静态文件。
# 生产应用镜像由仓库根 Dockerfile 直接构建并内置 dist；本文件保留用于
# 单独验证或导出前端产物。
#
# 设计取舍（ADR 0005）：
# - 用 node:20-alpine 构建，输出 dist 到 /app/dist
# - 不在 frontend 镜像里跑 nginx，最终 dist 由后端 FastAPI StaticFiles 托管
# - 多阶段：构建阶段用 node，产出阶段不需要任何运行时（dist 是静态文件）

# === 阶段 1：构建 ===
FROM node:20-alpine AS builder

WORKDIR /app

# 先复制 package.json + package-lock.json，利用 docker 层缓存
COPY package.json package-lock.json* ./
RUN npm ci

# 复制源码并构建
COPY . .
RUN npm run build

# === 阶段 2：导出 dist ===
# 使用 scratch 不可行（需要目录结构），用 alpine + 仅保留 dist
FROM alpine:3.20 AS dist-export

COPY --from=builder /app/dist /dist

# 标注镜像元数据
LABEL org.opencontainers.image.title="research-rag-frontend-dist"
LABEL org.opencontainers.image.description="前端 React SPA 静态文件（dist），由后端 FastAPI 托管"
LABEL org.opencontainers.image.source="https://github.com/fufufu11/research-rag-assistant"
