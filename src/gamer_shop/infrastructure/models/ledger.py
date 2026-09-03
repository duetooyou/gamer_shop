from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.infrastructure.database import Base


class LedgerEntryORM(Base):
    """Журнал денежных движений, двойная запись.

    Каждая операция — две строки на равную сумму, поэтому сумма дебетов минус
    сумма кредитов тождественно ноль. Только на запись: строки не меняются.
    """

    __tablename__ = 'ledger_entries'

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    txn_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    order_id: Mapped[str] = mapped_column(String(32), nullable=False)
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False, comment='debit/credit')
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    ref_type: Mapped[str] = mapped_column(String(32), nullable=False, comment='payment/delivery')
    ref_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # Повтор вебхука или выдачи не должен породить вторую проводку.
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint('amount > 0', name='ck_ledger_amount_positive'),
        CheckConstraint(
            "direction IN ('debit', 'credit')", name='ck_ledger_direction'
        ),
        Index('ix_ledger_order_id', 'order_id'),
        Index('ix_ledger_account', 'account'),
        Index('ix_ledger_txn_id', 'txn_id'),
    )
