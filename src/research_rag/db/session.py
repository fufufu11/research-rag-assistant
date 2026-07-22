"""SQLAlchemy Session 配置。

依据 PROJECT_PLAN.md 第 709 节（阶段 5 交付物）、第 11 节（DATABASE_URL）。

设计取舍（初学者向说明）：
- 用工厂函数而非模块级单例：``create_session_factory(database_url)`` 接受
  URL 参数，测试时可注入临时 SQLite（``sqlite:///:memory:`` 或 ``tmp_path``
  下的文件），不污染全局状态。真实运行时由调用方用 ``get_database_url()``
  读环境变量后调用，避免导入本模块就建立连接池。
- ``expire_on_commit=False``：commit 后对象属性不过期，避免在请求处理过程中
  访问属性触发额外查询（FastAPI 场景常见配置）。
- ``future=True``：强制使用 SQLAlchemy 2.0 风格 API，与 ``models.py`` 一致。
- SQLite 的 ``check_same_thread`` 由 ``create_engine_for_url`` 自动处理：
  FastAPI 同步路由运行在线程池，多个请求可能用不同线程访问同一 engine，
  而 SQLite 默认禁止跨线程使用连接，必须关闭此检查（否则运行时抛
  ``ProgrammingError: SQLite objects created in a thread can only be used
  in that same thread``）。帮助函数按 URL 前缀判断，只对 SQLite 生效，
  PostgreSQL 等其他数据库不受影响。
- 不在本模块创建模块级 engine / sessionmaker：测试中每个测试函数需要独立
  数据库，模块级单例会让测试互相干扰。应用启动时在 ``api/app.py`` 的
  ``lifespan`` 中调用 ``create_engine_for_url`` 创建单例 engine，并在关闭时
  ``dispose``。
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# 默认数据库 URL（PROJECT_PLAN.md 第 11 节、.env.example）
# ``sqlite:///./data/app.db``：相对当前工作目录的 data/ 目录
# （.gitignore 已忽略 data/，不提交 SQLite 文件）
DEFAULT_DATABASE_URL = "sqlite:///./data/app.db"


def get_database_url() -> str:
    """从环境变量读取 ``DATABASE_URL``，未设置时返回默认值。

    默认值与 ``.env.example`` 一致，确保不配置 ``.env`` 也能本地运行。
    """

    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_engine_for_url(database_url: str) -> Any:
    """按 URL 创建 SQLAlchemy engine，自动为 SQLite 处理跨线程连接。

    FastAPI 的同步路由（``def`` 而非 ``async def``）运行在线程池中，不同请求
    可能由不同线程处理；SQLAlchemy 的连接池会把连接分发给不同线程。SQLite
    默认 ``check_same_thread=True``，禁止连接跨线程使用，会在线程池场景抛
    ``ProgrammingError``。本函数检测 URL 前缀，仅对 SQLite 关闭此检查；
    PostgreSQL / MySQL 等服务端数据库无此限制。

    Args:
        database_url: SQLAlchemy 数据库 URL（如 ``sqlite:///./data/app.db``）。

    Returns:
        ``Engine`` 实例。调用方负责在应用关闭时 ``dispose()`` 释放连接池
        （通常在 FastAPI ``lifespan`` 中处理）。
    """

    connect_args: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, future=True)


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    """创建 session 工厂。

    Args:
        database_url: 数据库 URL。``None`` 时用 ``get_database_url()``。
            测试时可传 ``sqlite:///:memory:`` 或临时文件 URL，隔离数据库。

    Returns:
        ``sessionmaker[Session]``，调用返回 ``Session`` 实例。
        ``expire_on_commit=False`` 让 commit 后对象属性仍可用。

    Note:
        返回的 factory 持有自己的 engine，多次调用 ``create_session_factory``
        会创建多个 engine。生产环境通常只调用一次（应用启动时），测试环境
        每个测试函数调用一次（隔离数据库）。如需在应用关闭时释放 engine，
        可通过 ``factory.kw["bind"]`` 取回 engine 并 ``dispose()``，或在
        ``api/app.py`` 中直接用 ``create_engine_for_url`` + ``sessionmaker``。
    """

    url = database_url or get_database_url()
    engine = create_engine_for_url(url)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
