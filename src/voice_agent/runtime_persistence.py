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
        return bool(method(request_id=request_id, **kwargs))
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
