from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request

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
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


def create_app(
    *,
    database_settings: DatabaseSettings | None = None,
    initialize_database: bool = False,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = database_settings or DatabaseSettings.from_env()
        app.state.database_settings = settings
        app.state.db_engine = None
        app.state.session_factory = None
        app.state.db_initialization_error = None

        if settings.is_configured:
            try:
                engine = create_engine(settings)
                app.state.db_engine = engine
                app.state.session_factory = create_session_factory(engine)
                if initialize_database:
                    await initialize_schema(engine)
            except Exception as exc:
                app.state.db_initialization_error = exc
                log_event(
                    logger,
                    "db_write_failed",
                    event_type="api_database_startup",
                    error_type=type(exc).__name__,
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

    app.include_router(calls_router)
    app.include_router(bookings_router)
    app.include_router(transcripts_router)
    app.include_router(settings_router)
    return app


app = create_app()
