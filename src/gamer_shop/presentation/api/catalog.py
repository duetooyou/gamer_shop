from dataclasses import asdict

from dishka import FromDishka
from dishka.integrations.litestar import inject
from litestar import Controller, get
from litestar.params import Parameter

from gamer_shop.application.interactors import StorefrontInteractor
from gamer_shop.presentation.response_models import StorefrontPageOut


class CatalogController(Controller):
    tags = ['Каталог']

    @get(
        '/catalog',
        summary='Витрина товаров с остатками',
        description=(
            'Keyset-пагинация по sku: время ответа не зависит от глубины '
            'страницы, в отличие от OFFSET.'
        ),
        cache=False,
    )
    @inject
    async def storefront(
        self,
        interactor: FromDishka[StorefrontInteractor],
        type: str | None = Parameter(default=None, description='topup/key/subscription/giftcard'),
        cursor: str | None = Parameter(default=None, description='sku последнего элемента прошлой страницы'),
        limit: int = Parameter(default=50, ge=1, le=100),
    ) -> StorefrontPageOut:
        page = await interactor.execute(type, cursor, limit)
        return StorefrontPageOut(
            items=[asdict(i) for i in page.items], next_cursor=page.next_cursor
        )
