"""Лимит поставщика: ведро токенов в базе.

Токены не доливает фоновый тик: они начисляются в момент обращения, исходя
из времени, прошедшего с прошлого. Ни таймера, ни спящего процесса для
этого не нужно, а после простоя ведро оказывается ровно там, где и должно.

Выдача разрешения — один UPDATE с условием. Под READ COMMITTED второй
UPDATE, дождавшись блокировки строки, перечитывает её и заново проверяет
условие, поэтому два воркера не выдадут одно и то же разрешение дважды.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import RateLimitDecisionDTO, RateLimitStateDTO
from gamer_shop.application.enums import SupplierName
from gamer_shop.infrastructure.config import SupplierSettings

# Сколько токенов в ведре прямо сейчас: то, что было, плюс налив за
# прошедшее время, но не больше ёмкости.
_AVAILABLE = """
    LEAST(capacity::float8,
          tokens + EXTRACT(EPOCH FROM (clock_timestamp() - updated_at))
                   * refill_per_minute / 60.0)
"""

# Настройка — источник правды для ёмкости и лимита, но переписывать строку
# на каждом обращении незачем: условие в DO UPDATE оставляет запись только
# тогда, когда настройка действительно изменилась.
_ENSURE_SQL = text(
    """
    INSERT INTO supplier_rate_limits (supplier, capacity, refill_per_minute, tokens)
    VALUES (:supplier, :capacity, :per_minute, :initial_tokens)
    ON CONFLICT (supplier) DO UPDATE
       SET capacity          = EXCLUDED.capacity,
           refill_per_minute = EXCLUDED.refill_per_minute,
           tokens            = LEAST(supplier_rate_limits.tokens, EXCLUDED.capacity)
     WHERE supplier_rate_limits.capacity          IS DISTINCT FROM EXCLUDED.capacity
        OR supplier_rate_limits.refill_per_minute IS DISTINCT FROM EXCLUDED.refill_per_minute
    """
)

_ACQUIRE_SQL = text(
    f"""
    UPDATE supplier_rate_limits
       SET tokens     = {_AVAILABLE} - 1,
           updated_at = clock_timestamp(),
           granted    = granted + 1
     WHERE supplier = :supplier
       AND {_AVAILABLE} >= 1
 RETURNING tokens, refill_per_minute
    """
)

# Отказ тоже надо посчитать — по нему видно, что очередь упёрлась в лимит,
# а не в поставщика. tokens и updated_at не трогаем: иначе сдвинули бы точку
# отсчёта налива и потеряли накопленное.
_THROTTLED_SQL = text(
    f"""
    UPDATE supplier_rate_limits
       SET throttled = throttled + 1
     WHERE supplier = :supplier
 RETURNING {_AVAILABLE} AS available, refill_per_minute
    """
)

_STATE_SQL = text(
    f"""
    SELECT supplier, capacity, refill_per_minute, granted, throttled,
           {_AVAILABLE} AS available
      FROM supplier_rate_limits
     ORDER BY supplier
    """
)

# Меньше этого не ждём: точный срок до следующего токена вырождается
# в busy loop, когда до него микросекунды.
_MIN_RETRY_AFTER = 0.05


class SqlAlchemySupplierRateLimitRepository:
    def __init__(self, session: AsyncSession, settings: SupplierSettings) -> None:
        self._session = session
        self._settings = settings

    def _pace(self) -> int:
        """Наш темп: объявленный лимит поставщика минус запас."""
        return max(
            1,
            int(
                self._settings.rate_limit_per_minute * self._settings.rate_limit_safety
            ),
        )

    async def acquire(self, supplier: SupplierName) -> RateLimitDecisionDTO:
        await self._session.execute(
            _ENSURE_SQL,
            {
                'supplier': supplier.value,
                'capacity': self._settings.rate_limit_burst,
                'per_minute': self._pace(),
                # Отдельным параметром, а не тем же: одно и то же имя в
                # integer- и float-колонке asyncpg вывести не может.
                'initial_tokens': float(self._settings.rate_limit_burst),
            },
        )
        row = (
            await self._session.execute(_ACQUIRE_SQL, {'supplier': supplier.value})
        ).one_or_none()
        if row is not None:
            return RateLimitDecisionDTO(
                granted=True, retry_after_seconds=0.0, tokens_left=float(row.tokens)
            )

        denied = (
            await self._session.execute(_THROTTLED_SQL, {'supplier': supplier.value})
        ).one()
        available = float(denied.available)
        per_second = max(int(denied.refill_per_minute), 1) / 60.0
        return RateLimitDecisionDTO(
            granted=False,
            retry_after_seconds=max((1.0 - available) / per_second, _MIN_RETRY_AFTER),
            tokens_left=available,
        )

    async def available(self) -> dict[str, float]:
        # Поставщик, к которому ещё не обращались, ведра не имеет — но ведро
        # у него полное по построению. Отдаём ёмкость, иначе релей на первом
        # же проходе взял бы работы больше, чем сможет выполнить.
        budget = {
            supplier.value: float(self._settings.rate_limit_burst)
            for supplier in SupplierName
        }
        rows = (await self._session.execute(_STATE_SQL)).all()
        budget.update({row.supplier: float(row.available) for row in rows})
        return budget

    async def state(self) -> list[RateLimitStateDTO]:
        rows = (await self._session.execute(_STATE_SQL)).all()
        return [
            RateLimitStateDTO(
                supplier=row.supplier,
                capacity=int(row.capacity),
                pace_per_minute=int(row.refill_per_minute),
                available=round(float(row.available), 3),
                granted=int(row.granted),
                throttled=int(row.throttled),
            )
            for row in rows
        ]
