"""会话管理 HTTP 路由：创建 / 列表 / 详情 / 删除（阶段 9.2 多轮对话）。

依据 PROJECT_PLAN.md 第 8 节 API 草案、阶段 9.2 多轮对话设计、
第 13.6 节（异常由 API 层映射为 HTTP 状态码）。

设计取舍（初学者向说明）：
- **路由前缀 ``/api/v1/conversations``**：对齐 ``/api/v1/documents`` 风格，
  为后续版本演进留余地。
- **复用 ``QaService`` 而非新建 ``ConversationService``**：会话管理与问答
  强耦合（问答时持久化消息、会话历史注入问答 prompt），放同一 service 层
  避免拆分过细（项目硬约束"不大量创建空模块"）。会话 CRUD 方法直接挂在
  ``QaService`` 上（``create_conversation`` / ``get_conversation`` /
  ``list_conversations`` / ``delete_conversation`` / ``list_messages``）。
- **路由只做编排，不写业务逻辑**：每个端点只做三件事——① 解析请求参数，
  ② 调 ``QaService`` 会话管理方法，③ 把 ORM ``Conversation`` / ``Message``
  转成 schema。事务边界、级联删除、标题自动设置等都由 service 层负责。
- **ORM → schema 转换用 ``model_validate``**：``ConversationRead`` /
  ``MessageRead`` 借助 ``from_attributes=True`` 直接读 ORM 属性。
- **POST 返回 201**：资源创建用 ``201 Created``（与 documents 路由一致）。
- **DELETE 返回 204**：删除成功无响应体（REST 惯例）。
- **详情接口返回完整消息列表**：列表接口为节省体积不返回消息
  （``ConversationList.items`` 的 ``messages`` 字段为 ``None``），详情接口
  返回完整消息便于前端切换会话时回看历史。
- **异常不在路由里捕获**：``ConversationNotFoundError`` 由 ``app.py`` 的
  全局异常处理器统一映射为 404，路由代码保持线性。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from research_rag.api.dependencies import get_db, get_qa_service
from research_rag.api.schemas import (
    ConversationCreate,
    ConversationList,
    ConversationRead,
    MessageRead,
)

if TYPE_CHECKING:
    from research_rag.db.models import Conversation, Message
    from research_rag.services.qa_service import QaService

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate = Body(...),
    service: QaService = Depends(get_qa_service),
    session: Session = Depends(get_db),
) -> Conversation:
    """创建会话。

    可选 ``title`` 和 ``document_ids``（会话级文档范围锁定）。``document_ids``
    为 ``None`` 或空列表表示查询全库 READY 文档。
    """

    conv = service.create_conversation(
        title=payload.title,
        document_ids=payload.document_ids,
    )
    # 显式 commit：service 层只 flush 不 commit，事务边界由路由控制
    # （与 feedback / document 路由风格一致，否则下个请求 GET 详情会 404）
    session.commit()
    # 新建会话无消息，messages 字段为 None（列表场景）
    return conv


@router.get("", response_model=ConversationList)
def list_conversations(service: QaService = Depends(get_qa_service)) -> ConversationList:
    """返回所有会话（按 ``updated_at`` 降序，最近活跃在前）。

    列表接口不返回 ``messages``（节省体积），需要消息列表请用详情接口
    ``GET /api/v1/conversations/{conversation_id}``。
    """

    convs = service.list_conversations()
    # 列表场景：messages 字段为 None（schema 默认 None，model_validate 不会自动加载
    # relationship，需显式置 None 避免触发懒加载）
    items = [
        ConversationRead(
            id=c.id,
            title=c.title,
            document_ids=c.document_ids,
            created_at=c.created_at,
            updated_at=c.updated_at,
            messages=None,
        )
        for c in convs
    ]
    return ConversationList(items=items)


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation(
    conversation_id: uuid.UUID,
    service: QaService = Depends(get_qa_service),
) -> Conversation:
    """按 ID 查询会话详情（含完整消息列表）。

    不存在抛 ``ConversationNotFoundError``（→ 404）。消息按 ``created_at``
    升序返回（对话时间顺序）。
    """

    conv = service.get_conversation(conversation_id)
    msgs = service.list_messages(conversation_id)
    # 把消息列表挂到会话上，让 ConversationRead.model_validate 能读到
    # （ORM relationship 也能读到，但显式传入避免依赖懒加载行为）
    conv.messages = msgs
    return conv


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: uuid.UUID,
    service: QaService = Depends(get_qa_service),
    session: Session = Depends(get_db),
) -> None:
    """删除会话（级联删除其消息）。不存在抛 ``ConversationNotFoundError``（→ 404）。"""

    service.delete_conversation(conversation_id)
    session.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageRead])
def list_messages(
    conversation_id: uuid.UUID,
    service: QaService = Depends(get_qa_service),
) -> list[Message]:
    """列出会话内消息（按 ``created_at`` 升序）。

    独立端点便于前端在不重新拉取整个会话详情的情况下刷新消息列表。会话不存在
    抛 ``ConversationNotFoundError``（→ 404）。
    """

    return service.list_messages(conversation_id)
