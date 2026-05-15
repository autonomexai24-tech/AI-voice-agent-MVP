from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from database.base import Base
import database.models  # noqa: F401
from voice_agent.config import DatabaseConfig
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class DatabaseSettings:
    url: str | None
    enabled: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout_seconds: float = 5.0
    retry_attempts: int = 2
    retry_backoff_seconds: float = 0.05
    queue_max_items: int = 500
    drain_timeout_seconds: float = 2.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "DatabaseSettings":
        source = os.environ if env is None else env
        url = _optional(source, "DATABASE_URL")
        enabled = _bool(source, "PERSISTENCE_ENABLED", url is not None)
        return cls(
            url=normalize_database_url(url) if url else None,
            enabled=enabled,
            pool_size=_int(source, "DATABASE_POOL_SIZE", 5),
            max_overflow=_int(source, "DATABASE_MAX_OVERFLOW", 10),
            pool_timeout_seconds=_float(source, "DATABASE_POOL_TIMEOUT_SECONDS", 5.0),
            retry_attempts=_int(source, "DATABASE_RETRY_ATTEMPTS", 2),
            retry_backoff_seconds=_float(source, "DATABASE_RETRY_BACKOFF_SECONDS", 0.05),
            queue_max_items=_int(source, "DATABASE_QUEUE_MAX_ITEMS", 500),
            drain_timeout_seconds=_float(source, "DATABASE_DRAIN_TIMEOUT_SECONDS", 2.0),
        )

    @classmethod
    def from_agent_config(cls, config: DatabaseConfig) -> "DatabaseSettings":
        return cls(
            url=normalize_database_url(config.url) if config.url else None,
            enabled=config.enabled,
            pool_size=config.pool_size,
            max_overflow=config.max_overflow,
            pool_timeout_seconds=config.pool_timeout_seconds,
            retry_attempts=config.retry_attempts,
            retry_backoff_seconds=config.retry_backoff_seconds,
            queue_max_items=config.queue_max_items,
            drain_timeout_seconds=config.drain_timeout_seconds,
        )

    @property
    def is_configured(self) -> bool:
        return self.enabled and self.url is not None


def normalize_database_url(url: str | None) -> str | None:
    if url is None:
        return None
    stripped = url.strip()
    if stripped.startswith("postgres://"):
        return "postgresql+asyncpg://" + stripped.removeprefix("postgres://")
    if stripped.startswith("postgresql://"):
        return "postgresql+asyncpg://" + stripped.removeprefix("postgresql://")
    return stripped


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    if not settings.is_configured or settings.url is None:
        raise RuntimeError("Database is not configured")

    engine = create_async_engine(
        settings.url,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout_seconds,
        pool_pre_ping=True,
    )
    log_event(
        logger,
        "db_connection_initialized",
        driver=engine.url.drivername,
        host=engine.url.host,
        database=engine.url.database,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
    )
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )


async def initialize_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def _optional(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def _bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(source: Mapping[str, str], name: str, default: int) -> int:
    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(source: Mapping[str, str], name: str, default: float) -> float:
    raw = source.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default
