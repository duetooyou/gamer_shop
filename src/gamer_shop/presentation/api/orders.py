from dishka import FromDishka
from dishka.integrations.litestar import inject
from litestar import Controller, get, post
from litestar.params import FromPath
from litestar.status_codes import HTTP_201_CREATED

from gamer_shop.application.dto import CreateOrderDTO, CreateOrderLineDTO, OrderViewDTO
from gamer_shop.application.interactors import CreateOrderInteractor, GetOrderInteractor
from gamer_shop.presentation.request_models import CreateOrderIn
from gamer_shop.presentation.response_models import OrderItemOut, OrderOut


def _to_out(view: OrderViewDTO) -> OrderOut:
    order = view.order
    return OrderOut(
        id=order.id,
        total_amount=order.total_amount,
        currency=order.currency,
        status=order.status.value,
        created_at=order.created_at,
        updated_at=order.updated_at,
        paid_at=order.paid_at,
        settled_at=order.settled_at,
        failure_reason=order.failure_reason,
        items=[
            OrderItemOut(
                id=v.item.id,
                position=v.item.position,
                sku=v.item.sku,
                price=v.item.price,
                currency=v.item.currency,
                supplier=v.item.supplier.value,
                status=v.item.status.value,
                code=v.code,
                delivered_by=v.delivered_by,
                delivered_at=v.item.delivered_at,
                refunded_at=v.item.refunded_at,
                failure_reason=v.item.failure_reason,
            )
            for v in view.items
        ],
    )


class OrderController(Controller):
    tags = ['Заказы']

    @post(
        '/orders',
        status_code=HTTP_201_CREATED,
        summary='Создать заказ из одного или нескольких товаров',
        description=(
            'Цены фиксируются из каталога на момент создания. Количество '
            'разворачивается в отдельные позиции: у каждой свой поставщик '
            'и свой код.'
        ),
    )
    @inject
    async def create_order(
        self, data: CreateOrderIn, interactor: FromDishka[CreateOrderInteractor]
    ) -> OrderOut:
        view = await interactor.execute(
            CreateOrderDTO(
                lines=[
                    CreateOrderLineDTO(sku=line.sku, quantity=line.quantity)
                    for line in data.lines()
                ]
            )
        )
        return _to_out(view)

    @get(
        '/orders/{order_id:str}',
        summary='Получить заказ по id',
        description='Статус заказа и каждой позиции вместе с выданными кодами.',
    )
    @inject
    async def get_order(
        self, order_id: FromPath[str], interactor: FromDishka[GetOrderInteractor]
    ) -> OrderOut:
        return _to_out(await interactor.execute(order_id))
