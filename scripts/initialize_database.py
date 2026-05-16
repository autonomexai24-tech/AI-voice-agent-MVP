from __future__ import annotations

import asyncio
import os

from database.session import DatabaseSettings, create_engine, initialize_schema
from voice_agent.logging_config import (
    configure_logging,
    get_logger,
    log_error,
    log_event,
)

logger = get_logger(__name__)


async def main() -> None:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    settings = DatabaseSettings.from_env()
    if not settings.is_configured:
        log_event(
            logger,
            "database_initialization_skipped",
            persistence_enabled=settings.enabled,
            has_database_url=settings.url is not None,
        )
        return

    for attempt in range(1, settings.startup_retry_attempts + 1):
        try:
            await _initialize_once(settings)
            log_event(logger, "database_schema_ready", attempt=attempt)
            return
        except Exception as exc:
            if attempt >= settings.startup_retry_attempts:
                log_error(
                    logger,
                    "database_schema_initialization_failed",
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                raise
            log_event(
                logger,
                "database_schema_initialization_retrying",
                attempt=attempt,
                error_type=type(exc).__name__,
                retry_in_seconds=settings.startup_retry_backoff_seconds,
            )
            await asyncio.sleep(settings.startup_retry_backoff_seconds)


async def _initialize_once(settings: DatabaseSettings) -> None:
    engine = create_engine(settings)
    try:
        await initialize_schema(engine)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
