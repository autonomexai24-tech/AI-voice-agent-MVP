from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.repositories.bookings import BookingCreate, BookingRepository
from database.repositories.business_settings import (
    BusinessSettingsRepository,
    BusinessSettingsUpdate,
)
from database.repositories.calls import CallCreate, CallRepository
from database.repositories.recordings import (
    RecordingMetadataCreate,
    RecordingMetadataRepository,
)
from database.repositories.transcripts import TranscriptCreate, TranscriptRepository
from database.session import DatabaseSettings, create_engine, create_session_factory, session_scope
from voice_agent.booking.entities import BookingField
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


@dataclass(frozen=True)
class PersistenceEnvelope:
    event_type: str
    payload: object
    request_id: str | None = None


class PersistenceWriter(Protocol):
    async def write(self, envelope: PersistenceEnvelope) -> None:
        ...


class RuntimePersistenceService:
    def __init__(
        self,
        writer: PersistenceWriter,
        *,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.05,
        queue_max_items: int = 500,
    ) -> None:
        self._writer = writer
        self._retry_attempts = retry_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._queue: asyncio.Queue[PersistenceEnvelope | None] = asyncio.Queue(
            maxsize=queue_max_items
        )
        self._worker: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._worker = asyncio.create_task(self._run(), name="runtime-persistence-writer")

    async def stop(self, *, drain_timeout_seconds: float = 2.0) -> None:
        if self._worker is None:
            return
        try:
            await self._queue.put(None)
            if drain_timeout_seconds > 0:
                await asyncio.wait_for(self._worker, timeout=drain_timeout_seconds)
            else:
                await self._worker
        except asyncio.TimeoutError:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        finally:
            self._worker = None

    def enqueue_call_started(
        self,
        *,
        call_id: str,
        room_id: str | None = None,
        caller_phone: str | None = None,
        language: str | None = None,
        started_at: datetime | None = None,
        request_id: str | None = None,
    ) -> bool:
        return self._enqueue(
            PersistenceEnvelope(
                "call_started",
                CallCreate(
                    call_id=call_id,
                    room_id=room_id,
                    caller_phone=caller_phone,
                    language=language,
                    started_at=started_at or _utc_now(),
                ),
                request_id=request_id,
            )
        )

    def enqueue_call_ended(
        self,
        *,
        call_id: str,
        ended_at: datetime | None = None,
        duration_seconds: int | None = None,
        booking_outcome: str | None = None,
        escalation_triggered: bool = False,
        request_id: str | None = None,
    ) -> bool:
        return self._enqueue(
            PersistenceEnvelope(
                "call_ended",
                CallCreate(
                    call_id=call_id,
                    ended_at=ended_at or _utc_now(),
                    duration_seconds=duration_seconds,
                    booking_outcome=booking_outcome,
                    escalation_triggered=escalation_triggered,
                ),
                request_id=request_id,
            )
        )

    def enqueue_transcript(
        self,
        *,
        call_id: str,
        speaker: str,
        text: str,
        language: str | None = None,
        timestamp: datetime | None = None,
        request_id: str | None = None,
    ) -> bool:
        if not text.strip():
            return True
        return self._enqueue(
            PersistenceEnvelope(
                "transcript",
                TranscriptCreate(
                    call_id=call_id,
                    speaker=speaker,
                    text=text.strip(),
                    timestamp=timestamp or _utc_now(),
                    language=language,
                ),
                request_id=request_id,
            )
        )

    def enqueue_booking_confirmed(
        self,
        *,
        memory: object,
        confirmation_status: str = "confirmed",
        request_id: str | None = None,
    ) -> bool:
        booking_values = memory.booking_values()
        return self._enqueue(
            PersistenceEnvelope(
                "booking_confirmed",
                BookingCreate(
                    call_id=memory.session_id,
                    customer_name=booking_values.get(BookingField.CUSTOMER_NAME),
                    phone_number=booking_values.get(BookingField.PHONE_NUMBER),
                    service_type=booking_values.get(BookingField.SERVICE_TYPE),
                    appointment_date=booking_values.get(BookingField.APPOINTMENT_DATE),
                    appointment_time=booking_values.get(BookingField.APPOINTMENT_TIME),
                    doctor_preference=booking_values.get(BookingField.DOCTOR_PREFERENCE),
                    notes=booking_values.get(BookingField.NOTES),
                    confirmation_status=confirmation_status,
                ),
                request_id=request_id,
            )
        )

    def enqueue_recording_metadata(
        self,
        *,
        call_id: str,
        file_path: str,
        duration_seconds: int | None = None,
        created_at: datetime | None = None,
        request_id: str | None = None,
    ) -> bool:
        return self._enqueue(
            PersistenceEnvelope(
                "recording_metadata",
                RecordingMetadataCreate(
                    call_id=call_id,
                    file_path=file_path,
                    duration_seconds=duration_seconds,
                    created_at=created_at or _utc_now(),
                ),
                request_id=request_id,
            )
        )

    def _enqueue(self, envelope: PersistenceEnvelope) -> bool:
        try:
            self._queue.put_nowait(envelope)
            return True
        except asyncio.QueueFull:
            log_event(
                logger,
                "db_write_failed",
                event_type=envelope.event_type,
                request_id=envelope.request_id,
                reason="persistence_queue_full",
            )
            return False

    async def _run(self) -> None:
        while True:
            envelope = await self._queue.get()
            try:
                if envelope is None:
                    return
                await self._write_with_retry(envelope)
            finally:
                self._queue.task_done()

    async def _write_with_retry(self, envelope: PersistenceEnvelope) -> None:
        max_attempts = self._retry_attempts + 1
        for attempt in range(1, max_attempts + 1):
            try:
                await self._writer.write(envelope)
                return
            except Exception as exc:
                if attempt < max_attempts:
                    log_event(
                        logger,
                        "persistence_retry_triggered",
                        event_type=envelope.event_type,
                        request_id=envelope.request_id,
                        attempt=attempt,
                        error_type=type(exc).__name__,
                    )
                    if self._retry_backoff_seconds > 0:
                        await asyncio.sleep(self._retry_backoff_seconds)
                    continue
                log_event(
                    logger,
                    "db_write_failed",
                    event_type=envelope.event_type,
                    request_id=envelope.request_id,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                return


class RepositoryPersistenceWriter:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def write(self, envelope: PersistenceEnvelope) -> None:
        async with session_scope(self._session_factory) as session:
            if isinstance(envelope.payload, CallCreate):
                model = await CallRepository(session).create_or_update(envelope.payload)
                log_event(
                    logger,
                    "call_persisted",
                    request_id=envelope.request_id,
                    call_id=model.call_id,
                    room_id=model.room_id,
                    booking_outcome=model.booking_outcome,
                )
                return

            if isinstance(envelope.payload, BookingCreate):
                model = await BookingRepository(session).create_or_update(envelope.payload)
                log_event(
                    logger,
                    "booking_persisted",
                    request_id=envelope.request_id,
                    booking_id=model.booking_id,
                    call_id=model.call_id,
                    confirmation_status=model.confirmation_status,
                )
                return

            if isinstance(envelope.payload, TranscriptCreate):
                model = await TranscriptRepository(session).create(envelope.payload)
                log_event(
                    logger,
                    "transcript_persisted",
                    request_id=envelope.request_id,
                    transcript_id=model.transcript_id,
                    call_id=model.call_id,
                    speaker=model.speaker,
                    language=model.language,
                )
                return

            if isinstance(envelope.payload, RecordingMetadataCreate):
                model = await RecordingMetadataRepository(session).create(envelope.payload)
                log_event(
                    logger,
                    "recording_metadata_persisted",
                    request_id=envelope.request_id,
                    recording_id=model.recording_id,
                    call_id=model.call_id,
                )
                return

            if isinstance(envelope.payload, BusinessSettingsUpdate):
                model = await BusinessSettingsRepository(session).upsert(envelope.payload)
                log_event(
                    logger,
                    "settings_updated",
                    request_id=envelope.request_id,
                    settings_id=model.settings_id,
                    services_count=len(model.services),
                )
                return

            raise TypeError(f"Unsupported persistence payload: {type(envelope.payload).__name__}")


def build_runtime_persistence_service(
    settings: DatabaseSettings,
    *,
    engine_factory: Callable[[DatabaseSettings], object] | None = None,
) -> RuntimePersistenceService | None:
    if not settings.is_configured:
        return None
    factory = engine_factory or create_engine
    engine = factory(settings)
    session_factory = create_session_factory(engine)
    return RuntimePersistenceService(
        RepositoryPersistenceWriter(session_factory),
        retry_attempts=settings.retry_attempts,
        retry_backoff_seconds=settings.retry_backoff_seconds,
        queue_max_items=settings.queue_max_items,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
