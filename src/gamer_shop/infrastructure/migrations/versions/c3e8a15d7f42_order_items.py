"""order_items: заказ из нескольких товаров

Единицей выдачи становится позиция, а не заказ. Существующие заказы
переносятся в одну позицию каждый — без этого история потеряла бы связь
с выдачами и обращениями к поставщику.

Revision ID: c3e8a15d7f42
Revises: b7d1f4a9c052
Create Date: 2026-09-10 13:00:00.000000+00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = 'c3e8a15d7f42'
down_revision: str | None = 'b7d1f4a9c052'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- товар знает своего поставщика -------------------------------------
    op.add_column(
        'products',
        sa.Column('supplier', sa.String(length=8), server_default=sa.text("'a'"), nullable=False),
    )

    # --- позиции ------------------------------------------------------------
    op.create_table(
        'order_items',
        sa.Column('id', sa.String(length=40), nullable=False),
        sa.Column('order_id', sa.String(length=32), nullable=False),
        sa.Column('position', sa.SmallInteger(), nullable=False),
        sa.Column('sku', sa.String(length=64), nullable=False),
        sa.Column('price', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('supplier', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('delivery_attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_reason', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('price >= 0', name='ck_order_items_price_non_negative'),
        sa.CheckConstraint('position > 0', name='ck_order_items_position_positive'),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sku'], ['products.sku'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id', 'position', name='uq_order_items_order_position'),
    )
    op.create_index('ix_order_items_order_id', 'order_items', ['order_id'])
    op.create_index('ix_order_items_sku', 'order_items', ['sku'])
    op.create_index(
        'ix_order_items_unsettled', 'order_items', ['status', 'updated_at'],
        postgresql_where=sa.text("status NOT IN ('delivered', 'refunded')"),
    )
    op.execute('ALTER TABLE order_items SET (fillfactor = 85)')

    # Перенос: каждый существующий заказ становится заказом из одной позиции.
    op.execute(
        """
        INSERT INTO order_items (
            id, order_id, position, sku, price, currency, supplier, status,
            delivered_at, failure_reason, created_at, updated_at
        )
        SELECT o.id || '-1', o.id, 1, o.sku, o.price, o.currency,
               COALESCE(p.supplier, 'a'),
               CASE o.status
                   WHEN 'created'         THEN 'pending'
                   WHEN 'payment_failed'  THEN 'pending'
                   WHEN 'paid'            THEN 'paid'
                   WHEN 'delivering'      THEN 'delivering'
                   WHEN 'delivered'       THEN 'delivered'
                   WHEN 'out_of_stock'    THEN 'out_of_stock'
                   WHEN 'delivery_failed' THEN 'delivery_failed'
                   ELSE 'pending'
               END,
               o.delivered_at, o.failure_reason, o.created_at, o.updated_at
        FROM orders o
        LEFT JOIN products p ON p.sku = o.sku
        """
    )

    # --- выдачи и обращения переезжают на позицию ---------------------------
    op.add_column('deliveries', sa.Column('order_item_id', sa.String(length=40), nullable=True))
    op.execute("UPDATE deliveries SET order_item_id = order_id || '-1'")
    op.alter_column('deliveries', 'order_item_id', nullable=False)
    op.drop_constraint('deliveries_order_id_fkey', 'deliveries', type_='foreignkey')
    op.drop_constraint('deliveries_order_id_key', 'deliveries', type_='unique')
    op.drop_column('deliveries', 'order_id')
    op.create_unique_constraint('deliveries_order_item_id_key', 'deliveries', ['order_item_id'])
    op.create_foreign_key(
        'deliveries_order_item_id_fkey', 'deliveries', 'order_items',
        ['order_item_id'], ['id'], ondelete='CASCADE',
    )

    op.add_column('supplier_requests', sa.Column('order_item_id', sa.String(length=40), nullable=True))
    op.execute("UPDATE supplier_requests SET order_item_id = order_id || '-1'")
    op.alter_column('supplier_requests', 'order_item_id', nullable=False)
    op.drop_index('uq_supplier_requests_order_supplier', table_name='supplier_requests')
    op.drop_constraint('supplier_requests_order_id_fkey', 'supplier_requests', type_='foreignkey')
    op.drop_column('supplier_requests', 'order_id')
    op.create_index(
        'uq_supplier_requests_item_supplier', 'supplier_requests',
        ['order_item_id', 'supplier'], unique=True,
    )
    op.create_foreign_key(
        'supplier_requests_order_item_id_fkey', 'supplier_requests', 'order_items',
        ['order_item_id'], ['id'], ondelete='CASCADE',
    )

    # --- проводки: выдача и возврат считаются по позициям -------------------
    op.add_column('ledger_entries', sa.Column('order_item_id', sa.String(length=40), nullable=True))
    op.execute(
        "UPDATE ledger_entries SET order_item_id = order_id || '-1' WHERE ref_type = 'delivery'"
    )
    op.create_index('ix_ledger_order_item_id', 'ledger_entries', ['order_item_id'])
    op.alter_column(
        'ledger_entries', 'ref_type',
        existing_type=sa.String(length=32), comment='payment/delivery/refund',
        existing_comment='payment/delivery', existing_nullable=False,
    )

    # --- шапка заказа теряет товар, но получает сумму -----------------------
    op.add_column('orders', sa.Column('total_amount', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True))
    op.execute('UPDATE orders SET total_amount = price')
    op.execute("UPDATE orders SET settled_at = delivered_at WHERE status = 'delivered'")
    # Статусы позиций теперь сами по себе: на уровне заказа восстановимых нет.
    op.execute(
        "UPDATE orders SET status = 'delivering' "
        "WHERE status IN ('out_of_stock', 'delivery_failed')"
    )
    op.alter_column('orders', 'total_amount', nullable=False)
    op.drop_index('ix_orders_sku', table_name='orders')
    op.drop_constraint('orders_sku_fkey', 'orders', type_='foreignkey')
    op.drop_constraint('ck_orders_price_non_negative', 'orders', type_='check')
    op.drop_column('orders', 'sku')
    op.drop_column('orders', 'price')
    op.drop_column('orders', 'delivered_at')
    op.create_check_constraint('ck_orders_total_non_negative', 'orders', 'total_amount >= 0')


def downgrade() -> None:
    op.drop_constraint('ck_orders_total_non_negative', 'orders', type_='check')
    op.add_column('orders', sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('price', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('sku', sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE orders o
           SET sku = i.sku, price = i.price, delivered_at = i.delivered_at
          FROM order_items i
         WHERE i.order_id = o.id AND i.position = 1
        """
    )
    op.alter_column('orders', 'sku', nullable=False)
    op.alter_column('orders', 'price', nullable=False)
    op.create_check_constraint('ck_orders_price_non_negative', 'orders', 'price >= 0')
    op.create_foreign_key('orders_sku_fkey', 'orders', 'products', ['sku'], ['sku'], ondelete='RESTRICT')
    op.create_index('ix_orders_sku', 'orders', ['sku'])
    op.drop_column('orders', 'settled_at')
    op.drop_column('orders', 'total_amount')

    op.alter_column(
        'ledger_entries', 'ref_type',
        existing_type=sa.String(length=32), comment='payment/delivery',
        existing_comment='payment/delivery/refund', existing_nullable=False,
    )
    op.drop_index('ix_ledger_order_item_id', table_name='ledger_entries')
    op.drop_column('ledger_entries', 'order_item_id')

    op.add_column('supplier_requests', sa.Column('order_id', sa.String(length=32), nullable=True))
    op.execute("UPDATE supplier_requests SET order_id = split_part(order_item_id, '-', 1)")
    op.alter_column('supplier_requests', 'order_id', nullable=False)
    op.drop_constraint('supplier_requests_order_item_id_fkey', 'supplier_requests', type_='foreignkey')
    op.drop_index('uq_supplier_requests_item_supplier', table_name='supplier_requests')
    op.drop_column('supplier_requests', 'order_item_id')
    op.create_index('uq_supplier_requests_order_supplier', 'supplier_requests', ['order_id', 'supplier'], unique=True)
    op.create_foreign_key('supplier_requests_order_id_fkey', 'supplier_requests', 'orders', ['order_id'], ['id'], ondelete='CASCADE')

    op.add_column('deliveries', sa.Column('order_id', sa.String(length=32), nullable=True))
    op.execute("UPDATE deliveries SET order_id = split_part(order_item_id, '-', 1)")
    op.alter_column('deliveries', 'order_id', nullable=False)
    op.drop_constraint('deliveries_order_item_id_fkey', 'deliveries', type_='foreignkey')
    op.drop_constraint('deliveries_order_item_id_key', 'deliveries', type_='unique')
    op.drop_column('deliveries', 'order_item_id')
    op.create_unique_constraint('deliveries_order_id_key', 'deliveries', ['order_id'])
    op.create_foreign_key('deliveries_order_id_fkey', 'deliveries', 'orders', ['order_id'], ['id'], ondelete='CASCADE')

    op.drop_index('ix_order_items_unsettled', table_name='order_items', postgresql_where=sa.text("status NOT IN ('delivered', 'refunded')"))
    op.drop_index('ix_order_items_sku', table_name='order_items')
    op.drop_index('ix_order_items_order_id', table_name='order_items')
    op.drop_table('order_items')
    op.drop_column('products', 'supplier')
