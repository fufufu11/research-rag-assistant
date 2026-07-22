"""文档管理 API 的 Pydantic schema。

依据 PROJECT_PLAN.md 第 8.2 节（上传文档响应）、第 8.5 节（错误响应）、
第 7.1 节（Document 字段）。

设计取舍（初学者向说明）：
- **Pydantic schema 与 ORM 模型分离**：``DocumentRead`` 不直接复用
  ``db.models.Document``，而是单独定义。理由：① API 响应字段可能与 ORM 字段
  不完全一致（未来可能加 ``chunk_count`` 等聚合字段）；② 避免把 ORM 内部
  实现（如 ``relationship``）暴露给 API；③ 符合 PROJECT_PLAN 第 10 节分层。
- **``from_attributes=True``**：Pydantic v2 配置，让
  ``DocumentRead.model_validate(orm_doc)`` 能直接从 ORM 对象的属性读值
  （``orm_doc.id`` / ``orm_doc.status`` 等），无需手写转换函数。
- **``status`` 用 ``DocumentStatus`` 枚举**：Pydantic v2 序列化时自动用枚举值
  （``"ready"`` 而非 ``"READY"``），与数据库存储和 API 草案一致，便于前端
  解析和 SQL 排查。
- **``ErrorResponse`` 统一错误格式**：所有异常处理器返回 ``{"detail": "..."}``，
  与 FastAPI 默认 ``HTTPException`` 格式一致，前端只需一种解析逻辑
  （PROJECT_PLAN 第 8.5 节示意 ``{"error": {...}}``，本 Issue 采用 FastAPI
  惯例的 ``{"detail": "..."}``，更贴合框架生态，减少自定义中间件）。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from research_rag.db.models import DocumentStatus


class DocumentRead(BaseModel):
    """文档详情响应（PROJECT_PLAN.md 第 8.2 节、第 7.1 节）。

    字段与 ``Document`` ORM 模型一一对应，``from_attributes=True`` 让
    ``model_validate(orm_doc)`` 直接读 ORM 属性。``id`` 用 ``uuid.UUID`` 类型，
    FastAPI 序列化时自动转为字符串（与第 8.2 节示例 ``"id": "document-uuid"``
    一致），同时保留类型安全。
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    stored_name: str
    sha256: str
    page_count: int
    status: DocumentStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    """文档列表响应：包裹 ``items`` 数组，便于后续加分页字段。"""

    items: list[DocumentRead]


class ErrorResponse(BaseModel):
    """统一错误响应（PROJECT_PLAN.md 第 8.5 节）。"""

    detail: str
