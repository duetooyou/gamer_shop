from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.infrastructure.database import Base


class DeliveryORM(Base):
    """Факт выдачи — последний рубеж однократности.

    UNIQUE(order_item_id) и UNIQUE(code): даже при полном отказе логики вторая
    выдача по позиции и повторное использование ключа физически не запишутся.
    """

    __tablename__ = 'deliveries'

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(
        ForeignKey('order_items.id', ondelete='CASCADE'), nullable=False, unique=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supplier: Mapped[str] = mapped_column(String(8), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
