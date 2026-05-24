from __future__ import annotations

from datetime import datetime
from typing import Protocol

from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


class RuntimePersistenceSink(Protocol):
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
        ...

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
        ...

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
        ...

    def enqueue_booking_confirmed(
        self,
        *,
        memory: object,
        confirmation_status: str = "confirmed",
        request_id: str | None = None,
    ) -> bool:
        ...

    def enqueue_recording_metadata(
        self,
        *,
        call_id: str,
        file_path: str,
        duration_seconds: int | None = None,
        created_at: datetime | None = None,
        request_id: str | None = None,
    ) -> bool:
        ...

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
        ...

    async def find_booking_by_fingerprint(self, fingerprint: str) -> object | None:
        ...

    async def reserve_booking(
        self,
        *,
        fingerprint: str,
        memory: object,
        request_id: str | None = None,
    ) -> bool:
        ...

    async def persist_booking_confirmed(
        self,
        *,
        fingerprint: str,
        memory: object,
        calcom_uid: str,
        external_status: str | None,
        booking_time: datetime,
        validation_state: str,
        request_id: str | None = None,
    ) -> bool:
        ...

    async def create_notification_delivery(self, payload: object) -> object:
        ...

    async def get_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        ...

    async def get_successful_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        ...

    async def list_notifications_by_booking_fingerprint(
        self,
        booking_fingerprint: str,
    ) -> list[object]:
        ...

    async def mark_notification_sending(self, notification_id: str) -> object | None:
        ...

    async def mark_notification_sent(
        self,
        notification_id: str,
        *,
        provider_request_id: str | None,
    ) -> object | None:
        ...

    async def mark_notification_retrying(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        ...

    async def mark_notification_failed(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        ...

    async def mark_notification_retry_exhausted(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        ...


def safe_enqueue(
    sink: RuntimePersistenceSink | None,
    method_name: str,
    *,
    request_id: str | None = None,
    **kwargs: object,
) -> bool:
    if sink is None:
        return True
    try:
        method = getattr(sink, method_name)
        queued = bool(method(request_id=request_id, **kwargs))
        if not queued:
            log_event(
                logger,
                "persistence_enqueue_dropped",
                event_type=method_name,
                request_id=request_id,
                reason="sink_rejected_event",
            )
        return queued
    except Exception as exc:
        log_event(
            logger,
            "db_write_failed",
            event_type=method_name,
            request_id=request_id,
            reason="persistence_enqueue_failed",
            error_type=type(exc).__name__,
        )
        return False
