"""supplier_rate_limits: ведро токенов на поставщика

Лимит у поставщика один на всех, а воркеров несколько, поэтому учёт обязан
жить в базе: разрешение выдаётся одним UPDATE, и два воркера не выдадут его
дважды. Строка обновляется на каждом обращении — отсюда пониженный
fillfactor: обновление находит место под новую версию строки на той же
странице и не гоняет индекс.

Revision ID: f1b6c04d92aa
Revises: d5a2c9b83e17
Create Date: 2026-09-10 16:00:00.000000+00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'f1b6c04d92aa'
down_revision: str | None = 'd5a2c9b83e17'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'supplier_rate_limits',
        sa.Column('supplier', sa.String(length=8), nullable=False),
        sa.Column('capacity', sa.Integer(), nullable=False,
                  comment='всплеск: столько обращений подряд поставщик стерпит'),
        sa.Column('refill_per_minute', sa.Integer(), nullable=False,
                  comment='собственно лимит поставщика'),
        sa.Column('tokens', sa.Float(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('granted', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
        sa.Column('throttled', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
        sa.CheckConstraint('capacity > 0', name='ck_rate_limit_capacity_positive'),
        sa.CheckConstraint('refill_per_minute > 0', name='ck_rate_limit_refill_positive'),
        sa.CheckConstraint('tokens >= 0', name='ck_rate_limit_tokens_non_negative'),
        sa.PrimaryKeyConstraint('supplier'),
    )
    # Строка обновляется на каждом обращении: оставляем место под новую
    # версию на той же странице.
    op.execute('ALTER TABLE supplier_rate_limits SET (fillfactor = 70)')


def downgrade() -> None:
    op.drop_table('supplier_rate_limits')
