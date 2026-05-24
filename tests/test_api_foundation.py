from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from api.dependencies.database import (
    get_booking_repository,
    get_business_settings_repository,
    get_call_repository,
    get_transcript_repository,
)
from api.main import create_app
from database.repositories.bookings import BookingList
from database.repositories.calls import CallList
from database.session import DatabaseSettings


def test_call_api_lists_and_fetches_calls() -> None:
    app = create_app()
    repository = _CallRepository()
    app.dependency_overrides[get_call_repository] = lambda: repository

    with TestClient(app) as client:
        list_response = client.get("/internal/v1/calls")
        item_response = client.get("/internal/v1/calls/call-1")
        missing_response = client.get("/internal/v1/calls/missing")

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["call_id"] == "call-1"
    assert list_response.json()["page"]["limit"] == 50
    assert item_response.status_code == 200
    assert item_response.json()["room_id"] == "room-1"
    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == "call_not_found"


def test_booking_api_lists_and_fetches_bookings() -> None:
    app = create_app()
    repository = _BookingRepository()
    app.dependency_overrides[get_booking_repository] = lambda: repository

    with TestClient(app) as client:
        list_response = client.get("/internal/v1/bookings")
        item_response = client.get("/internal/v1/bookings/booking-1")

    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["confirmation_status"] == "confirmed"
    assert item_response.status_code == 200
    assert item_response.json()["service_type"] == "dental cleaning"


def test_transcript_api_returns_call_transcripts() -> None:
    app = create_app()
    repository = _TranscriptRepository()
    app.dependency_overrides[get_transcript_repository] = lambda: repository

    with TestClient(app) as client:
        response = client.get("/internal/v1/calls/call-1/transcripts")

    assert response.status_code == 200
    assert response.json()["call_id"] == "call-1"
    assert response.json()["items"][0]["speaker"] == "caller"
    assert response.json()["items"][0]["text"] == "I need dental cleaning tomorrow"


def test_settings_api_gets_and_updates_business_settings() -> None:
    app = create_app()
    repository = _SettingsRepository()
    app.dependency_overrides[get_business_settings_repository] = lambda: repository

    with TestClient(app) as client:
        get_response = client.get("/internal/v1/settings/business")
        patch_response = client.patch(
            "/internal/v1/settings/business",
            json={
                "business_name": "Updated Clinic",
                "services": ["braces treatment"],
                "default_language": "hinglish",
            },
        )

    assert get_response.status_code == 200
    assert get_response.json()["business_name"] == "Smile Dental Clinic"
    assert patch_response.status_code == 200
    assert patch_response.json()["business_name"] == "Updated Clinic"
    assert patch_response.json()["services"] == ["braces treatment"]
    assert repository.updated is True


def test_api_returns_503_when_database_is_not_configured() -> None:
    app = create_app()

    with TestClient(app) as client:
        response = client.get("/internal/v1/calls")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


def test_health_and_readiness_endpoints_are_container_friendly() -> None:
    app = create_app()

    with TestClient(app) as client:
        health_response = client.get("/healthz")
        readiness_response = client.get("/readyz")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "service": "api"}
    assert readiness_response.status_code == 200
    assert readiness_response.json()["status"] == "ready"
    assert readiness_response.json()["database_configured"] is False


def test_deployment_health_live_and_diagnostics_are_operational_surfaces() -> None:
    app = create_app()

    with TestClient(app) as client:
        health_response = client.get("/health")
        live_response = client.get("/live")
        diagnostics_response = client.get("/internal/v1/deployment/diagnostics")
        full_ready_response = client.get("/ready")

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert live_response.status_code == 200
    assert "runtime" in live_response.json()
    assert diagnostics_response.status_code == 200
    diagnostics = diagnostics_response.json()
    assert diagnostics["dependencies"]["postgres"]["status"] == "disabled"
    assert diagnostics["dependencies"]["worker"]["status"] == "unknown"
    assert diagnostics["runtime"]["active_calls"] == 0
    assert full_ready_response.status_code == 503
    assert full_ready_response.json()["error"]["code"] == "deployment_not_ready"


def test_configured_app_initializes_database_schema_on_startup(monkeypatch) -> None:
    monkeypatch.delenv("INITIALIZE_DATABASE_ON_STARTUP", raising=False)
    events: list[str] = []
    engine = _FakeEngine(events)
    session_factory = object()

    def fake_create_engine(settings):
        assert settings.is_configured is True
        events.append("engine_created")
        return engine

    def fake_create_session_factory(received_engine):
        assert received_engine is engine
        events.append("session_factory_created")
        return session_factory

    async def fake_initialize_schema(received_engine):
        assert received_engine is engine
        events.append("schema_initialized")

    monkeypatch.setattr("api.main.create_engine", fake_create_engine)
    monkeypatch.setattr("api.main.create_session_factory", fake_create_session_factory)
    monkeypatch.setattr("api.main.initialize_schema", fake_initialize_schema)

    app = create_app(
        database_settings=DatabaseSettings(
            url="postgresql+asyncpg://postgres:postgres@localhost/app",
            enabled=True,
        ),
    )

    with TestClient(app) as client:
        readiness_response = client.get("/readyz")

    assert readiness_response.status_code == 200
    assert readiness_response.json()["database_configured"] is True
    assert events == [
        "engine_created",
        "schema_initialized",
        "session_factory_created",
        "engine_disposed",
    ]


