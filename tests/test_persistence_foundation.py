from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from database.models.bookings import BookingModel
from database.models.business_settings import BusinessSettingsModel
from database.models.calls import CallModel
from database.models.notifications import NotificationDeliveryModel
from database.models.recordings import RecordingMetadataModel
from database.models.transcripts import TranscriptModel
from database.models.workers import WorkerHeartbeatModel
from database.persistence import PersistenceEnvelope, RuntimePersistenceService
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
from database.session import DatabaseSettings, normalize_database_url
from voice_agent.config import ConfigError, load_config
from voice_agent.runtime_metrics import DEPLOYMENT_METRICS


def test_database_url_is_normalized_for_async_postgres_driver() -> None:
    assert normalize_database_url("postgres://user:pass@localhost/app") == (
        "postgresql+asyncpg://user:pass@localhost/app"
    )
    assert normalize_database_url("postgresql://user:pass@localhost/app") == (
        "postgresql+asyncpg://user:pass@localhost/app"
    )


def test_database_settings_parse_environment() -> None:
    settings = DatabaseSettings.from_env(
        {
            "DATABASE_URL": "postgres://user:pass@localhost/app",
            "DATABASE_POOL_SIZE": "7",
            "DATABASE_MAX_OVERFLOW": "3",
            "DATABASE_RETRY_ATTEMPTS": "4",
            "DATABASE_RETRY_BACKOFF_SECONDS": "0.2",
            "DATABASE_QUEUE_MAX_ITEMS": "25",
            "DATABASE_STARTUP_RETRY_ATTEMPTS": "9",
            "DATABASE_STARTUP_RETRY_BACKOFF_SECONDS": "1.5",
        }
    )

    assert settings.is_configured is True
    assert settings.url == "postgresql+asyncpg://user:pass@localhost/app"
    assert settings.pool_size == 7
    assert settings.max_overflow == 3
    assert settings.retry_attempts == 4
    assert settings.retry_backoff_seconds == 0.2
    assert settings.queue_max_items == 25
    assert settings.startup_retry_attempts == 9
    assert settings.startup_retry_backoff_seconds == 1.5


def test_load_config_parses_worker_resilience_defaults(monkeypatch) -> None:
    _set_required_runtime_env(monkeypatch)
    monkeypatch.setenv("WORKER_ID", "worker-test")
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "11")
    monkeypatch.setenv("WORKER_STALE_AFTER_SECONDS", "61")
    monkeypatch.setenv("WORKER_DRAIN_TIMEOUT_SECONDS", "31")
    monkeypatch.setenv("MAX_CALL_DURATION_SECONDS", "1810")
    monkeypatch.setenv("CALL_TIMEOUT_WARNING_SECONDS", "20")
    monkeypatch.setenv("WORKER_HEALTH_PORT", "8091")

    config = load_config()

    assert config.worker.worker_id == "worker-test"
    assert config.worker.heartbeat_interval_seconds == 11
    assert config.worker.stale_after_seconds == 61
    assert config.worker.drain_timeout_seconds == 31
    assert config.worker.max_call_duration_seconds == 1810
    assert config.worker.call_timeout_warning_seconds == 20
    assert config.worker.health_port == 8091


def test_load_config_requires_url_when_persistence_is_explicitly_enabled(
    monkeypatch,
) -> None:
    _set_required_runtime_env(monkeypatch)
    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    try:
        load_config()
    except ConfigError as exc:
        assert "DATABASE_URL is required when PERSISTENCE_ENABLED is true" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected ConfigError")


def test_repositories_persist_core_entities_without_cross_coupling() -> None:
    asyncio.run(_run_repository_test())


def test_notification_delivery_model_defines_authoritative_idempotency_table() -> None:
    columns = NotificationDeliveryModel.__table__.columns
    constraints = {
        constraint.name
        for constraint in NotificationDeliveryModel.__table__.constraints
    }

    assert NotificationDeliveryModel.__tablename__ == "notification_deliveries"
    assert "notification_id" in columns
    assert "booking_fingerprint" in columns
    assert "notification_type" in columns
    assert "idempotency_key" in columns
    assert "provider_request_id" in columns
    assert "status" in columns
    assert "attempts" in columns
    assert "last_attempt_at" in columns
    assert "delivered_at" in columns
    assert "failed_at" in columns
    assert "error_detail" in columns
    assert "uq_notification_deliveries_idempotency_key" in constraints


