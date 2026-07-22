"""Alembic 迁移环境配置。

依据 PROJECT_PLAN.md 第 709 节（阶段 5 交付物）。

设计取舍（初学者向说明）：
- ``sqlalchemy.url`` 从环境变量 ``DATABASE_URL`` 读取（通过
  ``research_rag.db.session.get_database_url``），不在 ``alembic.ini`` 硬编码：
  避免密钥入库、便于跨环境（本地 / CI / 生产）切换数据库。
- ``target_metadata = Base.metadata``：让 ``alembic revision --autogenerate``
  对比模型与数据库差异生成迁移脚本。新增 / 修改模型后只需跑 autogenerate。
- ``compare_type=True``：让 autogenerate 检测列类型变化（如 String(64) →
  String(128)），默认只检测表结构存在性，会漏掉类型变更。
- ``render_as_batch=True``：SQLite 不支持 ALTER TABLE 部分操作（如改列类型），
  batch 模式会把 ALTER 拆成"建新表 → 复制数据 → 删旧表 → 改名"，让 SQLite
  也能执行复杂迁移。生产用 PostgreSQL 时可关闭，但开启对 PostgreSQL 无害。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from research_rag.db.models import Base
from research_rag.db.session import get_database_url

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 从环境变量读取 DATABASE_URL，覆盖 alembic.ini 中的占位（已被注释）。
# 这样 `alembic upgrade head` 与应用代码用同一个 URL 来源。
config.set_main_option("sqlalchemy.url", get_database_url())

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    仅根据 URL 生成 SQL 脚本，不连接数据库。适合在没有数据库的环境
    生成迁移 SQL 供 DBA 审查。
    """

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    创建 Engine 并在真实数据库上执行迁移。``NullPool`` 避免迁移期间
    持有连接（迁移结束连接即关闭）。
    """

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
