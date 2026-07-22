"""问答 HTTP 路由：POST /api/v1/queries。

依据 PROJECT_PLAN.md 第 8.4 节（文档问答）、US-003、第 13.6 节（异常由
API 层映射为 HTTP 状态码）。

设计取舍（初学者向说明）：
- **路由只做编排，不写业务逻辑**：本端点只做三件事——① 解析 ``QueryRequest``，
  ② 调 ``QaService.answer``，③ 返回 ``QueryResponse``。文档查询、向量检索、
  LLM 调用、引用映射等都由 service 层负责（PROJECT_PLAN 第 13.6 节"业务服务
  不直接拼接 HTTP 响应"，反向也成立：API 层不做业务）。
- **POST 返回 200**：问答是"执行查询并返回结果"而非"创建资源"，用 200 而非
  201（REST 惯例：201 用于创建新资源，如 POST /documents 创建文档记录）。
- **异常不在路由里捕获**：``InsufficientEvidenceError`` / ``LlmServiceError``
  / ``EmbeddingServiceError`` / ``VectorStoreError`` / ``DocumentNotFoundError``
  / ``NoAvailableDocumentsError`` 由 ``app.py`` 的全局异常处理器统一映射为
  HTTP 状态码，路由代码保持线性。
- **``QueryRequest`` 作为请求体**：FastAPI 自动校验 JSON 请求体，``question``
  为空或缺失时返回 422，``document_ids`` 中的 UUID 格式错误也返回 422。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, status

from research_rag.api.dependencies import get_qa_service
from research_rag.api.schemas import QueryRequest, QueryResponse
from research_rag.services.qa_service import QaService

router = APIRouter(prefix="/api/v1/queries", tags=["queries"])


@router.post("", response_model=QueryResponse, status_code=status.HTTP_200_OK)
def create_query(
    query_request: QueryRequest = Body(...),
    service: QaService = Depends(get_qa_service),
) -> QueryResponse:
    """提交问答请求。

    接收 ``QueryRequest``（问题 + 可选文档 ID 过滤 + 可选 top_k），调用
    ``QaService.answer`` 完成检索、LLM 问答和引用映射，返回 ``QueryResponse``
    （答案 + 引用列表 + request_id + 耗时）。

    可能的异常（由全局处理器映射为 HTTP 状态码）：
    - ``DocumentNotFoundError`` → 404（指定的 document_ids 中有不存在的 UUID）
    - ``NoAvailableDocumentsError`` → 404（无可用 READY 文档）
    - ``InsufficientEvidenceError`` → 422（上下文证据不足以回答）
    - ``LlmServiceError`` → 503（LLM 服务不可用）
    - ``EmbeddingServiceError`` → 503（Embedding 服务不可用）
    - ``VectorStoreError`` → 500（向量索引或检索失败）
    """

    return service.answer(
        question=query_request.question,
        document_ids=query_request.document_ids,
        top_k=query_request.top_k,
    )
