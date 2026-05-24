from __future__ import annotations

import asyncio
import hashlib
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from database.repositories.notifications import NotificationDeliveryCreate
from voice_agent.booking import BookingConfirmationEvent
from voice_agent.fulfillment import NotificationFulfillmentState
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)

_MAX_FULFILLMENT_ATTEMPTS = 3
_FULFILLMENT_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_SUCCESSFUL_STATUSES = frozenset({"sent", "delivered"})
_TERMINAL_FAILURE_STATUSES = frozenset({"failed", "retry_exhausted"})


class SMSProvider(Protocol):
    async def send_sms(
        self,
        *,
        phone_number: str,
        message: str,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        ...


class NotificationDeliveryStore(Protocol):
    async def create_notification_delivery(
        self,
        payload: NotificationDeliveryCreate,
    ) -> object:
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

    async def mark_notification_retry_exhausted(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        ...


@dataclass(frozen=True)
class _BookingSMSWorkItem:
    event: BookingConfirmationEvent
    request_id: str | None
    idempotency_key: str
    notification_id: str


class BookingConfirmationNotificationOrchestrator:
    def __init__(
        self,
        *,
        sms_provider: SMSProvider | None,
        queue_max_items: int,
        drain_timeout_seconds: float,
        delivery_store: NotificationDeliveryStore | None = None,
        provider_name: str = "fast2sms",
        max_attempts: int = _MAX_FULFILLMENT_ATTEMPTS,
        retry_backoff_seconds: tuple[float, ...] = _FULFILLMENT_BACKOFF_SECONDS,
    ) -> None:
        self._sms_provider = sms_provider
        self._delivery_store = delivery_store
        self._provider_name = provider_name
        self._queue: asyncio.Queue[_BookingSMSWorkItem | None] = asyncio.Queue(
            maxsize=queue_max_items
        )
        self._drain_timeout_seconds = drain_timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._retry_backoff_seconds = retry_backoff_seconds
        self._active_keys: set[str] = set()
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False

    async def enqueue_booking_confirmation(
        self,
        event: BookingConfirmationEvent,
        request_id: str | None,
    ) -> NotificationFulfillmentState:
        key = _idempotency_key(event)
        booking_fingerprint = event.booking_fingerprint or key
        if self._closed:
            return self._failure_state(
                event,
                key=key,
                status="failed",
                reason="notification_orchestrator_closed",
                request_id=request_id,
            )
        if self._sms_provider is None:
            return self._failure_state(
                event,
                key=key,
                status="failed",
                reason="fast2sms_not_configured",
                request_id=request_id,
            )
        if self._delivery_store is None:
            return self._failure_state(
                event,
                key=key,
                status="failed",
                reason="notification_persistence_not_configured",
                request_id=request_id,
            )

        successful = await self._delivery_store.get_successful_notification_by_idempotency_key(key)
        if successful is not None:
            state = _state_from_model(successful, duplicate_prevented=True)
            log_event(
                logger,
                "notification_duplicate_prevented",
                request_id=request_id,
                notification_key=key,
                notification_id=state.notification_id,
                booking_uid=event.booking_uid,
                booking_fingerprint=booking_fingerprint,
                status=state.status,
            )
            return state

        model = await self._delivery_store.create_notification_delivery(
            NotificationDeliveryCreate(
                booking_fingerprint=booking_fingerprint,
                notification_type="booking_confirmation_sms",
                idempotency_key=key,
                provider=self._provider_name,
                fulfillment_language=event.fulfillment_language,
            )
        )
        state = _state_from_model(model)
        log_event(
            logger,
            "notification_persisted",
            request_id=request_id,
            notification_key=key,
            notification_id=state.notification_id,
            booking_uid=event.booking_uid,
            booking_fingerprint=booking_fingerprint,
            status=state.status,
            attempts=state.attempts,
            fulfillment_language=state.fulfillment_language,
        )

        if state.status in _SUCCESSFUL_STATUSES:
            log_event(
                logger,
                "notification_duplicate_prevented",
                request_id=request_id,
                notification_key=key,
                notification_id=state.notification_id,
                booking_uid=event.booking_uid,
                booking_fingerprint=booking_fingerprint,
                status=state.status,
            )
            return _state_from_model(model, duplicate_prevented=True)
        if state.status in _TERMINAL_FAILURE_STATUSES or state.attempts >= self._max_attempts:
            log_event(
                logger,
                "notification_retry_exhausted",
                request_id=request_id,
                notification_key=key,
                notification_id=state.notification_id,
                booking_uid=event.booking_uid,
                booking_fingerprint=booking_fingerprint,
                status=state.status,
                attempts=state.attempts,
            )
            return state
        if key in self._active_keys:
            log_event(
                logger,
                "notification_duplicate_prevented",
                request_id=request_id,
                notification_key=key,
                notification_id=state.notification_id,
                booking_uid=event.booking_uid,
                booking_fingerprint=booking_fingerprint,
                status=state.status,
                reason="already_active_in_worker",
            )
            return _state_from_model(model, duplicate_prevented=True)

        self._ensure_worker()
        try:
            self._queue.put_nowait(
                _BookingSMSWorkItem(
                    event=event,
                    request_id=request_id,
                    idempotency_key=key,
                    notification_id=state.notification_id or "",
                )
            )
        except asyncio.QueueFull:
            failed = await self._delivery_store.mark_notification_retry_exhausted(
                state.notification_id or "",
                error_detail="sms_queue_full",
            )
            log_event(
                logger,
                "notification_retry_exhausted",
                request_id=request_id,
                notification_key=key,
                notification_id=state.notification_id,
                booking_uid=event.booking_uid,
                booking_fingerprint=booking_fingerprint,
                reason="sms_queue_full",
                queue_maxsize=self._queue.maxsize,
            )
            return _state_from_model(failed) if failed is not None else state

        self._active_keys.add(key)
        log_event(
            logger,
            "notification_queued",
            request_id=request_id,
            notification_key=key,
            notification_id=state.notification_id,
            booking_uid=event.booking_uid,
            booking_fingerprint=booking_fingerprint,
            queue_size=self._queue.qsize(),
            attempts=state.attempts,
        )
        return state

    async def aclose(self) -> None:
        self._closed = True
        if self._worker_task is None:
            return

        await self._queue.put(None)
        try:
            await asyncio.wait_for(self._worker_task, timeout=self._drain_timeout_seconds)
        except asyncio.TimeoutError:
            self._worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._worker_task

    def _ensure_worker(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(
            self._run_worker(),
            name="booking-confirmation-sms-worker",
        )

    async def _run_worker(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                await self._deliver(item)
            finally:
                if item is not None:
                    self._active_keys.discard(item.idempotency_key)
                self._queue.task_done()

    async def _deliver(self, item: _BookingSMSWorkItem) -> None:
        if self._sms_provider is None or self._delivery_store is None:
            return

        message = build_booking_confirmation_sms(item.event)
        for _ in range(self._max_attempts):
            current = await self._delivery_store.get_notification_by_idempotency_key(
                item.idempotency_key
            )
            if current is None:
                log_event(
                    logger,
                    "notification_delivery_failed",
                    request_id=item.request_id,
                    notification_key=item.idempotency_key,
                    notification_id=item.notification_id,
                    booking_uid=item.event.booking_uid,
                    reason="notification_record_missing",
                )
                return
            current_state = _state_from_model(current)
            if current_state.status in _SUCCESSFUL_STATUSES:
                log_event(
                    logger,
                    "notification_duplicate_prevented",
                    request_id=item.request_id,
                    notification_key=item.idempotency_key,
                    notification_id=current_state.notification_id,
                    booking_uid=item.event.booking_uid,
                    status=current_state.status,
                    reason="successful_before_retry",
                )
                return
            if current_state.status in _TERMINAL_FAILURE_STATUSES:
                return
            if current_state.attempts >= self._max_attempts:
                await self._delivery_store.mark_notification_retry_exhausted(
                    item.notification_id,
                    error_detail="max_attempts_already_reached",
                )
                log_event(
                    logger,
                    "notification_retry_exhausted",
                    request_id=item.request_id,
                    notification_key=item.idempotency_key,
                    notification_id=item.notification_id,
                    booking_uid=item.event.booking_uid,
                    attempts=current_state.attempts,
                )
                return

            sending = await self._delivery_store.mark_notification_sending(
                item.notification_id
            )
            attempt = int(getattr(sending, "attempts", current_state.attempts + 1))
            started_at = time.perf_counter()
            try:
                result = await self._sms_provider.send_sms(
                    phone_number=item.event.phone_number,
                    message=message,
                    request_id=item.request_id,
                    idempotency_key=item.idempotency_key,
                )
            except Exception as exc:
                latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
                error_detail = f"{type(exc).__name__}: {exc}"
                log_event(
                    logger,
                    "notification_delivery_failed",
                    request_id=item.request_id,
                    notification_key=item.idempotency_key,
                    notification_id=item.notification_id,
                    booking_uid=item.event.booking_uid,
                    attempt=attempt,
                    max_attempts=self._max_attempts,
                    error_type=type(exc).__name__,
                    latency_ms=latency_ms,
                )
                if attempt >= self._max_attempts:
                    await self._delivery_store.mark_notification_retry_exhausted(
                        item.notification_id,
                        error_detail=error_detail,
                    )
                    log_event(
                        logger,
                        "notification_retry_exhausted",
                        request_id=item.request_id,
                        notification_key=item.idempotency_key,
                        notification_id=item.notification_id,
                        booking_uid=item.event.booking_uid,
                        attempts=attempt,
                    )
                    return
                await self._delivery_store.mark_notification_retrying(
                    item.notification_id,
                    error_detail=error_detail,
                )
                backoff = self._backoff_for_attempt(attempt)
                log_event(
                    logger,
                    "notification_retry_scheduled",
                    request_id=item.request_id,
                    notification_key=item.idempotency_key,
                    notification_id=item.notification_id,
                    booking_uid=item.event.booking_uid,
                    attempt=attempt,
                    next_attempt=attempt + 1,
                    backoff_seconds=backoff,
                )
                if backoff > 0:
                    await asyncio.sleep(backoff)
                continue

            provider_request_id = _provider_request_id(result)
            await self._delivery_store.mark_notification_sent(
                item.notification_id,
                provider_request_id=provider_request_id,
            )
            log_event(
                logger,
                "notification_sent",
                request_id=item.request_id,
                notification_key=item.idempotency_key,
                notification_id=item.notification_id,
                booking_uid=item.event.booking_uid,
                provider_request_id=provider_request_id,
                provider_acceptance_only=True,
                delivery_truth="provider_accepted_not_customer_delivered",
                attempt=attempt,
                max_attempts=self._max_attempts,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
            return

    def _backoff_for_attempt(self, attempt: int) -> float:
        index = max(0, attempt - 1)
        if index >= len(self._retry_backoff_seconds):
            return self._retry_backoff_seconds[-1] if self._retry_backoff_seconds else 0.0
        return self._retry_backoff_seconds[index]

    def _failure_state(
        self,
        event: BookingConfirmationEvent,
        *,
        key: str,
        status: str,
        reason: str,
        request_id: str | None,
    ) -> NotificationFulfillmentState:
        log_event(
            logger,
            "fulfillment_partial",
            request_id=request_id,
            notification_key=key,
            booking_uid=event.booking_uid,
            booking_fingerprint=event.booking_fingerprint,
            reason=reason,
            status=status,
        )
        return NotificationFulfillmentState(
            notification_id=None,
            booking_fingerprint=event.booking_fingerprint,
            idempotency_key=key,
            notification_type="booking_confirmation_sms",
            provider=self._provider_name,
            status=status,
            attempts=0,
            fulfillment_language=event.fulfillment_language,
            error_detail=reason,
            persisted=False,
        )


def build_booking_confirmation_sms(event: BookingConfirmationEvent) -> str:
    local_start = _local_datetime(event.appointment_start, event.time_zone)
    customer_name = _compact_text(event.customer_name)
    business_name = _compact_text(event.business_name)
    date_text = _format_date(local_start)
    time_text = _format_time(local_start.time())
    return (
        f"Hello {customer_name}, your appointment at {business_name} "
        f"is confirmed for {date_text} at {time_text}."
    )


def _state_from_model(
    model: object,
    *,
    duplicate_prevented: bool = False,
) -> NotificationFulfillmentState:
    return NotificationFulfillmentState(
        notification_id=_string_or_none(getattr(model, "notification_id", None)),
        booking_fingerprint=_string_or_none(getattr(model, "booking_fingerprint", None)),
        idempotency_key=str(getattr(model, "idempotency_key", "")),
        notification_type=str(getattr(model, "notification_type", "booking_confirmation_sms")),
        provider=str(getattr(model, "provider", "unknown")),
        provider_request_id=_string_or_none(getattr(model, "provider_request_id", None)),
        status=str(getattr(model, "status", "unknown")),
        attempts=int(getattr(model, "attempts", 0) or 0),
        fulfillment_language=_string_or_none(getattr(model, "fulfillment_language", None)),
        error_detail=_string_or_none(getattr(model, "error_detail", None)),
        persisted=True,
        duplicate_prevented=duplicate_prevented,
    )


def _provider_request_id(result: object) -> str | None:
    return _string_or_none(getattr(result, "request_id", None))


def _idempotency_key(event: BookingConfirmationEvent) -> str:
    if event.booking_uid:
        return f"calcom:{event.booking_uid}:booking_confirmation_sms"
    if event.booking_fingerprint:
        return f"booking:{event.booking_fingerprint}:booking_confirmation_sms"
    raw = "|".join(
        (
            event.business_name,
            re.sub(r"\D", "", event.phone_number),
            event.appointment_start.isoformat(),
        )
    )
    return "booking:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _local_datetime(value: datetime, time_zone: str) -> datetime:
    tz = _timezone_for(time_zone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _timezone_for(name: str) -> timezone:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Kolkata", "Asia/Calcutta"}:
            return timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
        return timezone.utc


def _format_date(value: datetime) -> str:
    return f"{value.day} {value.strftime('%b %Y')}"


def _format_time(value: clock_time) -> str:
    hour = value.hour
    minute = value.minute
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    if minute:
        return f"{display_hour}:{minute:02d} {suffix}"
    return f"{display_hour} {suffix}"


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _string_or_none(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
