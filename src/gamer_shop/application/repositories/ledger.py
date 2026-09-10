from typing import Protocol

from gamer_shop.application.dto import AccountBalanceDTO
from gamer_shop.application.enums import LedgerAccount


class LedgerRepository(Protocol):
    async def record_double_entry(
        self,
        order_id: str,
        debit_account: LedgerAccount,
        credit_account: LedgerAccount,
        amount: int,
        currency: str,
        ref_type: str,
        ref_id: str,
        idempotency_key: str,
        order_item_id: str | None = None,
    ) -> bool:
        """Проводка двумя строками. False — она уже была."""
        ...

    async def balances(self) -> list[AccountBalanceDTO]: ...

    async def order_liability(self, order_id: str) -> int:
        """Остаток обязательства: != 0 значит оплачен, но не выдан."""
        ...

    async def order_settlement(self, order_id: str) -> tuple[int, int, int]:
        """Оплачено, выдано, возвращено — прямо из проводок."""
        ...
