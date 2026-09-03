"""Эмулятор платёжной системы, он же генератор гонок.

    python scripts/send_webhooks.py --sku STEAM-TOPUP-500 --parallel 50
    python scripts/send_webhooks.py --sku STEAM-TOPUP-500 --parallel 50 --same-event-id
    python scripts/send_webhooks.py --order-id ord_99999 --parallel 1
    python scripts/send_webhooks.py --sku KEY-GTA5 --status failed
"""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

import httpx

DEFAULT_API = 'http://127.0.0.1:8100/api/v1'


async def create_order(client: httpx.AsyncClient, api: str, sku: str) -> dict:
    response = await client.post(f'{api}/orders', json={'sku': sku})
    response.raise_for_status()
    return response.json()


def build_payload(order_id: str, status: str, amount: int, currency: str, event_id: str) -> dict:
    return {
        'event_id': event_id,
        'order_id': order_id,
        'status': status,
        'amount': amount,
        'currency': currency,
        'created_at': datetime.now(UTC).isoformat().replace('+00:00', 'Z'),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default=DEFAULT_API)
    parser.add_argument('--sku', default='STEAM-TOPUP-500')
    parser.add_argument('--order-id', default=None, help='не создавать заказ, слать по готовому id')
    parser.add_argument('--amount', type=int, default=None)
    parser.add_argument('--currency', default='RUB')
    parser.add_argument('--status', default='paid', choices=['paid', 'failed'])
    parser.add_argument('--parallel', type=int, default=50, help='сколько вебхуков слать одновременно')
    parser.add_argument('--same-event-id', action='store_true', help='все вебхуки с одним event_id')
    parser.add_argument('--wait', type=float, default=3.0, help='пауза перед проверкой результата')
    args = parser.parse_args()

    async with httpx.AsyncClient(timeout=30.0) as client:
        if args.order_id:
            order_id, amount = args.order_id, (args.amount or 0)
            print(f'Заказ не создаём, шлём по готовому order_id={order_id}')
        else:
            order = await create_order(client, args.api, args.sku)
            order_id, amount = order['id'], order['price']
            print(f'Создан заказ {order_id}, сумма {amount} {order["currency"]}')

        amount = args.amount if args.amount is not None else amount
        shared_event_id = f'evt_{uuid4().hex[:12]}'
        payloads = [
            build_payload(
                order_id,
                args.status,
                amount,
                args.currency,
                shared_event_id if args.same_event_id else f'evt_{uuid4().hex[:12]}',
            )
            for _ in range(args.parallel)
        ]

        print(f'Шлём {args.parallel} вебхуков одновременно '
              f'({"один и тот же" if args.same_event_id else "разные"} event_id)...')
        responses = await asyncio.gather(
            *(client.post(f'{args.api}/webhook/payment', json=p) for p in payloads),
            return_exceptions=True,
        )

        statuses = Counter()
        outcomes = Counter()
        for r in responses:
            if isinstance(r, Exception):
                statuses[type(r).__name__] += 1
                continue
            statuses[r.status_code] += 1
            if r.status_code == 200:
                outcomes[r.json().get('outcome')] += 1

        print(f'  HTTP-коды: {dict(statuses)}')
        print(f'  Исходы:    {dict(outcomes)}')

        if args.wait:
            await asyncio.sleep(args.wait)
        final = await client.get(f'{args.api}/orders/{order_id}')
        if final.status_code == 200:
            data = final.json()
            print(f'\nИтог по заказу {order_id}:')
            print(f'  статус: {data["status"]}')
            print(f'  код:    {data["code"]}  (поставщик: {data["supplier"]})')
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f'\nЗаказ {order_id}: HTTP {final.status_code} — {final.text}')


if __name__ == '__main__':
    asyncio.run(main())
