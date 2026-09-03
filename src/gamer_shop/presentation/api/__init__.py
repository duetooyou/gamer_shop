from litestar import Router

from .admin import AdminController
from .catalog import CatalogController
from .healthcheck import HealthCheckController
from .orders import OrderController
from .webhooks import PaymentWebhookController

router = Router(
    path='/api/v1',
    route_handlers=[
        HealthCheckController,
        CatalogController,
        OrderController,
        PaymentWebhookController,
        AdminController,
    ],
)

__all__ = ['router']
