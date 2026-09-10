from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.infrastructure.database import Base


class OutboxCommandORM(Base):
    """Команда во внешний мир, записанная в транзакции изменения состояния.

    Хранит намерение, а не факт: строка живёт, пока команда не выполнена, и
    подчищается позже. Историю сюда писать нельзя — для неё отдельный
    журнал, который никогда не меняется.
    """

    __tablename__ = 'outbox'

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Ключ шардирования: по нему же считается лимит поставщика.
    partition_key: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'default'")
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text('0')
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Не более одной незавершённой команды на предмет. Уникальность частичная:
    # выполненная команда не мешает поставить такую же заново.
    dedup_key: Mapped[str] = mapped_column(String(160), nullable=False)

    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pending'"),
        comment='pending/done/dead',
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    # Аренда: взятая в работу команда прячется на время выполнения и всплывает
    # сама, если исполнитель умер, не отчитавшись.
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("state IN ('pending', 'done', 'dead')", name='ck_outbox_state'),
        Index(
            'uq_outbox_pending',
            'kind',
            'dedup_key',
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        Index(
            'ix_outbox_ready',
            'partition_key',
            text('priority DESC'),
            'available_at',
            postgresql_where=text("state = 'pending'"),
        ),
        Index(
            'ix_outbox_done',
            'dispatched_at',
            postgresql_where=text("state = 'done'"),
        ),
    )
    # fillfactor задаётся в миграции: каждая попытка обновляет строку.
