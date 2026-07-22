"""rename page_number to start_page and add end_page

Revision ID: 46a698fd7415
Revises: 91c0c0df60b0
Create Date: 2026-07-22 20:39:34.046312

阶段 8.2 跨页切分：将 chunks.page_number（单页）拆分为 start_page + end_page
（页码范围），支持 chunk 跨越多页。现有数据按单页回填（end_page = start_page）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "46a698fd7415"
down_revision: str | Sequence[str] | None = "91c0c0df60b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: rename page_number → start_page, add end_page (NOT NULL)."""
    # 第一步：重命名 page_number → start_page，并添加可空的 end_page 列
    # SQLite 用 batch_alter_table（复制表策略）支持列重命名
    with op.batch_alter_table("chunks", schema=None) as batch_op:
        batch_op.alter_column("page_number", new_column_name="start_page")
        batch_op.add_column(sa.Column("end_page", sa.Integer(), nullable=True))

    # 第二步：回填 end_page = start_page（现有 chunk 均为单页，跨页切分前生成）
    op.execute("UPDATE chunks SET end_page = start_page WHERE end_page IS NULL")

    # 第三步：将 end_page 设为 NOT NULL
    with op.batch_alter_table("chunks", schema=None) as batch_op:
        batch_op.alter_column("end_page", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    """Downgrade schema: drop end_page, rename start_page → page_number."""
    with op.batch_alter_table("chunks", schema=None) as batch_op:
        batch_op.drop_column("end_page")
        batch_op.alter_column("start_page", new_column_name="page_number")
