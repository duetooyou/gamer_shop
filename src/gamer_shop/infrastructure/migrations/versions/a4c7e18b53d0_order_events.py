"""order_events: журнал состояний заказа, только на дополнение

Состояние заказа хранится перезаписью: статус меняется на месте, и прошлое
из таблицы не достать. Журнал возвращает прошлое обратно.

Пишется триггером, а не приложением. Приложение, которое ведёт журнал само,
рано или поздно забывает сделать это в одной ветке из десяти, и
восстановленная картина начинает врать; триггер же не пропустит перехода,
откуда бы тот ни пришёл — хоть из ручного UPDATE в консоли.

Дополнение защищено тем же способом: UPDATE и DELETE по журналу — и по
проводкам денег — запрещены триггером. «Задним числом ничего не
переписывается» перестаёт быть обещанием и становится ограничением.

Revision ID: a4c7e18b53d0
Revises: f1b6c04d92aa
Create Date: 2026-09-10 16:30:00.000000+00:00
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'a4c7e18b53d0'
down_revision: str | None = 'f1b6c04d92aa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'order_events',
        sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column('order_id', sa.String(length=32), nullable=False),
        sa.Column('order_item_id', sa.String(length=40), nullable=True),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('from_status', sa.String(length=32), nullable=True),
        sa.Column('to_status', sa.String(length=32), nullable=True),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'{}'::jsonb"), nullable=False),
        # now(), а не clock_timestamp(): время начала транзакции, как и у
        # проводок. Срез «на момент» не разрежет транзакцию пополам.
        sa.Column('occurred_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_order_events_order', 'order_events', ['order_id', 'occurred_at', 'id'])
    op.create_index('ix_order_events_occurred', 'order_events', ['occurred_at', 'id'])

    # --- журнал заказа -----------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION log_order_event() RETURNS trigger AS $fn$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO order_events (order_id, kind, to_status, payload)
                VALUES (NEW.id, 'order_created', NEW.status,
                        jsonb_build_object('total_amount', NEW.total_amount,
                                           'currency', NEW.currency));
            ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
                INSERT INTO order_events (order_id, kind, from_status, to_status, payload)
                VALUES (NEW.id, 'order_status_changed', OLD.status, NEW.status,
                        jsonb_build_object('failure_reason', NEW.failure_reason));
            END IF;
            RETURN NULL;
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        'CREATE TRIGGER trg_orders_journal AFTER INSERT OR UPDATE ON orders '
        'FOR EACH ROW EXECUTE FUNCTION log_order_event()'
    )

    # --- журнал позиции ----------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION log_order_item_event() RETURNS trigger AS $fn$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                INSERT INTO order_events (order_id, order_item_id, kind, to_status, payload)
                VALUES (NEW.order_id, NEW.id, 'item_created', NEW.status,
                        jsonb_build_object('sku', NEW.sku, 'price', NEW.price,
                                           'currency', NEW.currency,
                                           'supplier', NEW.supplier,
                                           'position', NEW.position));
            ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
                INSERT INTO order_events (order_id, order_item_id, kind,
                                          from_status, to_status, payload)
                VALUES (NEW.order_id, NEW.id, 'item_status_changed', OLD.status, NEW.status,
                        jsonb_build_object('failure_reason', NEW.failure_reason,
                                           'delivery_attempts', NEW.delivery_attempts));
            END IF;
            RETURN NULL;
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        'CREATE TRIGGER trg_order_items_journal AFTER INSERT OR UPDATE ON order_items '
        'FOR EACH ROW EXECUTE FUNCTION log_order_item_event()'
    )

    # --- факт выдачи -------------------------------------------------------
    # Сам код в журнал не кладём: журнал читают широко, а код — это товар.
    op.execute(
        """
        CREATE FUNCTION log_delivery_event() RETURNS trigger AS $fn$
        BEGIN
            INSERT INTO order_events (order_id, order_item_id, kind, payload)
            SELECT i.order_id, NEW.order_item_id, 'code_issued',
                   jsonb_build_object('supplier', NEW.supplier,
                                      'request_id', NEW.request_id)
              FROM order_items i
             WHERE i.id = NEW.order_item_id;
            RETURN NULL;
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        'CREATE TRIGGER trg_deliveries_journal AFTER INSERT ON deliveries '
        'FOR EACH ROW EXECUTE FUNCTION log_delivery_event()'
    )

    # --- запрет переписывания ---------------------------------------------
    # TRUNCATE строчные триггеры не задевает, так что очистка тестовой базы
    # по-прежнему работает; правка отдельной строки — нет.
    op.execute(
        """
        CREATE FUNCTION forbid_rewrite() RETURNS trigger AS $fn$
        BEGIN
            RAISE EXCEPTION '% только дополняется: % запрещён',
                            TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $fn$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        'CREATE TRIGGER trg_order_events_append_only '
        'BEFORE UPDATE OR DELETE ON order_events '
        'FOR EACH ROW EXECUTE FUNCTION forbid_rewrite()'
    )
    op.execute(
        'CREATE TRIGGER trg_ledger_entries_append_only '
        'BEFORE UPDATE OR DELETE ON ledger_entries '
        'FOR EACH ROW EXECUTE FUNCTION forbid_rewrite()'
    )


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS trg_ledger_entries_append_only ON ledger_entries')
    op.execute('DROP TRIGGER IF EXISTS trg_order_events_append_only ON order_events')
    op.execute('DROP TRIGGER IF EXISTS trg_deliveries_journal ON deliveries')
    op.execute('DROP TRIGGER IF EXISTS trg_order_items_journal ON order_items')
    op.execute('DROP TRIGGER IF EXISTS trg_orders_journal ON orders')
    op.execute('DROP FUNCTION IF EXISTS forbid_rewrite()')
    op.execute('DROP FUNCTION IF EXISTS log_delivery_event()')
    op.execute('DROP FUNCTION IF EXISTS log_order_item_event()')
    op.execute('DROP FUNCTION IF EXISTS log_order_event()')
    op.drop_index('ix_order_events_occurred', table_name='order_events')
    op.drop_index('ix_order_events_order', table_name='order_events')
    op.drop_table('order_events')
