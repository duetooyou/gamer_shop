from uuid import uuid4

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamer_shop.application.dto import AccountBalanceDTO
from gamer_shop.application.enums import LedgerAccount, LedgerDirection
from gamer_shop.infrastructure.models import LedgerEntryORM


class SqlAlchemyLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> bool:
        """Две строки одним запросом — «половина» проводки появиться не может."""
        txn_id = uuid4()
        rows = [
            {
                'id': uuid4(),
                'txn_id': txn_id,
                'order_id': order_id,
                'account': debit_account.value,
                'direction': LedgerDirection.DEBIT.value,
                'amount': amount,
                'currency': currency,
                'ref_type': ref_type,
                'ref_id': ref_id,
                'idempotency_key': f'{idempotency_key}:debit',
            },
            {
                'id': uuid4(),
                'txn_id': txn_id,
                'order_id': order_id,
                'account': credit_account.value,
                'direction': LedgerDirection.CREDIT.value,
                'amount': amount,
                'currency': currency,
                'ref_type': ref_type,
                'ref_id': ref_id,
                'idempotency_key': f'{idempotency_key}:credit',
            },
        ]
        stmt = (
            pg_insert(LedgerEntryORM)
            .values(rows)
            .on_conflict_do_nothing(index_elements=['idempotency_key'])
            .returning(LedgerEntryORM.id)
        )
        inserted = (await self._session.execute(stmt)).scalars().all()
        return len(inserted) == 2

    async def balances(self) -> list[AccountBalanceDTO]:
        debit = func.coalesce(
            func.sum(
                case(
                    (LedgerEntryORM.direction == LedgerDirection.DEBIT.value, LedgerEntryORM.amount),
                    else_=0,
                )
            ),
            0,
        )
        credit = func.coalesce(
            func.sum(
                case(
                    (LedgerEntryORM.direction == LedgerDirection.CREDIT.value, LedgerEntryORM.amount),
                    else_=0,
                )
            ),
            0,
        )
        stmt = select(LedgerEntryORM.account, debit, credit).group_by(LedgerEntryORM.account)
        return [
            AccountBalanceDTO(account=account, debit_total=int(d), credit_total=int(c))
            for account, d, c in (await self._session.execute(stmt)).all()
        ]

    async def order_liability(self, order_id: str) -> int:
        debit = func.coalesce(
            func.sum(
                case(
                    (LedgerEntryORM.direction == LedgerDirection.DEBIT.value, LedgerEntryORM.amount),
                    else_=0,
                )
            ),
            0,
        )
        credit = func.coalesce(
            func.sum(
                case(
                    (LedgerEntryORM.direction == LedgerDirection.CREDIT.value, LedgerEntryORM.amount),
                    else_=0,
                )
            ),
            0,
        )
        stmt = select(credit - debit).where(
            LedgerEntryORM.order_id == order_id,
            LedgerEntryORM.account == LedgerAccount.CUSTOMER_LIABILITY.value,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)
