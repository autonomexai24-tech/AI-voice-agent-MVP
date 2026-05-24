from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from database.repositories.notifications import NotificationDeliveryCreate
from voice_agent.booking import BookingConfirmationEvent
from voice_agent.fulfillment import NotificationFulfillmentState
from voice_agent.notifications import (
    BookingConfirmationNotificationOrchestrator,
    build_booking_confirmation_sms,
)
from voice_agent.validation import RuntimeIntegrityValidator


def test_booking_confirmation_sms_is_short_and_includes_required_fields() -> None:
    event = _event()

    message = build_booking_confirmation_sms(event)

    assert message == (
        "Hello Ravi Kumar, your appointment at Smile Dental Clinic "
        "is confirmed for 14 May 2026 at 7:30 PM."
    )
    assert len(message) < 120


def test_notification_orchestrator_persists_and_marks_provider_acceptance() -> None:
    asyncio.run(_run_success_test())


async def _run_success_test() -> None:
    store = _InMemoryNotificationStore()
    provider = _SuccessfulSMSProvider(provider_request_id="fast-1")
    orchestrator = _orchestrator(provider=provider, store=store)

    queued = await orchestrator.enqueue_booking_confirmation(_event(), "req-1")
    await orchestrator.aclose()

    record = store.records[queued.idempotency_key]
    assert queued.persisted is True
    assert queued.status == "queued"
    assert record.status == "sent"
    assert record.attempts == 1
    assert record.provider_request_id == "fast-1"
    assert record.delivered_at is None
    assert provider.messages == [
        "Hello Ravi Kumar, your appointment at Smile Dental Clinic "
        "is confirmed for 14 May 2026 at 7:30 PM."
    ]


def test_db_level_deduplication_survives_worker_restart() -> None:
    asyncio.run(_run_restart_dedupe_test())


async def _run_restart_dedupe_test() -> None:
    store = _InMemoryNotificationStore()
    first_provider = _SuccessfulSMSProvider(provider_request_id="fast-1")
    first = _orchestrator(provider=first_provider, store=store)
    initial = await first.enqueue_booking_confirmation(_event(), "req-1")
    await first.aclose()
    assert store.records[initial.idempotency_key].status == "sent"

    second_provider = _SuccessfulSMSProvider(provider_request_id="fast-2")
    second = _orchestrator(provider=second_provider, store=store)
    duplicate = await second.enqueue_booking_confirmation(_event(), "req-2")
    await second.aclose()

    assert duplicate.duplicate_prevented is True
    assert duplicate.status == "sent"
    assert len(first_provider.messages) == 1
    assert second_provider.messages == []


def test_reconnect_safe_retry_continues_existing_persistent_state() -> None:
    asyncio.run(_run_reconnect_retry_test())


async def _run_reconnect_retry_test() -> None:
    store = _InMemoryNotificationStore()
    existing = await store.create_notification_delivery(
        NotificationDeliveryCreate(
            booking_fingerprint="booking-fingerprint-1",
            notification_type="booking_confirmation_sms",
            idempotency_key="calcom:booking_uid_123:booking_confirmation_sms",
            provider="fast2sms",
            fulfillment_language="marathi",
        )
    )
    await store.mark_notification_sending(existing.notification_id)
    await store.mark_notification_retrying(
        existing.notification_id,
        error_detail="RuntimeError: temporary provider outage",
    )

    provider = _SuccessfulSMSProvider(provider_request_id="fast-retry")
    orchestrator = _orchestrator(provider=provider, store=store)
    state = await orchestrator.enqueue_booking_confirmation(
        _event(fulfillment_language="marathi"),
        "req-reconnect",
    )
    await orchestrator.aclose()

    record = store.records[state.idempotency_key]
    assert state.status == "retrying"
    assert record.status == "sent"
    assert record.attempts == 2
    assert record.fulfillment_language == "marathi"
    assert provider.messages


def test_retry_exhaustion_is_bounded_and_persisted() -> None:
    asyncio.run(_run_retry_exhaustion_test())


async def _run_retry_exhaustion_test() -> None:
    store = _InMemoryNotificationStore()
    provider = _FailingSMSProvider()
    orchestrator = _orchestrator(provider=provider, store=store)

    state = await orchestrator.enqueue_booking_confirmation(_event(), "req-fail")
    await orchestrator.aclose()
    record = store.records[state.idempotency_key]

    assert record.status == "retry_exhausted"
    assert record.attempts == 3
    assert len(provider.messages) == 3
    assert "provider failed" in (record.error_detail or "")


def test_notification_multilingual_state_is_persisted() -> None:
    asyncio.run(_run_multilingual_test())


async def _run_multilingual_test() -> None:
    store = _InMemoryNotificationStore()
    provider = _SuccessfulSMSProvider()
    orchestrator = _orchestrator(provider=provider, store=store)

    state = await orchestrator.enqueue_booking_confirmation(
        _event(fulfillment_language="kannada"),
        "req-lang",
    )
    await orchestrator.aclose()

    record = store.records[state.idempotency_key]
    assert state.fulfillment_language == "kannada"
    assert record.fulfillment_language == "kannada"