def test_worker_heartbeat_model_defines_dead_worker_detection_surface() -> None:
    columns = WorkerHeartbeatModel.__table__.columns

    assert WorkerHeartbeatModel.__tablename__ == "worker_heartbeats"
    assert "worker_id" in columns
    assert "last_seen_at" in columns
    assert "active_session_count" in columns
    assert "uptime_seconds" in columns
    assert "current_calls" in columns
    assert "memory_usage_mb" in columns
    assert "cpu_usage_percent" in columns
    assert "status" in columns


def test_call_model_tracks_worker_lifecycle_state() -> None:
    columns = CallModel.__table__.columns

    assert "status" in columns
    assert "worker_id" in columns
    assert "abandoned_at" in columns
    assert "termination_reason" in columns
    assert "last_lifecycle_event" in columns


async def _run_repository_test() -> None:
    session = _FakeSession()

    call = await CallRepository(session).create_or_update(
        CallCreate(
            call_id="call-1",
            room_id="room-1",
            caller_phone="+919876543210",
            language="hinglish",
            started_at=datetime(2026, 5, 15, 8, 0, tzinfo=timezone.utc),
        )
    )
    assert call.call_id == "call-1"
    assert call.room_id == "room-1"
    assert call.language == "hinglish"

    booking = await BookingRepository(session).create_or_update(
        BookingCreate(
            booking_id="booking-1",
            call_id="call-1",
            customer_name="Rahul",
            phone_number="+919876543210",
            service_type="dental cleaning",
            appointment_date="tomorrow",
            appointment_time="6 PM",
            doctor_preference="any doctor",
            notes="",
            confirmation_status="confirmed",
        )
    )
    assert booking.booking_id == "booking-1"
    assert booking.confirmation_status == "confirmed"

    transcript = await TranscriptRepository(session).create(
        TranscriptCreate(
            transcript_id="transcript-1",
            call_id="call-1",
            speaker="caller",
            text="I need dental cleaning tomorrow",
            language="english",
        )
    )
    assert transcript.transcript_id == "transcript-1"
    assert transcript.speaker == "caller"

    recording = await RecordingMetadataRepository(session).create(
        RecordingMetadataCreate(
            recording_id="recording-1",
            call_id="call-1",
            file_path="recordings/call-1.wav",
            duration_seconds=42,
        )
    )
    assert recording.recording_id == "recording-1"
    assert recording.file_path == "recordings/call-1.wav"

    settings = await BusinessSettingsRepository(session).upsert(
        BusinessSettingsUpdate(
            business_name="Smile Dental Clinic",
            business_type="clinic",
            services=("dental cleaning", "braces treatment"),
            receptionist_tone="calm",
            default_language="english",
            greeting_prompt="Hello",
            refusal_policy="Clinic questions only.",
        )
    )
    assert settings.business_name == "Smile Dental Clinic"
    assert settings.services == ["dental cleaning", "braces treatment"]


def test_runtime_persistence_retries_failed_writes(caplog) -> None:
    asyncio.run(_run_retry_test(caplog))


async def _run_retry_test(caplog) -> None:
    caplog.set_level(logging.INFO)
    writer = _FlakyWriter()
    service = RuntimePersistenceService(
        writer,
        retry_attempts=1,
        retry_backoff_seconds=0,
        queue_max_items=5,
    )

    await service.start()
    queued = service.enqueue_transcript(
        call_id="call-1",
        speaker="caller",
        text="hello",
        language="english",
        request_id="req-1",
    )
    await service.stop(drain_timeout_seconds=1.0)

    assert queued is True
    assert writer.attempts == 2
    assert [envelope.event_type for envelope in writer.written] == ["transcript"]
    events = [record.getMessage() for record in caplog.records]
    assert "persistence_retry_triggered" in events


def test_runtime_persistence_failures_do_not_escape_realtime_path(caplog) -> None:
    asyncio.run(_run_failure_isolation_test(caplog))


async def _run_failure_isolation_test(caplog) -> None:
    caplog.set_level(logging.INFO)
    service = RuntimePersistenceService(
        _AlwaysFailWriter(),
        retry_attempts=0,
        retry_backoff_seconds=0,
        queue_max_items=5,
    )

    await service.start()
    queued = service.enqueue_call_started(call_id="call-1", room_id="room-1")
    await service.stop(drain_timeout_seconds=1.0)

    assert queued is True
    events = [record.getMessage() for record in caplog.records]
    assert "db_write_failed" in events


