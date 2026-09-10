"""outbox: команды во внешний мир

Очередь живёт в той же базе, что и состояние: только так команда и её
причина коммитятся одной транзакцией.

Revision ID: b7d1f4a9c052
Revises: 830a3155b716
Create Date: 2026-09-10 12:00:00.000000+00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b7d1f4a9c052'
down_revision: str | None = '830a3155b716'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'outbox',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('partition_key', sa.String(length=32), server_default=sa.text("'default'"), nullable=False),
        sa.Column('priority', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('dedup_key', sa.String(length=160), nullable=False),
        sa.Column('state', sa.String(length=16), server_default=sa.text("'pending'"), nullable=False, comment='pending/done/dead'),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('available_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('dispatched_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("state IN ('pending', 'done', 'dead')", name='ck_outbox_state'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Не более одной невыполненной команды на предмет; выполненная не мешает
    # поставить такую же заново.
    op.create_index(
        'uq_outbox_pending', 'outbox', ['kind', 'dedup_key'],
        unique=True, postgresql_where=sa.text("state = 'pending'"),
    )
    # Рабочая выборка релея: партиция, приоритет, время готовности.
    op.execute(
        'CREATE INDEX ix_outbox_ready ON outbox '
        '(partition_key, priority DESC, available_at) '
        "WHERE state = 'pending'"
    )
    op.create_index(
        'ix_outbox_done', 'outbox', ['dispatched_at'],
        postgresql_where=sa.text("state = 'done'"),
    )
    # Каждая попытка обновляет строку — оставляем запас в странице.
    op.execute('ALTER TABLE outbox SET (fillfactor = 70)')


def downgrade() -> None:
    op.drop_index('ix_outbox_done', table_name='outbox', postgresql_where=sa.text("state = 'done'"))
    op.execute('DROP INDEX IF EXISTS ix_outbox_ready')
    op.drop_index('uq_outbox_pending', table_name='outbox', postgresql_where=sa.text("state = 'pending'"))
    op.drop_table('outbox')
