"""initial schema

Revision ID: 830a3155b716
Revises: 
Create Date: 2026-09-01 11:55:38.353550+00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '830a3155b716'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Читаемые идентификаторы заказов вида ord_00123.
    op.execute('CREATE SEQUENCE order_id_seq START WITH 123')

    op.create_table('ledger_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('txn_id', sa.UUID(), nullable=False),
    sa.Column('order_id', sa.String(length=32), nullable=False),
    sa.Column('account', sa.String(length=32), nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False, comment='debit/credit'),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('ref_type', sa.String(length=32), nullable=False, comment='payment/delivery'),
    sa.Column('ref_id', sa.String(length=128), nullable=False),
    sa.Column('idempotency_key', sa.String(length=160), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("direction IN ('debit', 'credit')", name='ck_ledger_direction'),
    sa.CheckConstraint('amount > 0', name='ck_ledger_amount_positive'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index('ix_ledger_account', 'ledger_entries', ['account'], unique=False)
    op.create_index('ix_ledger_order_id', 'ledger_entries', ['order_id'], unique=False)
    op.create_index('ix_ledger_txn_id', 'ledger_entries', ['txn_id'], unique=False)
    op.create_table('payment_events',
    sa.Column('event_id', sa.String(length=128), nullable=False),
    sa.Column('order_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False, comment='paid/failed'),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True, comment='created_at из полезной нагрузки'),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('applied', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reject_reason', sa.String(length=255), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.PrimaryKeyConstraint('event_id')
    )
    op.create_index('ix_payment_events_order_id', 'payment_events', ['order_id'], unique=False)
    op.create_index('ix_payment_events_unapplied', 'payment_events', ['received_at'], unique=False, postgresql_where=sa.text('NOT applied'))
    op.create_table('products',
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False, comment='topup/key/subscription/giftcard'),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('image', sa.String(length=255), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('price >= 0', name='ck_products_price_non_negative'),
    sa.PrimaryKeyConstraint('sku')
    )
    op.create_index('ix_products_storefront', 'products', ['type', 'sku'], unique=False, postgresql_include=['name', 'price', 'currency'], postgresql_where=sa.text('is_active'))
    op.create_index('ix_products_storefront_all', 'products', ['sku'], unique=False, postgresql_include=['name', 'type', 'price', 'currency'], postgresql_where=sa.text('is_active'))
    op.create_table('orders',
    sa.Column('id', sa.String(length=32), server_default=sa.text("'ord_' || lpad(nextval('order_id_seq')::text, 5, '0')"), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('status', sa.String(length=32), server_default=sa.text("'created'"), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_reason', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('price >= 0', name='ck_orders_price_non_negative'),
    sa.ForeignKeyConstraint(['sku'], ['products.sku'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_orders_sku', 'orders', ['sku'], unique=False)
    op.create_index('ix_orders_status_updated_at', 'orders', ['status', 'updated_at'], unique=False)
    op.create_table('product_stock',
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('available_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('reserved_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.CheckConstraint('available_count >= 0', name='ck_stock_available_non_negative'),
    sa.CheckConstraint('reserved_count >= 0', name='ck_stock_reserved_non_negative'),
    sa.ForeignKeyConstraint(['sku'], ['products.sku'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('sku')
    )
    op.create_table('deliveries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('order_id', sa.String(length=32), nullable=False),
    sa.Column('code', sa.String(length=64), nullable=False),
    sa.Column('supplier', sa.String(length=8), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code'),
    sa.UniqueConstraint('order_id')
    )
    op.create_table('supplier_requests',
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('order_id', sa.String(length=32), nullable=False),
    sa.Column('supplier', sa.String(length=8), nullable=False, comment='a/b'),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False, comment='pending/ok/refused/unknown'),
    sa.Column('code', sa.String(length=64), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_error', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('request_id')
    )
    op.create_index('ix_supplier_requests_unresolved', 'supplier_requests', ['updated_at'], unique=False, postgresql_where=sa.text("state = 'unknown'"))
    op.create_index('uq_supplier_requests_order_supplier', 'supplier_requests', ['order_id', 'supplier'], unique=True)

    # Остаток меняется на каждой выдаче — запас в странице под HOT-update.
    op.execute('ALTER TABLE product_stock SET (fillfactor = 80)')
    op.execute('ALTER TABLE orders SET (fillfactor = 85)')


def downgrade() -> None:
    op.drop_index('uq_supplier_requests_order_supplier', table_name='supplier_requests')
    op.drop_index('ix_supplier_requests_unresolved', table_name='supplier_requests', postgresql_where=sa.text("state = 'unknown'"))
    op.drop_table('supplier_requests')
    op.drop_table('deliveries')
    op.drop_table('product_stock')
    op.drop_index('ix_orders_status_updated_at', table_name='orders')
    op.drop_index('ix_orders_sku', table_name='orders')
    op.drop_table('orders')
    op.drop_index('ix_products_storefront_all', table_name='products', postgresql_include=['name', 'type', 'price', 'currency'], postgresql_where=sa.text('is_active'))
    op.drop_index('ix_products_storefront', table_name='products', postgresql_include=['name', 'price', 'currency'], postgresql_where=sa.text('is_active'))
    op.drop_table('products')
    op.drop_index('ix_payment_events_unapplied', table_name='payment_events', postgresql_where=sa.text('NOT applied'))
    op.drop_index('ix_payment_events_order_id', table_name='payment_events')
    op.drop_table('payment_events')
    op.drop_index('ix_ledger_txn_id', table_name='ledger_entries')
    op.drop_index('ix_ledger_order_id', table_name='ledger_entries')
    op.drop_index('ix_ledger_account', table_name='ledger_entries')
    op.drop_table('ledger_entries')
    op.execute('DROP SEQUENCE IF EXISTS order_id_seq')
