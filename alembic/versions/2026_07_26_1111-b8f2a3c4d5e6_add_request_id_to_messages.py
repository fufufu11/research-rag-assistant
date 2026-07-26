"""add request_id column to messages

Revision ID: b8f2a3c4d5e6
Revises: aa7a10e898fd
Create Date: 2026-07-26 11:11:00.000000

Issue #89 / ADR 0003：在 messages 表新增可空 request_id 列（带唯一索引），
供历史消息反馈反查 feedback 状态。

- request_id 可空：旧消息（迁移前）保持 NULL，不回填（旧 request_id 未持久化）。
  user 消息也保持 NULL（#90 决策：仅 assistant 消息写入 request_id）。
- 唯一索引：一个 request_id 唯一映射一条 assistant 消息，反馈状态无歧义。
  SQL 标准对 NULL 的唯一约束语义：多条 NULL 不冲突（SQLite 遵循此标准）。

详见 ADR 0003（本迁移同步标记 ADR 0001 为 Superseded）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8f2a3c4d5e6"
down_revision: str | Sequence[str] | None = "aa7a10e898fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: messages 表新增 request_id 列 + 唯一索引。"""

    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("request_id", sa.Uuid(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_messages_request_id"),
            ["request_id"],
            unique=True,
        )


def downgrade() -> None:
    """Downgrade schema: messages 表删除 request_id 列与索引。"""

    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_messages_request_id"))
        batch_op.drop_column("request_id")
