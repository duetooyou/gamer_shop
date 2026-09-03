"""Сквозной correlation_id — в каждую запись лога и в заголовок ответа."""

from uuid import uuid4

from litestar.types import ASGIApp, Message, Receive, Scope, Send

from gamer_shop.infrastructure.services import correlation_id_var

HEADER = b'x-correlation-id'


def correlation_id_middleware(app: ASGIApp) -> ASGIApp:
    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await app(scope, receive, send)
            return

        headers = dict(scope.get('headers') or [])
        cid = headers.get(HEADER, b'').decode() or str(uuid4())
        token = correlation_id_var.set(cid)

        async def send_wrapper(message: Message) -> None:
            if message['type'] == 'http.response.start':
                message.setdefault('headers', [])
                message['headers'].append((HEADER, cid.encode()))
            await send(message)

        try:
            await app(scope, receive, send_wrapper)
        finally:
            correlation_id_var.reset(token)

    return middleware
