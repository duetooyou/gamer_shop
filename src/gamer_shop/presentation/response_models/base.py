from pydantic import BaseModel, ConfigDict


class ResponseBase(BaseModel):
    """Сборка ответов из DTO по атрибутам."""

    model_config = ConfigDict(from_attributes=True)
