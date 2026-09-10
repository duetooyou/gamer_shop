from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class RefundOutcomeKind(StrEnum):
    """Как и у поставщика, три исхода, а не «успех/ошибка».

    UNKNOWN означает, что деньги могли уйти: повторять можно только с тем же
    refund_id, иначе покупателю вернут дважды.
    """

    OK = 'ok'
    REFUSED = 'refused'
    UNKNOWN = 'unknown'


@dataclass(frozen=True, slots=True)
class RefundOutcome:
    kind: RefundOutcomeKind
    refund_id: str
    reason: str | None = None


class PaymentGateway(Protocol):
    async def refund(
        self,
        refund_id: str,
        order_id: str,
        amount: int,
        currency: str,
        reason: str,
    ) -> RefundOutcome:
        """Вернуть деньги. Идемпотентно по refund_id."""
        ...
