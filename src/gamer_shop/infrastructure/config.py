from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProjectBaseSettings(BaseSettings):
    """Общая база настроек: .env, вложенные ключи через '__'."""

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        env_nested_delimiter='__',
        case_sensitive=False,
        extra='ignore',
        populate_by_name=True,
    )


class PostgresSettings(ProjectBaseSettings):
    host: str = Field(default='127.0.0.1')
    port: int = Field(default=5432)
    user: str = Field(default='postgres')
    password: str = Field(default='postgres')
    database: str = Field(default='gamer_shop')
    echo: bool = Field(default=False)

    # На один процесс. Процессов несколько (api, воркеры, планировщик),
    # так что (pool_size + max_overflow) * их число должно влезать
    # в max_connections сервера.
    pool_size: int = Field(default=10)
    max_overflow: int = Field(default=10)

    model_config = SettingsConfigDict(env_prefix='POSTGRES_')

    def async_url(self) -> str:
        return (
            f'postgresql+asyncpg://{self.user}:{self.password}'
            f'@{self.host}:{self.port}/{self.database}'
        )


class RedisSettings(ProjectBaseSettings):
    host: str = Field(default='127.0.0.1')
    port: int = Field(default=6379)
    db: int = Field(default=0)
    password: str | None = Field(default=None)

    model_config = SettingsConfigDict(env_prefix='REDIS_')

    def url(self) -> str:
        auth = f':{self.password}@' if self.password else ''
        return f'redis://{auth}{self.host}:{self.port}/{self.db}'


class SupplierSettings(ProjectBaseSettings):
    """Клиент к поставщикам: сначала primary, затем fallback."""

    primary_url: str = Field(default='http://127.0.0.1:9101')
    fallback_url: str = Field(default='http://127.0.0.1:9102')

    request_timeout: float = Field(default=2.0)
    # Сколько раз повторяем одному поставщику с тем же request_id.
    max_attempts: int = Field(default=3)
    # base * 2**(attempt-1) плюс джиттер.
    backoff_base: float = Field(default=0.2)
    backoff_max: float = Field(default=2.0)
    backoff_jitter: float = Field(default=0.1)

    model_config = SettingsConfigDict(env_prefix='SUPPLIER_')


class DeliverySettings(ProjectBaseSettings):
    """Фоновое дожатие «зависших» заказов."""

    # Заказ в delivering дольше этого срока считается зависшим.
    stuck_after_seconds: int = Field(default=30)
    batch_size: int = Field(default=100)
    scan_interval_seconds: int = Field(default=30)

    model_config = SettingsConfigDict(env_prefix='DELIVERY_')


class AppSettings(ProjectBaseSettings):
    debug: bool = Field(default=False)
    log_level: str = Field(default='INFO')
    log_json: bool = Field(default=True)

    model_config = SettingsConfigDict(env_prefix='APP_')


class Config:
    """Собирается один раз на старте и кладётся в DI-контекст."""

    def __init__(self) -> None:
        self.app = AppSettings()
        self.postgres = PostgresSettings()
        self.redis = RedisSettings()
        self.supplier = SupplierSettings()
        self.delivery = DeliverySettings()
