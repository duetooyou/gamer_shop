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


class OutboxDepthOut(ResponseBase):
    partition_key: str
    kind: str
    state: str
    count: int


class DeadCommandOut(ResponseBase):
    id: int
    kind: str
    dedup_key: str
    attempts: int


class DiscrepancyOut(ResponseBase):
    id: int
    kind: str
    supplier: str
    request_id: str
    order_item_id: str | None = None
    code: str | None = None
    detail: str | None = None
    state: str
    resolution: str | None = None


class ReconciliationOut(ResponseBase):
    paid_not_delivered: list[OrderProblemOut]
    delivered_not_paid: list[OrderProblemOut]
    stuck_delivering: list[OrderProblemOut]
    unresolved_supplier_requests: list[OrderProblemOut]
    unapplied_events: list[OrderProblemOut]
    open_discrepancies: list[DiscrepancyOut]
    discrepancy_counts: dict[str, int]
    money_mismatch: list[OrderProblemOut]
    ledger_balances: list[AccountBalanceOut]
    ledger_is_balanced: bool
    stock_drift: list[str]
    outbox_depth: list[OutboxDepthOut]
    dead_commands: list[DeadCommandOut]
