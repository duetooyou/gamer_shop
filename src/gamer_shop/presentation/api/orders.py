from dataclasses import asdict

from dishka import FromDishka
from dishka.integrations.litestar import inject
from litestar import Controller, get, post
from litestar.params import FromPath
from litestar.status_codes import HTTP_201_CREATED

from gamer_shop.application.dto import CreateOrderDTO
from gamer_shop.application.interactors import CreateOrderInteractor, GetOrderInteractor
from gamer_shop.presentation.request_models import CreateOrderIn
from gamer_shop.presentation.response_models import OrderOut


class OrderController(Controller):
    tags = ['Заказы']

    @post(
        '/orders',
        status_code=HTTP_201_CREATED,
        summary='Создать заказ по SKU',
        description='Цена фиксируется из каталога на момент создания.',
    )
    @inject
    async def create_order(
        self, data: CreateOrderIn, interactor: FromDishka[CreateOrderInteractor]
    ) -> OrderOut:
        order = await interactor.execute(CreateOrderDTO(sku=data.sku))
        return OrderOut(**asdict(order))

    @get(
        '/orders/{order_id:str}',
        summary='Получить заказ по id',
        description='Возвращает статус и выданный код, если выдача состоялась.',
    )
    @inject
    async def get_order(
        self, order_id: FromPath[str], interactor: FromDishka[GetOrderInteractor]
    ) -> OrderOut:
        view = await interactor.execute(order_id)
        return OrderOut(**asdict(view.order), code=view.code, supplier=view.supplier)
