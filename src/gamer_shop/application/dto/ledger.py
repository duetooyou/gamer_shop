from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AccountBalanceDTO:
    account: str
    debit_total: int
    credit_total: int

    @property
    def balance(self) -> int:
        return self.debit_total - self.credit_total
