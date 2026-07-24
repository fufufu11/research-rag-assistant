"""用户反馈 HTTP 路由：创建/更新 / 撤销 / 查询（阶段 10.2 用户反馈闭环）。

依据 Issue #59 验收标准、ADR 0001（``request_id`` 作为主关联键）。

设计取舍（初学者向说明）：
- **路由前缀 ``/api/v1/feedback``**：对齐 ``/api/v1/documents`` /
  ``/api/v1/conversations`` 风格，为后续版本演进留余地。
- **路由直接调 ``FeedbackRepository``，不新建 ``FeedbackService``**：反馈逻辑
  仅是薄 CRUD（upsert / delete / list），无复杂业务编排。挂 service 会成为空
  模块（违反项目硬约束"不大量创建空模块"）。事务边界由路由层显式控制
  （``session.commit()``），与 ``DocumentService`` 风格一致。
- **Upsert 语义**：``POST /api/v1/feedback`` 不存在则创建（201），存在则更新
  rating/comment（200）。``request_id`` 唯一约束兼作匿名防刷（详见 ADR 0001）。
  like↔dislike 切换通过 POST 同一 ``request_id`` 实现，无需 PATCH 端点。
- **DELETE 撤销**：独立端点 ``DELETE /api/v1/feedback/{request_id}``，不存在
  抛 ``FeedbackNotFoundError``（→ 404），成功 204（REST 惯例）。
- **GET 单条 + 列表**：``GET /api/v1/feedback/{request_id}`` 查单条；
  ``GET /api/v1/feedback?rating=&conversation_id=&limit=`` 列表筛选。
  ``rating`` 用 ``FeedbackRating`` 枚举注解，FastAPI 自动校验非法值返回 422。
- **异常不在路由里捕获**：``FeedbackNotFoundError`` 由 ``app.py`` 全局异常处理器
  统一映射为 404，路由代码保持线性。
- **ORM → schema 转换用 ``model_validate``**：``FeedbackRead.model_validate(fb)``
  借助 ``from_attributes=True`` 直接读 ORM 属性。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends, Query, Response, status

from research_rag.api.dependencies import get_db
from research_rag.api.schemas import FeedbackCreate, FeedbackList, FeedbackRead
from research_rag.db.models import FeedbackNotFoundError, FeedbackRating
from research_rag.db.repositories import FeedbackRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackRead)
def upsert_feedback(
    response: Response,
    payload: FeedbackCreate = Body(...),
    session: Session = Depends(get_db),
) -> FeedbackRead:
    """提交反馈（Upsert 语义）。

    - ``request_id`` 不存在：创建反馈记录，返回 201 Created。
    - ``request_id`` 已存在：更新 rating/message_id/comment，返回 200 OK。
      ``updated_at`` 由 ORM ``onupdate`` 自动维护。

    ``request_id`` 关联到问答 API 返回的 ``QueryResponse.request_id``（详见
    ADR 0001）。匿名场景下唯一约束兼作防刷。
    """

    repo = FeedbackRepository(session)
    existed = repo.get_by_request_id(payload.request_id)
    fb = repo.upsert(
        request_id=payload.request_id,
        rating=payload.rating,
        message_id=payload.message_id,
        comment=payload.comment,
    )
    session.commit()
    # commit 后 ORM 对象属性仍可用（expire_on_commit=False），无需 refresh
    response.status_code = status.HTTP_200_OK if existed is not None else status.HTTP_201_CREATED
    return FeedbackRead.model_validate(fb)


@router.get("/{request_id}", response_model=FeedbackRead)
def get_feedback(
    request_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> FeedbackRead:
    """按 ``request_id`` 查询单条反馈。不存在抛 ``FeedbackNotFoundError``（→ 404）。"""

    repo = FeedbackRepository(session)
    fb = repo.get_by_request_id(request_id)
    if fb is None:
        msg = f"反馈不存在：{request_id}"
        raise FeedbackNotFoundError(msg)
    return FeedbackRead.model_validate(fb)


@router.get("", response_model=FeedbackList)
def list_feedback(
    rating: FeedbackRating | None = Query(
        default=None, description="按反馈类型筛选（like/dislike）"
    ),
    conversation_id: uuid.UUID | None = Query(
        default=None, description="按会话筛选（join messages.conversation_id）"
    ),
    limit: int | None = Query(default=None, ge=1, description="最多返回条数"),
    session: Session = Depends(get_db),
) -> FeedbackList:
    """查询反馈列表，支持按 rating / conversation_id / limit 筛选。

    按 ``created_at`` 降序（最新的在前）。``conversation_id`` 筛选走
    ``message_id → messages.conversation_id`` join，单轮问答反馈
    （``message_id=None``）不会出现在按会话筛选结果中。
    """

    repo = FeedbackRepository(session)
    items = repo.list(rating=rating, conversation_id=conversation_id, limit=limit)
    return FeedbackList(items=[FeedbackRead.model_validate(f) for f in items])


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feedback(
    request_id: uuid.UUID,
    session: Session = Depends(get_db),
) -> None:
    """撤销反馈。不存在抛 ``FeedbackNotFoundError``（→ 404），成功 204。"""

    repo = FeedbackRepository(session)
    fb = repo.get_by_request_id(request_id)
    if fb is None:
        msg = f"反馈不存在：{request_id}"
        raise FeedbackNotFoundError(msg)
    repo.delete(fb)
    session.commit()
