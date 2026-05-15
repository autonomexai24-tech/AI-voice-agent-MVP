from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.errors import api_error
from database.repositories.bookings import BookingRepository
from database.repositories.business_settings import BusinessSettingsRepository
from database.repositories.calls import CallRepository
from database.repositories.transcripts import TranscriptRepository
from database.session import session_scope


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    initialization_error = getattr(request.app.state, "db_initialization_error", None)
    if initialization_error is not None:
        raise api_error(
            503,
            code="database_unavailable",
            message="Database is unavailable",
            details={"error_type": type(initialization_error).__name__},
        )
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        raise api_error(
            503,
            code="database_unavailable",
            message="Database session factory is not initialized",
        )
    async with session_scope(session_factory) as session:
        yield session


async def get_call_repository(
    session: AsyncSession = Depends(get_db_session),
) -> CallRepository:
    return CallRepository(session)


async def get_booking_repository(
    session: AsyncSession = Depends(get_db_session),
) -> BookingRepository:
    return BookingRepository(session)


async def get_transcript_repository(
    session: AsyncSession = Depends(get_db_session),
) -> TranscriptRepository:
    return TranscriptRepository(session)


async def get_business_settings_repository(
    session: AsyncSession = Depends(get_db_session),
) -> BusinessSettingsRepository:
    return BusinessSettingsRepository(session)
