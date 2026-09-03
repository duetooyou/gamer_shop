from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from gamer_shop.infrastructure.config import PostgresSettings


def new_session_maker(config: PostgresSettings) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        config.async_url(),
        echo=config.echo,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_pre_ping=True,
    )
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )
