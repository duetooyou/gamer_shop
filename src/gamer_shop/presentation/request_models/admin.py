from pydantic import BaseModel, Field


class RefillStockIn(BaseModel):
    count: int = Field(..., gt=0, le=100_000, description='Сколько единиц добавить к остатку')
