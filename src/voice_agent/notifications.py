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

from voice_agent.booking import BookingConfirmationEvent
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


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


@dataclass(frozen=True)
class _BookingSMSWorkItem:
    event: BookingConfirmationEvent
    request_id: str | None
    idempotency_key: str


class BookingConfirmationNotificationOrchestrator:
    def __init__(
        self,
        *,
        sms_provider: SMSProvider | None,
        queue_max_items: int,
        drain_timeout_seconds: float,
    ) -> None:
        self._sms_provider = sms_provider
        self._queue: asyncio.Queue[_BookingSMSWorkItem | None] = asyncio.Queue(
            maxsize=queue_max_items
        )
        self._drain_timeout_seconds = drain_timeout_seconds
        self._seen_keys: set[str] = set()
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False

    async def enqueue_booking_confirmation(
        self,
        event: BookingConfirmationEvent,
        request_id: str | None,
    ) -> bool:
        key = _idempotency_key(event)
        if key in self._seen_keys:
            log_event(
                logger,
                "booking_sms_duplicate_prevented",
                request_id=request_id,
                notification_key=key,
                booking_uid=event.booking_uid,
            )
            return False
        self._seen_keys.add(key)

        if self._closed:
            log_event(
                logger,
                "booking_sms_delivery_failed",
                request_id=request_id,
                notification_key=key,
                booking_uid=event.booking_uid,
                reason="notification_orchestrator_closed",
            )
            return False

        if self._sms_provider is None:
            log_event(
                logger,
                "booking_sms_delivery_failed",
                request_id=request_id,
                notification_key=key,
                booking_uid=event.booking_uid,
                reason="fast2sms_not_configured",
            )
            return False

        self._ensure_worker()
        try:
            self._queue.put_nowait(
                _BookingSMSWorkItem(
                    event=event,
                    request_id=request_id,
                    idempotency_key=key,
                )
            )
        except asyncio.QueueFull:
            log_event(
                logger,
                "booking_sms_delivery_failed",
                request_id=request_id,
                notification_key=key,
                booking_uid=event.booking_uid,
                reason="sms_queue_full",
                queue_maxsize=self._queue.maxsize,
            )
            return False

        log_event(
            logger,
            "booking_sms_queued",
            request_id=request_id,
            notification_key=key,
            booking_uid=event.booking_uid,
            queue_size=self._queue.qsize(),
        )
        return True

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
                self._queue.task_done()

    async def _deliver(self, item: _BookingSMSWorkItem) -> None:
        if self._sms_provider is None:
            return

        message = build_booking_confirmation_sms(item.event)
        started_at = time.perf_counter()
        try:
            await self._sms_provider.send_sms(
                phone_number=item.event.phone_number,
                message=message,
                request_id=item.request_id,
                idempotency_key=item.idempotency_key,
            )
        except Exception as exc:
            log_event(
                logger,
                "booking_sms_delivery_failed",
                request_id=item.request_id,
                notification_key=item.idempotency_key,
                booking_uid=item.event.booking_uid,
                error_type=type(exc).__name__,
                latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
            )
            return

        log_event(
            logger,
            "booking_sms_delivery_succeeded",
            request_id=item.request_id,
            notification_key=item.idempotency_key,
            booking_uid=item.event.booking_uid,
            latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
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


def _idempotency_key(event: BookingConfirmationEvent) -> str:
    if event.booking_uid:
        return f"calcom:{event.booking_uid}"
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
