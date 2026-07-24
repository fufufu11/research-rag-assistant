"""add feedback table for user feedback loop

Revision ID: aa7a10e898fd
Revises: 7ae8903026ac
Create Date: 2026-07-24 14:13:27.375793

阶段 10.2 用户反馈闭环：新增 feedback 表，记录用户对问答答案的点赞/点踩反馈。
- request_id 加唯一约束（Upsert 语义，匿名防刷），作为主关联键（详见 ADR 0001）。
- message_id 可空 FK→messages.id（ondelete=SET NULL）：单轮问答为 None，多轮问答
  非 None 可 join 消息内容；消息删除时反馈记录保留用于事后分析。
- rating 二值枚举（like / dislike），与 DocumentStatus / MessageRole 风格一致。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa7a10e898fd"
down_revision: str | Sequence[str] | None = "7ae8903026ac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: 创建 feedback 表。"""

    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "rating",
            sa.Enum("like", "dislike", name="feedbackrating", native_enum=False, length=20),
            nullable=False,
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_feedback_request_id"),
    )
    with op.batch_alter_table("feedback", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_feedback_request_id"), ["request_id"], unique=True)
        batch_op.create_index(batch_op.f("ix_feedback_message_id"), ["message_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema: 删除 feedback 表。"""

    with op.batch_alter_table("feedback", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_feedback_message_id"))
        batch_op.drop_index(batch_op.f("ix_feedback_request_id"))

    op.drop_table("feedback")
