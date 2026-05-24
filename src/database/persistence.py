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
from database.repositories.notifications import (
    NotificationDeliveryCreate,
    NotificationDeliveryRepository,
)
from database.repositories.recordings import (
    RecordingMetadataCreate,
    RecordingMetadataRepository,
)
from database.repositories.transcripts import TranscriptCreate, TranscriptRepository
from database.repositories.workers import WorkerHeartbeatCreate, WorkerHeartbeatRepository
from database.session import DatabaseSettings, create_engine, create_session_factory, session_scope
from voice_agent.booking.entities import BookingField
from voice_agent.session_memory import CallSessionMemory
from voice_agent.logging_config import get_logger, log_event
from voice_agent.runtime_metrics import DEPLOYMENT_METRICS, record_infrastructure_alert

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
        self._queue_capacity = queue_max_items
        self._high_watermark_ratio = 0.8
        self._enqueued_count = 0
        self._completed_count = 0
        self._failed_count = 0
        self._retry_count = 0
        self._dropped_count = 0
        self._max_queue_depth = 0
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
        log_event(
            logger,
            "persistence_queue_drain_started",
            queue_size=self._queue.qsize(),
            queue_capacity=self._queue_capacity,
            enqueued_count=self._enqueued_count,
            completed_count=self._completed_count,
            failed_count=self._failed_count,
            dropped_count=self._dropped_count,
            retry_count=self._retry_count,
            drain_timeout_seconds=drain_timeout_seconds,
        )
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
            log_event(
                logger,
                "persistence_queue_drain_timeout",
                queue_size=self._queue.qsize(),
                queue_capacity=self._queue_capacity,
                drain_timeout_seconds=drain_timeout_seconds,
            )
        finally:
            self._worker = None
            log_event(
                logger,
                "persistence_queue_drain_completed",
                queue_size=self._queue.qsize(),
                queue_capacity=self._queue_capacity,
                enqueued_count=self._enqueued_count,
                completed_count=self._completed_count,
                failed_count=self._failed_count,
                dropped_count=self._dropped_count,
                retry_count=self._retry_count,
                max_queue_depth=self._max_queue_depth,
            )

    def enqueue_call_started(
        self,
        *,
        call_id: str,
        room_id: str | None = None,
        caller_phone: str | None = None,
        language: str | None = None,
        started_at: datetime | None = None,
        status: str | None = None,
        worker_id: str | None = None,
        last_lifecycle_event: str | None = None,
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
                    status=status,
                    worker_id=worker_id,
                    last_lifecycle_event=last_lifecycle_event,
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
        status: str | None = None,
        worker_id: str | None = None,
        termination_reason: str | None = None,
        last_lifecycle_event: str | None = None,
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
                    status=status,
                    worker_id=worker_id,
                    termination_reason=termination_reason,
                    last_lifecycle_event=last_lifecycle_event,
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

    async def find_booking_by_fingerprint(self, fingerprint: str) -> object | None:
        method = getattr(self._writer, "find_booking_by_fingerprint", None)
        if method is None:
            return None
        return await method(fingerprint)

    async def reserve_booking(
        self,
        *,
        fingerprint: str,
        memory: CallSessionMemory,
        request_id: str | None = None,
    ) -> bool:
        method = getattr(self._writer, "reserve_booking", None)
        if method is None:
            log_event(
                logger,
                "booking_failed",
                request_id=request_id,
                session_id=memory.session_id,
                booking_fingerprint=fingerprint,
                reason="booking_runtime_store_missing",
            )
            return False
        return bool(
            await method(
                fingerprint=fingerprint,
                memory=memory,
                request_id=request_id,
            )
        )

    async def persist_booking_confirmed(
        self,
        *,
        fingerprint: str,
        memory: CallSessionMemory,
        calcom_uid: str,
        external_status: str | None,
        booking_time: datetime,
        validation_state: str,
        request_id: str | None = None,
    ) -> bool:
        method = getattr(self._writer, "persist_booking_confirmed", None)
        if method is None:
            log_event(
                logger,
                "booking_failed",
                request_id=request_id,
                session_id=memory.session_id,
                booking_fingerprint=fingerprint,
                reason="booking_runtime_store_missing",
            )
            return False
        return bool(
            await method(
                fingerprint=fingerprint,
                memory=memory,
                calcom_uid=calcom_uid,
                external_status=external_status,
                booking_time=booking_time,
                validation_state=validation_state,
                request_id=request_id,
            )
        )

    async def create_notification_delivery(
        self,
        payload: NotificationDeliveryCreate,
    ) -> object:
        method = getattr(self._writer, "create_notification_delivery", None)
        if method is None:
            raise RuntimeError("notification_delivery_store_missing")
        return await method(payload)

    async def get_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        method = getattr(self._writer, "get_notification_by_idempotency_key", None)
        if method is None:
            return None
        return await method(idempotency_key)

    async def get_successful_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        method = getattr(self._writer, "get_successful_notification_by_idempotency_key", None)
        if method is None:
            return None
        return await method(idempotency_key)

    async def list_notifications_by_booking_fingerprint(
        self,
        booking_fingerprint: str,
    ) -> list[object]:
        method = getattr(self._writer, "list_notifications_by_booking_fingerprint", None)
        if method is None:
            return []
        return list(await method(booking_fingerprint))

    async def mark_notification_sending(self, notification_id: str) -> object | None:
        method = getattr(self._writer, "mark_notification_sending", None)
        if method is None:
            return None
        return await method(notification_id)

    async def mark_notification_sent(
        self,
        notification_id: str,
        *,
        provider_request_id: str | None,
    ) -> object | None:
        method = getattr(self._writer, "mark_notification_sent", None)
        if method is None:
            return None
        return await method(notification_id, provider_request_id=provider_request_id)

    async def mark_notification_retrying(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        method = getattr(self._writer, "mark_notification_retrying", None)
        if method is None:
            return None
        return await method(notification_id, error_detail=error_detail)

    async def mark_notification_failed(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        method = getattr(self._writer, "mark_notification_failed", None)
        if method is None:
            return None
        return await method(notification_id, error_detail=error_detail)

    async def mark_notification_retry_exhausted(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        method = getattr(self._writer, "mark_notification_retry_exhausted", None)
        if method is None:
            return None
        return await method(notification_id, error_detail=error_detail)

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

    def enqueue_worker_heartbeat(
        self,
        *,
        worker_id: str,
        active_session_count: int,
        uptime_seconds: int,
        current_calls: tuple[str, ...],
        memory_usage_mb: float | None = None,
        cpu_usage_percent: float | None = None,
        status: str = "running",
        request_id: str | None = None,
    ) -> bool:
        return self._enqueue(
            PersistenceEnvelope(
                "worker_heartbeat",
                WorkerHeartbeatCreate(
                    worker_id=worker_id,
                    active_session_count=active_session_count,
                    uptime_seconds=uptime_seconds,
                    current_calls=current_calls,
                    memory_usage_mb=memory_usage_mb,
                    cpu_usage_percent=cpu_usage_percent,
                    status=status,
                ),
                request_id=request_id,
            )
        )

    def _enqueue(self, envelope: PersistenceEnvelope) -> bool:
        try:
            self._queue.put_nowait(envelope)
            self._enqueued_count += 1
            queue_depth = self._queue.qsize()
            self._max_queue_depth = max(self._max_queue_depth, queue_depth)
            if queue_depth >= max(1, int(self._queue_capacity * self._high_watermark_ratio)):
                queue_usage_ratio = round(queue_depth / max(self._queue_capacity, 1), 3)
                DEPLOYMENT_METRICS.increment("persistence_queue_pressure")
                DEPLOYMENT_METRICS.set_gauge("queue_depth", queue_depth)
                DEPLOYMENT_METRICS.set_gauge("queue_capacity", self._queue_capacity)
                DEPLOYMENT_METRICS.set_gauge("queue_usage_ratio", queue_usage_ratio)
                record_infrastructure_alert(
                    logger,
                    "queue_pressure_high",
                    event_type=envelope.event_type,
                    request_id=envelope.request_id,
                    queue_size=queue_depth,
                    queue_capacity=self._queue_capacity,
                    queue_usage_ratio=queue_usage_ratio,
                )
                log_event(
                    logger,
                    "persistence_queue_pressure",
                    event_type=envelope.event_type,
                    request_id=envelope.request_id,
                    queue_size=queue_depth,
                    queue_capacity=self._queue_capacity,
                    queue_usage_ratio=queue_usage_ratio,
                    high_watermark_ratio=self._high_watermark_ratio,
                )
            return True
        except asyncio.QueueFull:
            self._dropped_count += 1
            DEPLOYMENT_METRICS.increment("queue_events_dropped")
            DEPLOYMENT_METRICS.set_gauge("queue_depth", self._queue.qsize())
            DEPLOYMENT_METRICS.set_gauge("queue_capacity", self._queue_capacity)
            record_infrastructure_alert(
                logger,
                "queue_events_dropped",
                event_type=envelope.event_type,
                request_id=envelope.request_id,
                reason="persistence_queue_full",
                queue_size=self._queue.qsize(),
                queue_capacity=self._queue_capacity,
                dropped_count=self._dropped_count,
            )
            log_event(
                logger,
                "persistence_event_dropped",
                event_type=envelope.event_type,
                request_id=envelope.request_id,
                reason="persistence_queue_full",
                queue_size=self._queue.qsize(),
                queue_capacity=self._queue_capacity,
                dropped_count=self._dropped_count,
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
                self._completed_count += 1
                if attempt > 1:
                    DEPLOYMENT_METRICS.increment("reconnect_count")
                    record_infrastructure_alert(
                        logger,
                        "postgres_reconnect",
                        severity="info",
                        event_type=envelope.event_type,
                        request_id=envelope.request_id,
                        attempt=attempt,
                    )
                    log_event(
                        logger,
                        "postgres_reconnected",
                        event_type=envelope.event_type,
                        request_id=envelope.request_id,
                        attempt=attempt,
                    )
                log_event(
                    logger,
                    "persistence_write_completed",
                    event_type=envelope.event_type,
                    request_id=envelope.request_id,
                    attempt=attempt,
                    queue_size=self._queue.qsize(),
                    completed_count=self._completed_count,
                )
                return
            except Exception as exc:
                if attempt < max_attempts:
                    self._retry_count += 1
                    DEPLOYMENT_METRICS.increment("retry_count")
                    log_event(
                        logger,
                        "persistence_retry_triggered",
                        event_type=envelope.event_type,
                        request_id=envelope.request_id,
                        attempt=attempt,
                        error_type=type(exc).__name__,
                        retry_count=self._retry_count,
                        queue_size=self._queue.qsize(),
                    )
                    if self._retry_backoff_seconds > 0:
                        await asyncio.sleep(self._retry_backoff_seconds)
                    continue
                self._failed_count += 1
                record_infrastructure_alert(
                    logger,
                    "postgres_unavailable",
                    severity="critical",
                    event_type=envelope.event_type,
                    request_id=envelope.request_id,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    failed_count=self._failed_count,
                )
                log_event(
                    logger,
                    "db_write_failed",
                    event_type=envelope.event_type,
                    request_id=envelope.request_id,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    failed_count=self._failed_count,
                    queue_size=self._queue.qsize(),
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
                    status=model.status,
                    worker_id=model.worker_id,
                    last_lifecycle_event=model.last_lifecycle_event,
                    booking_outcome=model.booking_outcome,
                )
                return

            if isinstance(envelope.payload, WorkerHeartbeatCreate):
                model = await WorkerHeartbeatRepository(session).upsert(envelope.payload)
                log_event(
                    logger,
                    "worker_heartbeat_persisted",
                    request_id=envelope.request_id,
                    worker_id=model.worker_id,
                    active_session_count=model.active_session_count,
                    current_calls=list(model.current_calls),
                    uptime_seconds=model.uptime_seconds,
                    memory_usage_mb=model.memory_usage_mb,
                    cpu_usage_percent=model.cpu_usage_percent,
                    status=model.status,
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

    async def find_booking_by_fingerprint(self, fingerprint: str) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await BookingRepository(session).get_by_fingerprint(fingerprint)

    async def reserve_booking(
        self,
        *,
        fingerprint: str,
        memory: CallSessionMemory,
        request_id: str | None = None,
    ) -> bool:
        booking_values = memory.booking_values()
        async with session_scope(self._session_factory) as session:
            reserved = await BookingRepository(session).reserve_by_fingerprint(
                BookingCreate(
                    call_id=memory.session_id,
                    customer_name=booking_values.get(BookingField.CUSTOMER_NAME),
                    phone_number=booking_values.get(BookingField.PHONE_NUMBER),
                    service_type=booking_values.get(BookingField.SERVICE_TYPE),
                    appointment_date=booking_values.get(BookingField.APPOINTMENT_DATE),
                    appointment_time=booking_values.get(BookingField.APPOINTMENT_TIME),
                    doctor_preference=booking_values.get(BookingField.DOCTOR_PREFERENCE),
                    notes=booking_values.get(BookingField.NOTES),
                    booking_fingerprint=fingerprint,
                    booking_validation_state="reserved",
                    confirmation_status="booking_in_progress",
                )
            )
        log_event(
            logger,
            "booking_persisted",
            request_id=request_id,
            call_id=memory.session_id,
            booking_fingerprint=fingerprint,
            confirmation_status="booking_in_progress",
            reserved=reserved,
        )
        return reserved

    async def persist_booking_confirmed(
        self,
        *,
        fingerprint: str,
        memory: CallSessionMemory,
        calcom_uid: str,
        external_status: str | None,
        booking_time: datetime,
        validation_state: str,
        request_id: str | None = None,
    ) -> bool:
        booking_values = memory.booking_values()
        async with session_scope(self._session_factory) as session:
            model = await BookingRepository(session).get_by_fingerprint(fingerprint)
            booking_id = model.booking_id if model is not None else None
            model = await BookingRepository(session).create_or_update(
                BookingCreate(
                    booking_id=booking_id,
                    call_id=memory.session_id,
                    customer_name=booking_values.get(BookingField.CUSTOMER_NAME),
                    phone_number=booking_values.get(BookingField.PHONE_NUMBER),
                    service_type=booking_values.get(BookingField.SERVICE_TYPE),
                    appointment_date=booking_values.get(BookingField.APPOINTMENT_DATE),
                    appointment_time=booking_values.get(BookingField.APPOINTMENT_TIME),
                    booking_time=booking_time,
                    doctor_preference=booking_values.get(BookingField.DOCTOR_PREFERENCE),
                    notes=booking_values.get(BookingField.NOTES),
                    booking_fingerprint=fingerprint,
                    calcom_uid=calcom_uid,
                    external_status=external_status,
                    confirmed_at=_utc_now(),
                    booking_validation_state=validation_state,
                    confirmation_status="confirmed",
                )
            )
        log_event(
            logger,
            "booking_persisted",
            request_id=request_id,
            booking_id=model.booking_id,
            call_id=model.call_id,
            booking_fingerprint=fingerprint,
            calcom_uid=calcom_uid,
            external_status=external_status,
            booking_validation_state=validation_state,
            confirmation_status="confirmed",
        )
        return True

    async def create_notification_delivery(
        self,
        payload: NotificationDeliveryCreate,
    ) -> object:
        async with session_scope(self._session_factory) as session:
            model = await NotificationDeliveryRepository(session).create_queued(payload)
        log_event(
            logger,
            "notification_persisted",
            notification_id=getattr(model, "notification_id", None),
            notification_key=payload.idempotency_key,
            booking_fingerprint=payload.booking_fingerprint,
            notification_type=payload.notification_type,
            provider=payload.provider,
            status=getattr(model, "status", None),
            attempts=getattr(model, "attempts", None),
        )
        return model

    async def get_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(session).get_by_idempotency_key(
                idempotency_key
            )

    async def get_successful_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(
                session
            ).get_successful_by_idempotency_key(idempotency_key)

    async def list_notifications_by_booking_fingerprint(
        self,
        booking_fingerprint: str,
    ) -> list[object]:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(
                session
            ).list_by_booking_fingerprint(booking_fingerprint)

    async def mark_notification_sending(self, notification_id: str) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(session).mark_sending(notification_id)

    async def mark_notification_sent(
        self,
        notification_id: str,
        *,
        provider_request_id: str | None,
    ) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(session).mark_sent(
                notification_id,
                provider_request_id=provider_request_id,
            )

    async def mark_notification_retrying(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(session).mark_retrying(
                notification_id,
                error_detail=error_detail,
            )

    async def mark_notification_failed(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(session).mark_failed(
                notification_id,
                error_detail=error_detail,
            )

    async def mark_notification_retry_exhausted(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        async with session_scope(self._session_factory) as session:
            return await NotificationDeliveryRepository(session).mark_retry_exhausted(
                notification_id,
                error_detail=error_detail,
            )


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
