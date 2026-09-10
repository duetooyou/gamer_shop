"""supplier_discrepancies: расхождения с поставщиком

Ответу поставщика веры нет, поэтому расхождения нужно не только замечать,
но и хранить: строка в состоянии open — это работа, которую кто-то обязан
доделать, а не запись в логе.

Revision ID: d5a2c9b83e17
Revises: c3e8a15d7f42
Create Date: 2026-09-10 14:00:00.000000+00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'd5a2c9b83e17'
down_revision: str | None = 'c3e8a15d7f42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'supplier_discrepancies',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False,
                  comment='duplicate_code/foreign_response/phantom_issue/unusable_code'),
        sa.Column('supplier', sa.String(length=8), nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=False),
        sa.Column('order_item_id', sa.String(length=40), nullable=True),
        sa.Column('code', sa.String(length=64), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('state', sa.String(length=16), server_default=sa.text("'open'"),
                  nullable=False, comment='open/resolved'),
        sa.Column('resolution', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('open', 'resolved')", name='ck_discrepancy_state'),
        sa.PrimaryKeyConstraint('id'),
    )
    # NULLS NOT DISTINCT: расхождение без кода тоже должно быть уникальным,
    # иначе каждый повтор писал бы новую строку.
    op.execute(
        'CREATE UNIQUE INDEX uq_discrepancy_natural '
        'ON supplier_discrepancies (kind, request_id, code) NULLS NOT DISTINCT'
    )
    op.create_index(
        'ix_discrepancy_open', 'supplier_discrepancies', ['created_at'],
        postgresql_where=sa.text("state = 'open'"),
    )


def downgrade() -> None:
    op.drop_index('ix_discrepancy_open', table_name='supplier_discrepancies',
                  postgresql_where=sa.text("state = 'open'"))
    op.execute('DROP INDEX IF EXISTS uq_discrepancy_natural')
    op.drop_table('supplier_discrepancies')
