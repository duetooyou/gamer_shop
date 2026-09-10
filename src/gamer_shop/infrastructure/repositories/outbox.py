from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import NewCommandDTO, OutboxCommandDTO, OutboxStatsDTO
from gamer_shop.application.enums import OutboxState
from gamer_shop.application.interfaces import UoW
from gamer_shop.infrastructure.models import OutboxCommandORM

# Видимость команд считается по clock_timestamp(), а не now(): now() — это
# время начала транзакции, и внутри долгой транзакции очередь бы застыла.
# Одним запросом: выбрать доступные, спрятать под аренду и вернуть.
# Порядок — приоритет, затем время готовности: оплаченное обгоняет
# неоплаченное, при равном приоритете очередь честно FIFO.
_CLAIM_SQL = text(
    """
    UPDATE outbox o
       SET attempts      = o.attempts + 1,
           available_at  = clock_timestamp() + make_interval(secs => :lease),
           updated_at    = now()
      FROM (
            SELECT id
              FROM outbox
             WHERE state = 'pending'
               AND available_at <= clock_timestamp()
               -- Приведение типа обязательно: без него asyncpg не выведет
               -- тип параметра, когда партиция не задана.
               AND (CAST(:partition_key AS text) IS NULL
                    OR partition_key = CAST(:partition_key AS text))
             ORDER BY priority DESC, available_at, id
               FOR UPDATE SKIP LOCKED
             LIMIT :limit
           ) picked
     WHERE o.id = picked.id
 RETURNING o.id, o.kind, o.dedup_key, o.payload, o.partition_key, o.priority, o.attempts
    """
)


# Время готовности считает сервер: часы приложения могут разъезжаться.
_AVAILABLE_AT = text('clock_timestamp() + make_interval(secs => :delay_seconds)')


class SqlAlchemyOutboxRepository:
    def __init__(self, session: AsyncSession, uow: UoW) -> None:
        self._session = session
        self._uow = uow

    async def enqueue(self, command: NewCommandDTO) -> bool:
        """Поставить команду. False — такая уже стоит невыполненной.

        Вызывать только внутри транзакции, меняющей состояние: смысл аутбокса
        ровно в том, что команда и её причина коммитятся вместе.
        """
        if not self._uow.in_transaction:
            raise RuntimeError(
                'outbox.enqueue вне транзакции: команда разъедется с состоянием'
            )

        stmt = (
            pg_insert(OutboxCommandORM)
            .values(
                kind=command.kind.value,
                dedup_key=command.dedup_key,
                payload=command.payload,
                partition_key=command.partition_key,
                priority=command.priority,
                available_at=_AVAILABLE_AT.bindparams(
                    delay_seconds=command.delay_seconds
                ),
            )
            .on_conflict_do_nothing(
                index_elements=['kind', 'dedup_key'],
                index_where=text("state = 'pending'"),
            )
            .returning(OutboxCommandORM.id)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def claim(
        self, limit: int, lease_seconds: int, partition_key: str | None = None
    ) -> list[OutboxCommandDTO]:
        rows = await self._session.execute(
            _CLAIM_SQL,
            {'limit': limit, 'lease': lease_seconds, 'partition_key': partition_key},
        )
        return [
            OutboxCommandDTO(
                id=row.id,
                kind=row.kind,
                dedup_key=row.dedup_key,
                payload=row.payload,
                partition_key=row.partition_key,
                priority=row.priority,
                attempts=row.attempts,
            )
            for row in rows
        ]

    async def mark_done(self, command_id: int) -> None:
        await self._session.execute(
            text(
                "UPDATE outbox SET state = 'done', dispatched_at = now(), "
                'updated_at = now(), last_error = NULL WHERE id = :id'
            ),
            {'id': command_id},
        )

    async def ready_by_partition(self) -> dict[str, int]:
        """Сколько команд в каждой партиции можно взять прямо сейчас.

        Релей смотрит сюда перед тем, как брать работу: партиции с лимитом
        поставщика он ограничивает, а в пустые не ходит вовсе.
        """
        rows = await self._session.execute(
            text(
                "SELECT partition_key, count(*) AS ready FROM outbox "
                " WHERE state = 'pending' AND available_at <= clock_timestamp() "
                ' GROUP BY partition_key'
            )
        )
        return {row.partition_key: int(row.ready) for row in rows}

    async def postpone(self, command_id: int, delay_seconds: float) -> None:
        """Отложить, не считая попыткой.

        Упереться в лимит поставщика — не то же самое, что провалиться:
        работа не начиналась. Счётчик попыток, увеличенный взятием в работу,
        возвращаем обратно, иначе всплеск сам себя добьёт до dead.
        """
        await self._session.execute(
            text(
                'UPDATE outbox '
                '   SET available_at = clock_timestamp() + make_interval(secs => :delay), '
                '       attempts = GREATEST(attempts - 1, 0), '
                '       last_error = NULL, updated_at = now() '
                ' WHERE id = :id'
            ),
            {'id': command_id, 'delay': delay_seconds},
        )

    async def reschedule(self, command_id: int, delay_seconds: float, error: str) -> None:
        """Вернуть в очередь с отсрочкой. Состояние остаётся pending —
        повтор здесь, а не в клиенте, поэтому он тоже проходит через лимит."""
        await self._session.execute(
            text(
                'UPDATE outbox SET available_at = clock_timestamp() + make_interval(secs => :delay), '
                'last_error = :error, updated_at = now() WHERE id = :id'
            ),
            {'id': command_id, 'delay': delay_seconds, 'error': error[:512]},
        )

    async def mark_dead(self, command_id: int, error: str) -> None:
        await self._session.execute(
            text(
                "UPDATE outbox SET state = 'dead', last_error = :error, "
                'updated_at = now() WHERE id = :id'
            ),
            {'id': command_id, 'error': error[:512]},
        )

    async def stats(self) -> list[OutboxStatsDTO]:
        stmt = (
            select(
                OutboxCommandORM.partition_key,
                OutboxCommandORM.kind,
                OutboxCommandORM.state,
                func.count().label('count'),
                func.min(OutboxCommandORM.available_at).label('oldest'),
            )
            .group_by(
                OutboxCommandORM.partition_key,
                OutboxCommandORM.kind,
                OutboxCommandORM.state,
            )
            .order_by(OutboxCommandORM.partition_key, OutboxCommandORM.kind)
        )
        return [
            OutboxStatsDTO(
                partition_key=row.partition_key,
                kind=row.kind,
                state=row.state,
                count=int(row.count),
                oldest_available_at=row.oldest,
            )
            for row in (await self._session.execute(stmt)).all()
        ]

    async def dead(self, limit: int) -> list[OutboxCommandDTO]:
        stmt = (
            select(OutboxCommandORM)
            .where(OutboxCommandORM.state == OutboxState.DEAD.value)
            .order_by(OutboxCommandORM.updated_at.desc())
            .limit(limit)
        )
        return [
            OutboxCommandDTO(
                id=orm.id,
                kind=orm.kind,
                dedup_key=orm.dedup_key,
                payload=orm.payload,
                partition_key=orm.partition_key,
                priority=orm.priority,
                attempts=orm.attempts,
            )
            for orm in (await self._session.execute(stmt)).scalars().all()
        ]

    async def prune(self, older_than_seconds: int) -> int:
        """Выполненные команды не нужны: история живёт в отдельном журнале."""
        result = await self._session.execute(
            text(
                "DELETE FROM outbox WHERE state = 'done' "
                'AND dispatched_at < clock_timestamp() - make_interval(secs => :age)'
            ),
            {'age': older_than_seconds},
        )
        return result.rowcount
