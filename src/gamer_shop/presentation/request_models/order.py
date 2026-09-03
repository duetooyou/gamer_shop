from pydantic import BaseModel, Field


class CreateOrderIn(BaseModel):
    """Клиент передаёт только SKU — цену берём из каталога."""

    sku: str = Field(..., min_length=1, max_length=64, description='SKU товара из каталога')
