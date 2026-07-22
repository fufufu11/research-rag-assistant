"""业务服务层。

依据 PROJECT_PLAN.md 第 10 节（仓库结构：``services/``）。

服务层负责业务编排：调用 repository（数据访问）、pdf_parser / chunker
（文档处理）、文件 IO（落盘）。不直接拼 SQL，不实现 HTTP 路由。
"""
