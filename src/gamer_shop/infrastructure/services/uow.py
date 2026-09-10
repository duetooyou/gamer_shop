from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession


class SQLAlchemyUoW:
    """Единица работы поверх сессии SQLAlchemy.

    Считает вложенность: коммит и откат делает только самый внешний выход.
    Savepoint'ы намеренно не используются — частичный откат внутреннего блока
    означал бы, что состояние изменилось, а команда аутбокса пропала.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._depth = 0
        self._rollback_only = False

    @property
    def in_transaction(self) -> bool:
        return self._depth > 0

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def flush(self) -> None:
        await self._session.flush()

    async def __aenter__(self) -> Self:
        self._depth += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self._depth -= 1

        if exc_type is not None:
            # Внешний блок может исключение проглотить — коммитить ему всё
            # равно нельзя, иначе зафиксируется половина работы.
            self._rollback_only = True

        if self._depth > 0:
            if exc_type is None:
                await self._session.flush()
            return

        if self._rollback_only:
            self._rollback_only = False
            await self._session.rollback()
        else:
            await self._session.commit()
