from .base import ResponseBase


class OrderProblemOut(ResponseBase):
    order_id: str
    status: str
    sku: str
    amount: int
    age_seconds: int
    detail: str | None = None


class AccountBalanceOut(ResponseBase):
    account: str
    debit_total: int
    credit_total: int
    balance: int


class ReconciliationOut(ResponseBase):
    paid_not_delivered: list[OrderProblemOut]
    delivered_not_paid: list[OrderProblemOut]
    stuck_delivering: list[OrderProblemOut]
    unresolved_supplier_requests: list[OrderProblemOut]
    unapplied_events: list[OrderProblemOut]
    ledger_balances: list[AccountBalanceOut]
    ledger_is_balanced: bool
    stock_drift: list[str]
