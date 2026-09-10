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
    # Повторы живут в релее аутбокса, а не здесь: иначе они прошли бы мимо
    # учёта обращений к поставщику и мимо его лимита.
    # Неопределённое обращение старше этого срока разбирается запросом
    # состояния.
    probe_after_seconds: int = Field(default=5)

    # Лимит, объявленный поставщиком: столько обращений в минуту он
    # разрешает. Соблюдается ведром токенов в базе — одним на все процессы.
    rate_limit_per_minute: int = Field(default=60)
    # Идём заведомо ниже объявленного, и это не перестраховка.
    #
    # Своё ведро мы опустошаем в момент, когда только собираемся послать
    # запрос, а поставщик — когда запрос до него дошёл. На одинаковых числах
    # оба ведра идут ноздря в ноздрю, и любое дрожание задержки выносит нас
    # за лимит: наш пятый токен появляется на те же миллисекунды раньше, чем
    # его. Ровно на эту разницу и берётся запас.
    rate_limit_safety: float = Field(default=0.9, ge=0.05, le=1.0)
    # Всплеск: столько обращений подряд выпускаем на полном ведре. Должен
    # быть строго меньше того, что стерпит поставщик, — по той же причине.
    rate_limit_burst: int = Field(default=5)
    # Столько ждём разрешения там, где отступить уже нельзя: у резервного
    # поставщика и при запросе состояния. Позиция в это время в выдаче, и
    # бросать её на полпути дороже, чем подождать секунду.
    permit_wait_seconds: float = Field(default=5.0)

    model_config = SettingsConfigDict(env_prefix='SUPPLIER_')


class PaymentSettings(ProjectBaseSettings):
    """Заглушка платёжной системы: возвраты."""

    url: str = Field(default='http://127.0.0.1:9201')
    request_timeout: float = Field(default=2.0)

    model_config = SettingsConfigDict(env_prefix='PAYMENT_')


class DeliverySettings(ProjectBaseSettings):
    """Фоновое дожатие «зависших» заказов."""

    # Позиция в delivering дольше этого срока считается зависшей.
    stuck_after_seconds: int = Field(default=30)
    batch_size: int = Field(default=100)
    scan_interval_seconds: int = Field(default=30)
    # Столько раз пробуем выдать позицию, прежде чем вернуть за неё деньги.
    max_attempts_before_refund: int = Field(default=3)

    model_config = SettingsConfigDict(env_prefix='DELIVERY_')


class OutboxSettings(ProjectBaseSettings):
    """Релей команд."""

    batch_size: int = Field(default=50)
    # Аренда: столько команда прячется, будучи взятой в работу. Должна с
    # запасом покрывать самый долгий поход к поставщику.
    lease_seconds: int = Field(default=60)
    # Столько релей ждёт, упершись в лимит поставщика, прежде чем зайти
    # снова: работа есть, а брать её пока нельзя.
    poll_interval_seconds: float = Field(default=1.0)
    # Дальше команда уходит в dead и попадает в сверку.
    max_attempts: int = Field(default=8)
    backoff_base: float = Field(default=0.5)
    backoff_max: float = Field(default=60.0)
    # Выполненные команды удаляются: история живёт в отдельном журнале.
    keep_done_seconds: int = Field(default=3600)

    model_config = SettingsConfigDict(env_prefix='OUTBOX_')


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
        self.payment = PaymentSettings()
        self.delivery = DeliverySettings()
        self.outbox = OutboxSettings()
