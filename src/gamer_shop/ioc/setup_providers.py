from dishka import Provider

from .common import CommonProvider
from .interactors import InteractorsProvider
from .logging import LoggingProvider
from .postgres_session import PostgresSessionProvider
from .repositories import RepositoriesProvider
from .suppliers import SupplierProvider
from .tasks import TaskQueueProvider
from .uow import UoWProvider


def setup_providers() -> list[Provider]:
    return [
        CommonProvider(),
        LoggingProvider(),
        PostgresSessionProvider(),
        UoWProvider(),
        RepositoriesProvider(),
        SupplierProvider(),
        InteractorsProvider(),
        TaskQueueProvider(),
    ]
