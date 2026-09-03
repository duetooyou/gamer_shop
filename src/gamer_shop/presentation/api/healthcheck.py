from dishka import FromDishka
from dishka.integrations.litestar import inject
from litestar import Controller, get
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class HealthCheckController(Controller):
    tags = ['Служебные']

    @get('/health', summary='Liveness')
    async def health(self) -> dict[str, str]:
        return {'status': 'ok'}

    @get('/ready', summary='Readiness: проверяет доступность БД')
    @inject
    async def ready(self, session: FromDishka[AsyncSession]) -> dict[str, str]:
        await session.execute(text('SELECT 1'))
        return {'status': 'ok', 'database': 'ok'}