def test_configured_app_retries_schema_initialization_on_startup(monkeypatch) -> None:
    monkeypatch.delenv("INITIALIZE_DATABASE_ON_STARTUP", raising=False)
    events: list[str] = []
    engine = _FakeEngine(events)
    attempts = 0

    def fake_create_engine(settings):
        events.append("engine_created")
        return engine

    def fake_create_session_factory(received_engine):
        assert received_engine is engine
        events.append("session_factory_created")
        return object()

    async def fake_initialize_schema(received_engine):
        nonlocal attempts
        assert received_engine is engine
        attempts += 1
        events.append(f"schema_attempt_{attempts}")
        if attempts == 1:
            raise RuntimeError("postgres is not ready yet")

    async def fake_sleep(seconds):
        events.append(f"sleep_{seconds}")

    monkeypatch.setattr("api.main.create_engine", fake_create_engine)
    monkeypatch.setattr("api.main.create_session_factory", fake_create_session_factory)
    monkeypatch.setattr("api.main.initialize_schema", fake_initialize_schema)
    monkeypatch.setattr("api.main.asyncio.sleep", fake_sleep)

    app = create_app(
        database_settings=DatabaseSettings(
            url="postgresql+asyncpg://postgres:postgres@localhost/app",
            enabled=True,
            startup_retry_attempts=2,
            startup_retry_backoff_seconds=0,
        ),
    )

    with TestClient(app) as client:
        readiness_response = client.get("/readyz")

    assert readiness_response.status_code == 200
    assert events == [
        "engine_created",
        "schema_attempt_1",
        "sleep_0",
        "schema_attempt_2",
        "session_factory_created",
        "engine_disposed",
    ]


@dataclass
class _Call:
    call_id: str = "call-1"
    room_id: str | None = "room-1"
    caller_phone: str | None = "+919876543210"
    language: str | None = "english"
    started_at: datetime = datetime(2026, 5, 15, 8, 0, tzinfo=timezone.utc)
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    booking_outcome: str | None = "confirmed"
    escalation_triggered: bool = False


@dataclass
class _Booking:
    booking_id: str = "booking-1"
    call_id: str = "call-1"
    customer_name: str | None = "Rahul"
    phone_number: str | None = "+919876543210"
    service_type: str | None = "dental cleaning"
    appointment_date: str | None = "tomorrow"
    appointment_time: str | None = "6 PM"
    doctor_preference: str | None = "any doctor"
    notes: str | None = ""
    confirmation_status: str = "confirmed"


@dataclass
class _Transcript:
    transcript_id: str = "transcript-1"
    call_id: str = "call-1"
    speaker: str = "caller"
    text: str = "I need dental cleaning tomorrow"
    timestamp: datetime = datetime(2026, 5, 15, 8, 1, tzinfo=timezone.utc)
    language: str | None = "english"


@dataclass
class _Settings:
    settings_id: str = "default"
    business_name: str = "Smile Dental Clinic"
    business_type: str = "clinic"
    services: list[str] = None
    receptionist_tone: str | None = "calm"
    default_language: str = "english"
    greeting_prompt: str | None = "Hello"
    refusal_policy: str | None = "Clinic questions only."
    updated_at: datetime = datetime(2026, 5, 15, 8, 2, tzinfo=timezone.utc)

    def __post_init__(self) -> None:
        if self.services is None:
            self.services = ["dental cleaning"]


class _CallRepository:
    async def list(self, *, limit: int | None = None, cursor: str | None = None) -> CallList:
        return CallList(items=[_Call()], next_cursor=None)

    async def get(self, call_id: str) -> _Call | None:
        return _Call() if call_id == "call-1" else None


class _BookingRepository:
    async def list(
        self,
        *,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> BookingList:
        return BookingList(items=[_Booking()], next_cursor=None)

    async def get(self, booking_id: str) -> _Booking | None:
        return _Booking() if booking_id == "booking-1" else None


class _TranscriptRepository:
    async def list_by_call(self, call_id: str) -> list[_Transcript]:
        return [_Transcript(call_id=call_id)]


class _SettingsRepository:
    def __init__(self) -> None:
        self.settings = _Settings()
        self.updated = False

    async def get(self) -> _Settings:
        return self.settings

    async def upsert(self, payload) -> _Settings:
        self.updated = True
        if payload.business_name is not None:
            self.settings.business_name = payload.business_name
        if payload.services is not None:
            self.settings.services = list(payload.services)
        if payload.default_language is not None:
            self.settings.default_language = payload.default_language
        return self.settings


class _FakeEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def dispose(self) -> None:
        self._events.append("engine_disposed")
