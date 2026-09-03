from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StubSettings(BaseSettings):
    """Настройки заглушки. Доли сбоев настраиваемые — под воспроизводимые сценарии."""

    name: str = Field(default='a')
    keys_file: str = Field(default='data/keys.json')
    # Нарезка общего пула: у A и B непересекающиеся ключи, поэтому один ключ
    # не уйдёт в два заказа даже при фолбэке.
    keys_offset: int = Field(default=0)
    keys_stride: int = Field(default=2)

    fail_rate: float = Field(default=0.0)
    timeout_rate: float = Field(default=0.0)
    # Должно превышать таймаут клиента.
    hang_seconds: float = Field(default=10.0)
    latency: float = Field(default=0.0)
    seed: int | None = Field(default=None)

    model_config = SettingsConfigDict(env_prefix='STUB_', extra='ignore')