def test_fulfillment_validator_blocks_unpersisted_notification_state() -> None:
    booking_result = SimpleNamespace(
        booking_success=True,
        calcom_booking_uid="booking_uid_123",
        booking_time=datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc),
        validation_status="valid",
        persistence_status="persisted",
        booking_fingerprint="booking-fingerprint-1",
    )
    unsafe = NotificationFulfillmentState(
        notification_id=None,
        booking_fingerprint="booking-fingerprint-1",
        idempotency_key="key",
        notification_type="booking_confirmation_sms",
        provider="fast2sms",
        status="failed",
        attempts=0,
        persisted=False,
    )

    validator = RuntimeIntegrityValidator()

    assert validator.validate_fulfillment(
        booking_result=booking_result,
        notification_state=unsafe,
        notification_required=True,
    ) is False
    assert validator.validate_fulfillment(
        booking_result=booking_result,
        notification_state=NotificationFulfillmentState(
            notification_id="notification-1",
            booking_fingerprint="booking-fingerprint-1",
            idempotency_key="key",
            notification_type="booking_confirmation_sms",
            provider="fast2sms",
            status="queued",
            attempts=0,
            persisted=True,
        ),
        notification_required=True,
    ) is True


def _event(*, fulfillment_language: str = "english") -> BookingConfirmationEvent:
    return BookingConfirmationEvent(
        booking_uid="booking_uid_123",
        booking_status="accepted",
        customer_name="Ravi Kumar",
        phone_number="+919876543210",
        business_name="Smile Dental Clinic",
        appointment_start=datetime(2026, 5, 14, 14, 0, tzinfo=timezone.utc),
        time_zone="Asia/Kolkata",
        booking_fingerprint="booking-fingerprint-1",
        fulfillment_language=fulfillment_language,
    )


def _orchestrator(
    *,
    provider: object,
    store: object,
) -> BookingConfirmationNotificationOrchestrator:
    return BookingConfirmationNotificationOrchestrator(
        sms_provider=provider,
        delivery_store=store,
        queue_max_items=10,
        drain_timeout_seconds=1.0,
        retry_backoff_seconds=(0.0, 0.0, 0.0),
    )


class _InMemoryNotificationStore:
    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}
        self._ids = 0

    async def create_notification_delivery(
        self,
        payload: NotificationDeliveryCreate,
    ) -> object:
        existing = self.records.get(payload.idempotency_key)
        if existing is not None:
            return existing
        self._ids += 1
        record = SimpleNamespace(
            notification_id=f"notification-{self._ids}",
            booking_fingerprint=payload.booking_fingerprint,
            notification_type=payload.notification_type,
            idempotency_key=payload.idempotency_key,
            provider=payload.provider,
            provider_request_id=None,
            status="queued",
            attempts=0,
            fulfillment_language=payload.fulfillment_language,
            created_at=datetime.now(timezone.utc),
            last_attempt_at=None,
            delivered_at=None,
            failed_at=None,
            error_detail=None,
        )
        self.records[payload.idempotency_key] = record
        return record

    async def get_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        return self.records.get(idempotency_key)

    async def get_successful_notification_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> object | None:
        record = self.records.get(idempotency_key)
        if record is not None and record.status in {"sent", "delivered"}:
            return record
        return None

    async def mark_notification_sending(self, notification_id: str) -> object | None:
        record = self._by_id(notification_id)
        if record is None:
            return None
        record.status = "sending"
        record.attempts += 1
        record.last_attempt_at = datetime.now(timezone.utc)
        record.error_detail = None
        return record

    async def mark_notification_sent(
        self,
        notification_id: str,
        *,
        provider_request_id: str | None,
    ) -> object | None:
        record = self._by_id(notification_id)
        if record is None:
            return None
        record.status = "sent"
        record.provider_request_id = provider_request_id
        record.error_detail = None
        return record

    async def mark_notification_retrying(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        record = self._by_id(notification_id)
        if record is None:
            return None
        record.status = "retrying"
        record.error_detail = error_detail
        return record

    async def mark_notification_retry_exhausted(
        self,
        notification_id: str,
        *,
        error_detail: str,
    ) -> object | None:
        record = self._by_id(notification_id)
        if record is None:
            return None
        record.status = "retry_exhausted"
        record.failed_at = datetime.now(timezone.utc)
        record.error_detail = error_detail
        return record

    def _by_id(self, notification_id: str) -> SimpleNamespace | None:
        for record in self.records.values():
            if record.notification_id == notification_id:
                return record
        return None


class _SuccessfulSMSProvider:
    def __init__(self, *, provider_request_id: str = "fast-request") -> None:
        self.provider_request_id = provider_request_id
        self.phone_numbers: list[str] = []
        self.messages: list[str] = []

    async def send_sms(
        self,
        *,
        phone_number: str,
        message: str,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        self.phone_numbers.append(phone_number)
        self.messages.append(message)
        return SimpleNamespace(request_id=self.provider_request_id)


class _FailingSMSProvider:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_sms(
        self,
        *,
        phone_number: str,
        message: str,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> object:
        self.messages.append(message)
        raise RuntimeError("provider failed")
