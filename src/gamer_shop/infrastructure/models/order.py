from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.application.enums import OrderItemStatus, OrderStatus
from gamer_shop.infrastructure.database import Base
from gamer_shop.infrastructure.models.mixins import TimestampMixin

# Идентификатор заказа — строка вида ord_00123, как в контрактах.
order_id_seq = Sequence('order_id_seq', start=123, metadata=Base.metadata)


class OrderORM(Base, TimestampMixin):
    """Шапка заказа. Товары и деньги за них живут в позициях."""

    __tablename__ = 'orders'

    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        server_default=text("'ord_' || lpad(nextval('order_id_seq')::text, 5, '0')"),
    )
    # Сумма фиксируется при создании: витрина может подорожать, а сверять
    # сумму вебхука надо с той, о которой договорились.
    total_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{OrderStatus.CREATED.value}'")
    )

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint('total_amount >= 0', name='ck_orders_total_non_negative'),
        # Скан «зависших» заказов.
        Index('ix_orders_status_updated_at', 'status', 'updated_at'),
    )


class OrderItemORM(Base, TimestampMixin):
    """Позиция заказа — одна единица товара и ровно один код.

    Две одинаковые единицы дают две строки: так «один код на позицию»
    выполняется по построению, без счётчиков количества.
    """

    __tablename__ = 'order_items'

    # Читаемый и выводимый из заказа идентификатор: ord_00123-2.
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey('orders.id', ondelete='CASCADE'), nullable=False
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sku: Mapped[str] = mapped_column(
        ForeignKey('products.sku', ondelete='RESTRICT'), nullable=False
    )
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # Закрепляется при создании: витрина может сменить поставщика, а обращение
    # обязано уйти тому, с кем позиция уже связана.
    supplier: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False,
        server_default=text(f"'{OrderItemStatus.PENDING.value}'"),
    )

    # Сколько раз позицию брали в выдачу. По нему решается, пробовать ли
    # ещё или пора возвращать деньги.
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text('0')
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint('price >= 0', name='ck_order_items_price_non_negative'),
        CheckConstraint('position > 0', name='ck_order_items_position_positive'),
        UniqueConstraint('order_id', 'position', name='uq_order_items_order_position'),
        Index('ix_order_items_order_id', 'order_id'),
        Index('ix_order_items_sku', 'sku'),
        # Скан незавершённых позиций: их мало, частичный индекс держит его дешёвым.
        Index(
            'ix_order_items_unsettled',
            'status',
            'updated_at',
            postgresql_where=text("status NOT IN ('delivered', 'refunded')"),
        ),
    )
    # fillfactor задаётся в миграции.
