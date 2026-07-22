"""FastAPI 应用层（阶段 5）。

依据 PROJECT_PLAN.md 第 8 节（API 草案）、第 10 节（仓库结构）、
第 13.6 节（API 层负责将异常转换为稳定错误码）。

分层职责：
- ``schemas.py``：Pydantic 请求/响应模型（与 ORM ``Document`` 解耦）。
- ``dependencies.py``：FastAPI 依赖注入（DB Session、DocumentService）。
- ``routes/documents.py``：文档管理 HTTP 路由（上传/列表/详情/删除）。
- ``app.py``：``create_app()`` 应用工厂 + lifespan + CORS + 异常处理器。
"""
