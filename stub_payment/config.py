from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StubPaymentSettings(BaseSettings):
    """Заглушка платёжной системы. Доли сбоев настраиваемые —
    под воспроизводимые сценарии."""

    fail_rate: float = Field(default=0.0)
    timeout_rate: float = Field(default=0.0)
    # Должно превышать таймаут клиента.
    hang_seconds: float = Field(default=10.0)
    latency: float = Field(default=0.0)
    seed: int | None = Field(default=None)

    model_config = SettingsConfigDict(env_prefix='STUB_PAYMENT_', extra='ignore')
