from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.application.enums import OrderStatus
from gamer_shop.infrastructure.database import Base
from gamer_shop.infrastructure.models.mixins import TimestampMixin

# Идентификатор заказа — строка вида ord_00123, как в контрактах.
order_id_seq = Sequence('order_id_seq', start=123, metadata=Base.metadata)


class OrderORM(Base, TimestampMixin):
    __tablename__ = 'orders'

    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        server_default=text("'ord_' || lpad(nextval('order_id_seq')::text, 5, '0')"),
    )
    sku: Mapped[str] = mapped_column(
        ForeignKey('products.sku', ondelete='RESTRICT'), nullable=False
    )
    # Цена фиксируется при создании: витрина может подорожать, а сверять
    # сумму вебхука надо с той, о которой договорились.
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{OrderStatus.CREATED.value}'")
    )

    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        CheckConstraint('price >= 0', name='ck_orders_price_non_negative'),
        # Скан «зависших» заказов.
        Index('ix_orders_status_updated_at', 'status', 'updated_at'),
        Index('ix_orders_sku', 'sku'),
    )
