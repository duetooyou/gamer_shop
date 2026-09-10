from typing import Protocol

from gamer_shop.application.dto import OutboxCommandDTO


class CommandDispatcher(Protocol):
    """Исполнение одной команды аутбокса.

    Каждая команда получает собственную область видимости и собственную
    транзакцию: соседи не должны делить снимок данных и падать вместе.
    Ошибка выбрасывается наружу — решение о повторе принимает релей.
    """

    async def dispatch(self, command: OutboxCommandDTO) -> None: ...


class OutboxNotifier(Protocol):
    """Побудка релея после постановки команды.

    Отвечает только за задержку, но не за доставку: гарантия живёт в таблице,
    и потерянная побудка означает лишь то, что команду подберёт ближайший
    плановый проход.
    """

    async def notify(self) -> None: ...