def test_runtime_persistence_enqueue_is_non_blocking_for_delayed_writes() -> None:
    asyncio.run(_run_non_blocking_enqueue_test())


def test_runtime_persistence_reports_queue_pressure_and_drops(caplog) -> None:
    DEPLOYMENT_METRICS.reset()
    caplog.set_level(logging.INFO)
    service = RuntimePersistenceService(
        _DelayedWriter(),
        retry_attempts=0,
        retry_backoff_seconds=0,
        queue_max_items=1,
    )

    first = service.enqueue_call_started(call_id="call-1", room_id="room-1")
    second = service.enqueue_transcript(
        call_id="call-1",
        speaker="caller",
        text="this should drop when queue is full",
    )

    assert first is True
    assert second is False
    events = [record.getMessage() for record in caplog.records]
    assert "persistence_queue_pressure" in events
    assert "persistence_event_dropped" in events
    metrics = DEPLOYMENT_METRICS.snapshot()
    assert metrics["counters"]["persistence_queue_pressure"] == 1
    assert metrics["counters"]["queue_events_dropped"] == 1
    alert_events = {alert["event"] for alert in metrics["recent_alerts"]}
    assert "queue_pressure_high" in alert_events
    assert "queue_events_dropped" in alert_events


def test_runtime_persistence_records_postgres_reconnect_recovery(caplog) -> None:
    asyncio.run(_run_reconnect_metrics_test(caplog))


async def _run_reconnect_metrics_test(caplog) -> None:
    DEPLOYMENT_METRICS.reset()
    caplog.set_level(logging.INFO)
    writer = _FlakyWriter()
    service = RuntimePersistenceService(
        writer,
        retry_attempts=1,
        retry_backoff_seconds=0,
        queue_max_items=5,
    )

    await service.start()
    service.enqueue_transcript(
        call_id="call-1",
        speaker="caller",
        text="hello",
        language="english",
        request_id="req-1",
    )
    await service.stop(drain_timeout_seconds=1.0)

    events = [record.getMessage() for record in caplog.records]
    assert "postgres_reconnected" in events
    metrics = DEPLOYMENT_METRICS.snapshot()
    assert metrics["counters"]["reconnect_count"] == 1
    assert metrics["counters"]["retry_count"] == 1
    assert "postgres_reconnect" in {alert["event"] for alert in metrics["recent_alerts"]}


async def _run_non_blocking_enqueue_test() -> None:
    writer = _DelayedWriter()
    service = RuntimePersistenceService(
        writer,
        retry_attempts=0,
        retry_backoff_seconds=0,
        queue_max_items=5,
    )

    await service.start()
    queued = service.enqueue_call_started(call_id="call-1", room_id="room-1")

    assert queued is True
    assert writer.started is False

    await service.stop(drain_timeout_seconds=1.0)
    assert writer.started is True
    assert writer.completed is True


class _FakeSession:
    def __init__(self) -> None:
        self.objects: dict[tuple[type[object], str], object] = {}

    async def get(self, model_type: type[object], key: str) -> object | None:
        return self.objects.get((model_type, key))

    def add(self, model: object) -> None:
        for field_name in (
            "call_id",
            "booking_id",
            "transcript_id",
            "recording_id",
            "settings_id",
            "notification_id",
        ):
            value = getattr(model, field_name, None)
            if value is not None:
                self.objects[(type(model), value)] = model
                return

    async def flush(self) -> None:
        return None

    async def refresh(self, model: object) -> None:
        self.add(model)


class _FlakyWriter:
    def __init__(self) -> None:
        self.attempts = 0
        self.written: list[PersistenceEnvelope] = []

    async def write(self, envelope: PersistenceEnvelope) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("temporary failure")
        self.written.append(envelope)


class _AlwaysFailWriter:
    async def write(self, envelope: PersistenceEnvelope) -> None:
        raise RuntimeError(f"failed {envelope.event_type}")


class _DelayedWriter:
    def __init__(self) -> None:
        self.started = False
        self.completed = False

    async def write(self, envelope: PersistenceEnvelope) -> None:
        self.started = True
        await asyncio.sleep(0.01)
        self.completed = True


def _set_required_runtime_env(monkeypatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "lk-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "lk-secret")
    monkeypatch.setenv("LIVEKIT_ROOM_NAME", "env-room")
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
