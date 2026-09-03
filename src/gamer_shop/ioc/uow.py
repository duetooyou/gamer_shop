from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.interfaces import UoW
from gamer_shop.infrastructure.services import SQLAlchemyUoW


class UoWProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def get_uow(self, session: AsyncSession) -> UoW:
        return SQLAlchemyUoW(session)
