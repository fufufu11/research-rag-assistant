"""文档管理 HTTP 路由：上传 / 列表 / 详情 / 删除。

依据 PROJECT_PLAN.md 第 8.2 节（上传文档）、第 8.3 节（文档管理）、
US-001 / US-002、第 13.6 节（异常由 API 层映射为 HTTP 状态码）。

设计取舍（初学者向说明）：
- **路由前缀 ``/api/v1``**：对齐 PROJECT_PLAN 第 8 节 API 草案，为后续版本演进
  留余地（未来 ``/api/v2`` 可共存）。
- **路由只做编排，不写业务逻辑**：每个端点只做三件事——① 解析请求参数，
  ② 调 ``DocumentService`` 方法，③ 把 ORM ``Document`` 转成 ``DocumentRead``。
  sha256 去重、文件落盘、状态机等都由 service 层负责（PROJECT_PLAN 第 13.6
  节"业务服务不直接拼接 HTTP 响应"，反向也成立：API 层不做业务）。
- **ORM → schema 转换用 ``model_validate``**：``DocumentRead.model_validate(doc)``
  借助 ``from_attributes=True`` 直接读 ORM 属性，无需手写字段映射。
- **POST 返回 201**：REST 惯例，资源创建用 ``201 Created`` 而非 200 OK。
- **DELETE 返回 204**：删除成功无响应体，符合 REST 惯例。
- **``doc_id`` 用 ``uuid.UUID`` 类型注解**：FastAPI 自动校验路径参数格式，
  非法 UUID 返回 422，合法 UUID 直接传入 service（无需手动解析）。
- **异常不在路由里捕获**：``DuplicateDocumentError`` / ``DocumentNotFoundError``
  由 ``app.py`` 的全局异常处理器统一映射为 409/404，路由代码保持线性。
- **不实现文件大小/MIME 强制校验**：本 Issue 范围限定（验收标准"可选简单校验，
  不强制"），service 层会通过 ``parse_pdf`` 自然拒绝非 PDF 文件（抛
  ``InvalidPdfError`` → service 标记 FAILED）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, File, UploadFile, status

from research_rag.api.dependencies import get_document_service
from research_rag.api.schemas import DocumentList, DocumentRead

if TYPE_CHECKING:
    from research_rag.db.models import Document
    from research_rag.services.document_service import DocumentService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(..., description="要上传的 PDF 文件"),
    service: DocumentService = Depends(get_document_service),
) -> Document:
    """上传 PDF 文档。

    接收 ``multipart/form-data`` 上传的文件，调用 ``DocumentService.upload_document``
    完成 sha256 去重、落盘、解析、切分与状态机流转。重复上传抛
    ``DuplicateDocumentError``（由全局处理器映射为 409）。
    """

    file_bytes = file.file.read()
    return service.upload_document(file_bytes, file.filename or "unknown")


@router.get("", response_model=DocumentList)
def list_documents(service: DocumentService = Depends(get_document_service)) -> DocumentList:
    """返回所有文档（按创建时间降序）。"""

    docs = service.list_documents()
    return DocumentList(items=[DocumentRead.model_validate(d) for d in docs])


@router.get("/{doc_id}", response_model=DocumentRead)
def get_document(
    doc_id: uuid.UUID,
    service: DocumentService = Depends(get_document_service),
) -> Document:
    """按 ID 查询文档详情。不存在抛 ``DocumentNotFoundError``（→ 404）。"""

    return service.get_document(doc_id)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    doc_id: uuid.UUID,
    service: DocumentService = Depends(get_document_service),
) -> None:
    """删除文档（DB 记录 + 磁盘文件）。不存在抛 ``DocumentNotFoundError``（→ 404）。"""

    service.delete_document(doc_id)
