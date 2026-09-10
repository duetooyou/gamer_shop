from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gamer_shop.infrastructure.database import Base


class OrderEventORM(Base):
    """Журнал состояний заказа. Только дополняется.

    Пишется триггером, а не приложением: любой переход, откуда бы он ни
    пришёл — интерактор, фоновая задача, ручной UPDATE в консоли, — обязан
    оставить строку. Приложение, которое пишет журнал само, рано или поздно
    забывает это сделать в одной ветке из десяти, и восстановленная картина
    начинает врать.

    Время события — now(), то есть время начала транзакции, как и у проводок
    журнала денег. Поэтому срез «на момент» никогда не разрежет транзакцию
    пополам: либо видно всё, что она сделала, либо ничего.
    """

    __tablename__ = 'order_events'

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # NULL — событие про заказ целиком, а не про отдельную позицию.
    order_item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Восстановление одного заказа на момент: событий у заказа единицы,
        # но заказов миллионы — без ведущего order_id это скан всей истории.
        Index('ix_order_events_order', 'order_id', 'occurred_at', 'id'),
        # Итоги за период идут по времени, без привязки к заказу.
        Index('ix_order_events_occurred', 'occurred_at', 'id'),
    )
