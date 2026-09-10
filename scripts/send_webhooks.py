"""Эмулятор платёжной системы, он же генератор гонок и всплесков.

    # пятьдесят «оплачено» по одному заказу — гонка вебхуков
    python scripts/send_webhooks.py --sku STEAM-TOPUP-500 --parallel 50

    # то же с одним event_id — идемпотентность
    python scripts/send_webhooks.py --sku KEY-CS2-PRIME --parallel 50 --same-event-id

    # заказ из нескольких товаров, часть которых не выдастся
    python scripts/send_webhooks.py --items STEAM-TOPUP-500:1,SUB-DISCORD-1M:5

    # всплеск: тридцать заказов оплачены разом, лимит поставщика в силе
    python scripts/send_webhooks.py --sku KEY-GTA5 --orders 30 --wait 20
"""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from uuid import uuid4

import httpx

DEFAULT_API = 'http://127.0.0.1:8000/api/v1'


def parse_items(raw: str) -> list[dict]:
    """'SKU:2,OTHER' -> [{'sku': 'SKU', 'quantity': 2}, {'sku': 'OTHER', ...}]"""
    lines = []
    for chunk in raw.split(','):
        sku, _, qty = chunk.partition(':')
        lines.append({'sku': sku.strip(), 'quantity': int(qty or 1)})
    return lines


async def create_order(client: httpx.AsyncClient, api: str, body: dict) -> dict:
    response = await client.post(f'{api}/orders', json=body)
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


def describe(order: dict) -> str:
    lines = [f'  статус заказа: {order["status"]}']
    for item in order['items']:
        code = item.get('code') or '—'
        by = item.get('delivered_by') or '—'
        lines.append(
            f'    {item["id"]}  {item["sku"]:<18} {item["status"]:<16} '
            f'код {code}  поставщик {by}'
        )
    return '\n'.join(lines)


async def pay(client: httpx.AsyncClient, api: str, order: dict, args) -> Counter:
    amount = args.amount if args.amount is not None else order['total_amount']
    shared = f'evt_{uuid4().hex[:12]}'
    payloads = [
        build_payload(
            order['id'], args.status, amount, args.currency,
            shared if args.same_event_id else f'evt_{uuid4().hex[:12]}',
        )
        for _ in range(args.parallel)
    ]
    responses = await asyncio.gather(
        *(client.post(f'{api}/webhook/payment', json=p) for p in payloads),
        return_exceptions=True,
    )
    outcomes: Counter = Counter()
    for r in responses:
        if isinstance(r, Exception):
            outcomes[type(r).__name__] += 1
        elif r.status_code == 200:
            outcomes[r.json().get('outcome')] += 1
        else:
            outcomes[f'http_{r.status_code}'] += 1
    return outcomes


async def show_progress(client: httpx.AsyncClient, api: str) -> None:
    response = await client.get(f'{api}/admin/progress')
    if response.status_code != 200:
        return
    data = response.json()
    print(
        f'  выдано {data["items_delivered"]}, возвращено {data["items_refunded"]}, '
        f'в очереди {data["items_in_queue"]}'
    )
    for queue in data['queue']:
        print(
            f'    поставщик {queue["supplier"]}: в очереди {queue["queued"]}, '
            f'в пути {queue["in_flight"]}'
        )
    for limit in data['rate_limits']:
        print(
            f'    лимит {limit["supplier"]}: темп {limit["pace_per_minute"]}/мин, '
            f'свободно {limit["available"]}, выпущено {limit["granted"]}, '
            f'подождало {limit["throttled"]}'
        )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default=DEFAULT_API)
    parser.add_argument('--sku', default='STEAM-TOPUP-500')
    parser.add_argument('--items', default=None, help="'SKU:2,OTHER:1' — заказ из нескольких товаров")
    parser.add_argument('--order-id', default=None, help='не создавать заказ, слать по готовому id')
    parser.add_argument('--orders', type=int, default=1, help='сколько заказов создать и оплатить разом')
    parser.add_argument('--amount', type=int, default=None)
    parser.add_argument('--currency', default='RUB')
    parser.add_argument('--status', default='paid', choices=['paid', 'failed'])
    parser.add_argument('--parallel', type=int, default=1, help='сколько вебхуков слать на каждый заказ')
    parser.add_argument('--same-event-id', action='store_true', help='все вебхуки с одним event_id')
    parser.add_argument('--wait', type=float, default=3.0, help='пауза перед проверкой результата')
    args = parser.parse_args()

    body = {'items': parse_items(args.items)} if args.items else {'sku': args.sku}

    async with httpx.AsyncClient(timeout=60.0) as client:
        if args.order_id:
            orders = [{'id': args.order_id, 'total_amount': args.amount or 0}]
            print(f'Заказ не создаём, шлём по готовому order_id={args.order_id}')
        else:
            orders = [
                await create_order(client, args.api, body) for _ in range(args.orders)
            ]
            first = orders[0]
            print(
                f'Создано заказов: {len(orders)}, первый {first["id"]}, '
                f'сумма {first["total_amount"]} {first["currency"]}, '
                f'позиций {len(first["items"])}'
            )

        print(
            f'Шлём {args.parallel} вебхук(ов) на каждый из {len(orders)} заказов '
            f'({"один и тот же" if args.same_event_id else "разные"} event_id)...'
        )
        results = await asyncio.gather(*(pay(client, args.api, o, args) for o in orders))
        merged: Counter = sum(results, Counter())
        print(f'  Исходы: {dict(merged)}')

        if args.wait:
            await asyncio.sleep(args.wait)

        print('\nПрогресс:')
        await show_progress(client, args.api)

        print('\nИтог по заказам:')
        statuses: Counter = Counter()
        for order in orders:
            final = await client.get(f'{args.api}/orders/{order["id"]}')
            if final.status_code != 200:
                statuses[f'http_{final.status_code}'] += 1
                continue
            data = final.json()
            statuses[data['status']] += 1
            if len(orders) == 1:
                print(describe(data))
                print(json.dumps(data, ensure_ascii=False, indent=2))
        if len(orders) > 1:
            print(f'  {dict(statuses)}')


if __name__ == '__main__':
    asyncio.run(main())
