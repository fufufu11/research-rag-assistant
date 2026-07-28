"""问答 HTTP 路由：POST /api/v1/queries。

依据 PROJECT_PLAN.md 第 8.4 节（文档问答）、US-003、第 13.6 节（异常由
API 层映射为 HTTP 状态码）、阶段 9.1（流式 SSE）。

设计取舍（初学者向说明）：
- **路由只做编排，不写业务逻辑**：本端点只做三件事——① 解析 ``QueryRequest``，
  ② 调 ``QaService.answer`` / ``answer_stream``，③ 返回 ``QueryResponse`` 或
  ``StreamingResponse``。文档查询、向量检索、LLM 调用、引用映射等都由 service
  层负责（PROJECT_PLAN 第 13.6 节"业务服务不直接拼接 HTTP 响应"，反向也成立：
  API 层不做业务）。
- **POST 返回 200**：问答是"执行查询并返回结果"而非"创建资源"，用 200 而非
  201（REST 惯例：201 用于创建新资源，如 POST /documents 创建文档记录）。
- **异常不在路由里捕获**：``InsufficientEvidenceError`` / ``LlmServiceError``
  / ``EmbeddingServiceError`` / ``VectorStoreError`` / ``DocumentNotFoundError``
  / ``NoAvailableDocumentsError`` 由 ``app.py`` 的全局异常处理器统一映射为
  HTTP 状态码，路由代码保持线性。
- **``QueryRequest`` 作为请求体**：FastAPI 自动校验 JSON 请求体，``question``
  为空或缺失时返回 422，``document_ids`` 中的 UUID 格式错误也返回 422。
- **流式路径用 SSE（阶段 9.1）**：``stream=true`` 时返回
  ``StreamingResponse(media_type="text/event-stream")``，事件类型
  ``token`` / ``done`` / ``error``。流式下业务异常由 service 层转为
  ``StreamErrorEvent``（SSE 已开始则无法改 HTTP 状态码），非流式仍由全局
  处理器映射。返回 ``StreamingResponse`` 时 FastAPI 跳过 ``response_model``
  序列化（Response 子类直接返回）。
- **Prompt 注入过滤在路由层完成**（阶段 11.2，Issue #76）：调 service 前用
  ``validate_question`` 检测常见注入模式（``ignore previous`` / ``system:`` 等），
  命中即返回 400，避免注入指令进入 LLM。``INPUT_VALIDATION_ENABLED=false`` 时
  跳过。校验在 ``stream`` 分支之前完成，确保流式 / 非流式路径都被覆盖。
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Body, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from research_rag.api.dependencies import get_db, get_qa_service
from research_rag.api.schemas import QueryRequest, QueryResponse
from research_rag.api.security import validate_question
from research_rag.services.qa_service import (
    QaService,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamTokenEvent,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from research_rag.services.qa_service import StreamEvent

router = APIRouter(prefix="/api/v1/queries", tags=["queries"])


@router.post("", response_model=QueryResponse, status_code=status.HTTP_200_OK)
def create_query(
    query_request: QueryRequest = Body(...),
    service: QaService = Depends(get_qa_service),
    session: Session = Depends(get_db),
) -> QueryResponse | StreamingResponse:
    """提交问答请求。

    接收 ``QueryRequest``（问题 + 可选文档 ID 过滤 + 可选 top_k + stream）。

    - ``stream=false``（默认）：调 ``QaService.answer`` 返回 ``QueryResponse``
      （答案 + 引用列表 + request_id + 耗时）。
    - ``stream=true``：返回 ``StreamingResponse``（SSE），逐字推送 LLM 生成
      内容，流结束后发 ``done`` 事件携带引用元数据。

    非流式可能的异常（由全局处理器映射为 HTTP 状态码）：
    - ``DocumentNotFoundError`` → 404（指定的 document_ids 中有不存在的 UUID）
    - ``NoAvailableDocumentsError`` → 404（无可用 READY 文档）
    - ``InsufficientEvidenceError`` → 422（上下文证据不足以回答）
    - ``LlmServiceError`` → 503（LLM 服务不可用）
    - ``EmbeddingServiceError`` → 503（Embedding 服务不可用）
    - ``VectorStoreError`` → 500（向量索引或检索失败）

    流式路径下上述异常转为 SSE ``error`` 事件（HTTP 仍为 200，错误详情在事件
    ``data`` 中），前端据此展示错误。

    阶段 11.2 输入校验（Issue #76）：调 service 前先做 Prompt 注入过滤，
    命中常见注入模式（``ignore previous`` / ``system:`` 等）返回 400。
    ``INPUT_VALIDATION_ENABLED=false`` 时跳过。校验在 stream 分支前完成，
    流式 / 非流式路径都被覆盖。
    """

    # 阶段 11.2：Prompt 注入过滤，命中即 400（在 stream 分支前，覆盖两条路径）
    validate_question(query_request.question)

    if query_request.stream:
        return StreamingResponse(
            _stream_answer(
                service,
                question=query_request.question,
                document_ids=query_request.document_ids,
                top_k=query_request.top_k,
                conversation_id=query_request.conversation_id,
                session=session,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 禁止 Nginx 代理缓冲，保证逐字到达
            },
        )

    response = service.answer(
        question=query_request.question,
        document_ids=query_request.document_ids,
        top_k=query_request.top_k,
        conversation_id=query_request.conversation_id,
    )
    session.commit()
    return response


async def _stream_answer(
    service: QaService,
    *,
    question: str,
    document_ids: list[uuid.UUID],
    top_k: int,
    conversation_id: uuid.UUID | None,
    session: Session,
) -> AsyncIterator[str]:
    """把 ``QaService.answer_stream`` 的 ``StreamEvent`` 序列化为 SSE 文本。

    SSE 事件格式（``event: <type>\\ndata: <json>\\n\\n``）：
    - ``token``：``{"text": "..."}``，逐字 LLM 生成内容。
    - ``done``：``{"citations": [...], "request_id": "...", "elapsed_ms": N,
      "conversation_id": "...", "message_id": "..."}``，流结束元数据；
      ``conversation_id`` 与 ``message_id`` 在单轮问答中可空。
    - ``error``：``{"detail": "..."}``，检索/LLM/证据不足等异常。
    """

    async for event in service.answer_stream(
        question=question,
        document_ids=document_ids,
        top_k=top_k,
        conversation_id=conversation_id,
    ):
        if isinstance(event, StreamDoneEvent):
            try:
                session.commit()
            except Exception:
                session.rollback()
                yield _sse("error", {"detail": "回答已生成，但会话保存失败。"})
                return
        elif isinstance(event, StreamErrorEvent):
            session.rollback()
        yield _format_sse(event)


def _format_sse(event: StreamEvent) -> str:
    """把单个 ``StreamEvent`` 格式化为 SSE 文本块。"""

    if isinstance(event, StreamTokenEvent):
        return _sse("token", {"text": event.text})
    if isinstance(event, StreamDoneEvent):
        return _sse(
            "done",
            {
                "citations": [c.model_dump(mode="json") for c in event.citations],
                "request_id": str(event.request_id),
                "elapsed_ms": event.elapsed_ms,
                "conversation_id": (
                    str(event.conversation_id) if event.conversation_id is not None else None
                ),
                "message_id": str(event.message_id) if event.message_id is not None else None,
            },
        )
    if isinstance(event, StreamErrorEvent):
        return _sse("error", {"detail": event.detail})
    # 理论不可达（StreamEvent 联合类型已穷尽），防御性兜底
    return _sse("error", {"detail": "未知事件类型"})


def _sse(event_name: str, data: dict[str, object]) -> str:
    """组装单条 SSE 事件文本（``ensure_ascii=False`` 保留中文可读性）。"""

    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
