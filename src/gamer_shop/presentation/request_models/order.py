from pydantic import BaseModel, Field, model_validator


class OrderLineIn(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64, description='SKU товара из каталога')
    quantity: int = Field(default=1, ge=1, le=10, description='Число единиц товара')


class CreateOrderIn(BaseModel):
    """Заказ из нескольких товаров.

    Форма одного SKU оставлена: контракт первого этапа продолжает работать,
    а заказ из одной позиции — частный случай заказа из нескольких.
    """

    sku: str | None = Field(default=None, min_length=1, max_length=64)
    items: list[OrderLineIn] | None = Field(default=None, max_length=20)

    @model_validator(mode='after')
    def require_one_form(self) -> 'CreateOrderIn':
        if not self.sku and not self.items:
            raise ValueError('нужен sku либо непустой items')
        if self.sku and self.items:
            raise ValueError('sku и items вместе не принимаются')
        return self

    def lines(self) -> list[OrderLineIn]:
        return self.items if self.items else [OrderLineIn(sku=self.sku)]
