from .base import ResponseBase


class StorefrontItemOut(ResponseBase):
    sku: str
    name: str
    type: str
    price: int
    currency: str
    available_count: int


class StorefrontPageOut(ResponseBase):
    items: list[StorefrontItemOut]
    next_cursor: str | None = None
