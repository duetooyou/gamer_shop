from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Float, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.infrastructure.database import Base


class SupplierRateLimitORM(Base):
    """Ведро токенов на поставщика, живущее в базе.

    В памяти процесса его держать нельзя: воркеров несколько, а лимит у
    поставщика один на всех. Строка — единственная точка учёта, и выдача
    разрешения умещается в один UPDATE, поэтому два воркера не выдадут
    одно и то же разрешение дважды.

    Токены не пересчитываются по таймеру: они доливаются в момент обращения,
    исходя из времени, прошедшего с прошлого. Спящих процессов и фонового
    тика для этого не нужно.
    """

    __tablename__ = 'supplier_rate_limits'

    supplier: Mapped[str] = mapped_column(String(8), primary_key=True)
    # Всплеск: столько обращений подряд поставщик стерпит.
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Собственно лимит: столько обращений в минуту он разрешает.
    refill_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Счётчики для отчёта о прогрессе: сколько обращений выпущено и сколько
    # раз очередь упёрлась в лимит.
    granted: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text('0'))
    throttled: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text('0'))

    __table_args__ = (
        CheckConstraint('capacity > 0', name='ck_rate_limit_capacity_positive'),
        CheckConstraint('refill_per_minute > 0', name='ck_rate_limit_refill_positive'),
        CheckConstraint('tokens >= 0', name='ck_rate_limit_tokens_non_negative'),
    )
    # fillfactor задаётся в миграции: строка обновляется на каждом обращении.
