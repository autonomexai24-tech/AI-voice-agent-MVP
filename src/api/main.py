from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import os

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from api.errors import api_error, http_exception_handler
from api.routes.bookings import router as bookings_router
from api.routes.calls import router as calls_router
from api.routes.settings import router as settings_router
from api.routes.transcripts import router as transcripts_router
from database.session import (
    DatabaseSettings,
    create_engine,
    create_session_factory,
    initialize_schema,
)
from voice_agent.deployment_diagnostics import DeploymentDiagnosticsService
from voice_agent.logging_config import get_logger, log_error, log_event
from voice_agent.runtime_metrics import log_deployment_event

logger = get_logger(__name__)


def create_app(
    *,
    database_settings: DatabaseSettings | None = None,
    initialize_database: bool | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = database_settings or DatabaseSettings.from_env()
        schema_initialization_enabled = _schema_initialization_enabled(
            initialize_database
        )
        app.state.database_settings = settings
        app.state.schema_initialization_enabled = schema_initialization_enabled
        app.state.db_engine = None
        app.state.session_factory = None
        app.state.db_initialization_error = None
        log_deployment_event(
            logger,
            "backend_started",
            worker_id=None,
            schema_initialization_enabled=schema_initialization_enabled,
            database_configured=settings.is_configured,
        )

        if settings.is_configured:
            try:
                engine = create_engine(settings)
                app.state.db_engine = engine
                if schema_initialization_enabled:
                    await _initialize_schema_with_retry(engine, settings)
                else:
                    log_event(
                        logger,
                        "database_schema_initialization_skipped",
                        reason="disabled",
                    )
                app.state.session_factory = create_session_factory(engine)
                log_event(
                    logger,
                    "api_database_ready",
                    schema_initialization_enabled=schema_initialization_enabled,
                )
                log_deployment_event(
                    logger,
                    "backend_ready",
                    worker_id=None,
                    database_configured=True,
                    schema_initialization_enabled=schema_initialization_enabled,
                )
            except Exception as exc:
                app.state.db_initialization_error = exc
                log_error(
                    logger,
                    "api_database_startup_failed",
                    error_type=type(exc).__name__,
                )
        else:
            log_event(
                logger,
                "api_database_startup_skipped",
                persistence_enabled=settings.enabled,
                has_database_url=settings.url is not None,
            )
            log_deployment_event(
                logger,
                "backend_ready",
                worker_id=None,
                database_configured=False,
                schema_initialization_enabled=schema_initialization_enabled,
            )

        try:
            yield
        finally:
            engine = getattr(app.state, "db_engine", None)
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="AI Voice Receptionist Internal API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_exception_handler(HTTPException, http_exception_handler)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "api"}

    @app.get("/health", include_in_schema=False)
    async def health(request: Request) -> dict[str, object]:
        return {
            "status": "ok",
            "service": "api",
            "diagnostics": _diagnostics_service(request).environment_audit(),
        }

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> dict[str, object]:
        initialization_error = getattr(
            request.app.state,
            "db_initialization_error",
            None,
        )
        if initialization_error is not None:
            raise api_error(
                503,
                code="database_unavailable",
                message="Database is unavailable",
                details={"error_type": type(initialization_error).__name__},
            )

        settings = getattr(request.app.state, "database_settings", None)
        return {
            "status": "ready",
            "service": "api",
            "database_configured": bool(
                settings is not None and settings.is_configured
            ),
        }

    @app.get("/ready", include_in_schema=False)
    async def ready(request: Request) -> dict[str, object]:
        diagnostics = await _diagnostics_service(request).collect()
        if diagnostics["status"] != "ready":
            raise api_error(
                503,
                code="deployment_not_ready",
                message="Deployment dependencies are not ready",
                details=diagnostics,
            )
        return diagnostics

    @app.get("/live", include_in_schema=False)
    async def live(request: Request) -> dict[str, object]:
        diagnostics = await _diagnostics_service(request).collect()
        return {
            "status": "live" if diagnostics["status"] in {"ready", "degraded"} else "down",
            "service": "api",
            "uptime_seconds": diagnostics["uptime_seconds"],
            "runtime": diagnostics["runtime"],
            "alerts": diagnostics["alerts"],
        }

    @app.get("/internal/v1/deployment/diagnostics")
    async def deployment_diagnostics(request: Request) -> dict[str, object]:
        return await _diagnostics_service(request).collect()

    app.include_router(calls_router)
    app.include_router(bookings_router)
    app.include_router(transcripts_router)
    app.include_router(settings_router)
    return app


async def _initialize_schema_with_retry(
    engine: AsyncEngine,
    settings: DatabaseSettings,
) -> None:
    retry_attempts = settings.startup_retry_attempts
    retry_backoff_seconds = settings.startup_retry_backoff_seconds

    for attempt in range(1, retry_attempts + 1):
        try:
            await initialize_schema(engine)
            log_event(logger, "database_schema_ready", attempt=attempt)
            if attempt > 1:
                log_deployment_event(
                    logger,
                    "postgres_reconnected",
                    worker_id=None,
                    attempt=attempt,
                )
                log_deployment_event(
                    logger,
                    "deployment_recovery_completed",
                    worker_id=None,
                    recovered_dependency="postgres",
                    attempt=attempt,
                )
            return
        except Exception as exc:
            if attempt >= retry_attempts:
                log_event(
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
                retry_in_seconds=retry_backoff_seconds,
            )
            await asyncio.sleep(retry_backoff_seconds)


def _schema_initialization_enabled(initialize_database: bool | None) -> bool:
    if initialize_database is not None:
        return initialize_database
    return _bool_env("INITIALIZE_DATABASE_ON_STARTUP", True)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _diagnostics_service(request: Request) -> DeploymentDiagnosticsService:
    settings = getattr(request.app.state, "database_settings", None)
    if settings is None:
        settings = DatabaseSettings.from_env()
    return DeploymentDiagnosticsService(
        database_settings=settings,
        session_factory=getattr(request.app.state, "session_factory", None),
    )


app = create_app()
