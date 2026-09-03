from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.infrastructure.database import Base


class PaymentEventORM(Base):
    """Журнал вебхуков оплаты.

    Первичный ключ = event_id: идемпотентность обеспечивает сама БД
    через ON CONFLICT DO NOTHING.
    """

    __tablename__ = 'payment_events'

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # Без FK: вебхук может прийти раньше заказа, внешний ключ его отверг бы.
    order_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment='paid/failed')
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment='created_at из полезной нагрузки'
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # applied=false — событие принято, но к заказу не применено: заказа нет
    # либо расхождение суммы. Такие добирает фоновая задача.
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('false'))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index('ix_payment_events_order_id', 'order_id'),
        # Непринятых событий мало — частичный индекс держит скан дешёвым.
        Index(
            'ix_payment_events_unapplied',
            'received_at',
            postgresql_where=text('NOT applied'),
        ),
    )


class SupplierRequestORM(Base):
    """Обращение к поставщику за кодом.

    request_id закреплён за парой (заказ, поставщик) уникальным ограничением —
    повтор физически не может уехать на новый идентификатор.
    """

    __tablename__ = 'supplier_requests'

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey('orders.id', ondelete='CASCADE'), nullable=False
    )
    supplier: Mapped[str] = mapped_column(String(8), nullable=False, comment='a/b')
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, comment='pending/ok/refused/unknown'
    )
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index('uq_supplier_requests_order_supplier', 'order_id', 'supplier', unique=True),
        # Незакрытые после таймаута — их дожимает фоновая задача.
        Index(
            'ix_supplier_requests_unresolved',
            'updated_at',
            postgresql_where=text("state = 'unknown'"),
        ),
    )
