from dishka import make_async_container
from dishka.integrations.litestar import setup_dishka as setup_litestar_dishka
from litestar import Litestar
from litestar.openapi.config import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin, SwaggerRenderPlugin

from gamer_shop.application.exceptions import ApplicationException
from gamer_shop.infrastructure.config import Config
from gamer_shop.infrastructure.services import configure_logging
from gamer_shop.ioc import setup_providers
from gamer_shop.presentation.api import router
from gamer_shop.presentation.exception_handlers import application_exception_handler
from gamer_shop.presentation.middleware import correlation_id_middleware


def create_app() -> Litestar:
    config = Config()
    configure_logging(config.app.log_level, config.app.log_json)

    container = make_async_container(*setup_providers(), context={Config: config})

    openapi_config = OpenAPIConfig(
        title='Gamer Shop API',
        version='1.0.0',
        description=(
            'Ядро магазина цифровых товаров: заказ по SKU, вебхук оплаты, '
            'автовыдача кода от поставщика.'
        ),
        path='/docs',
        render_plugins=[ScalarRenderPlugin(), SwaggerRenderPlugin()],
    )

    app = Litestar(
        debug=config.app.debug,
        route_handlers=[router],
        middleware=[correlation_id_middleware],
        openapi_config=openapi_config,
        exception_handlers={ApplicationException: application_exception_handler},
    )
    setup_litestar_dishka(container=container, app=app)
    return app


app = create_app()
