from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.infrastructure.database import Base


class SupplierDiscrepancyORM(Base):
    """Расхождение с поставщиком.

    Пишется в момент обнаружения и закрывается тем, кто его разобрал. Строка
    в состоянии open — это не «ошибка в логе», а работа, которую кто-то
    обязан доделать; в норме таких строк нет.
    """

    __tablename__ = 'supplier_discrepancies'

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment='duplicate_code/foreign_response/phantom_issue/unusable_code',
    )
    supplier: Mapped[str] = mapped_column(String(8), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'"), comment='open/resolved'
    )
    resolution: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("state IN ('open', 'resolved')", name='ck_discrepancy_state'),
        # Повторное обнаружение того же расхождения не плодит строк.
        Index(
            'uq_discrepancy_natural', 'kind', 'request_id', 'code', unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index('ix_discrepancy_open', 'created_at', postgresql_where=text("state = 'open'")),
    )
