from __future__ import annotations

import asyncio
import logging

from voice_agent.agent_v2 import SimpleClinicAgent
from voice_agent.config import BusinessConfig
from voice_agent.telephony_resilience import (
    MaxCallDurationGuard,
    WorkerRuntimeState,
    register_room_lifecycle_handlers,
)
from voice_agent.runtime_metrics import DEPLOYMENT_METRICS


def test_room_lifecycle_handlers_emit_reconnect_and_participant_events(caplog) -> None:
    caplog.set_level(logging.INFO)
    room = _FakeRoom()

    register_room_lifecycle_handlers(
        room,
        room_name="room-1",
        call_id="call-1",
        worker_id="worker-1",
    )

    room.emit("participant_connected", _Participant("caller-1"))
    room.emit("participant_disconnected", _Participant("caller-1"), reason="network")
    room.emit("disconnected", "websocket_closed")
    room.emit("reconnected")

    events = [record.getMessage() for record in caplog.records]
    assert "participant_joined" in events
    assert "participant_disconnected" in events
    assert "room_disconnected" in events
    assert "call_reconnect_attempt" in events
    assert "room_reconnected" in events
    assert "call_reconnect_success" in events
    assert "call_recovered" in events


def test_worker_runtime_state_tracks_drain_and_active_calls() -> None:
    state = WorkerRuntimeState(worker_id="worker-1")

    state.track_call_started("call-1")
    state.track_call_started("call-2")
    state.start_draining()
    state.track_call_ended("call-1")
    snapshot = state.snapshot()

    assert state.is_draining is True
    assert snapshot.active_session_count == 1
    assert snapshot.current_calls == ("call-2",)
    assert snapshot.status == "draining"


def test_max_call_duration_guard_invokes_timeout_callback(caplog) -> None:
    asyncio.run(_run_timeout_guard_test(caplog))


async def _run_timeout_guard_test(caplog) -> None:
    DEPLOYMENT_METRICS.reset()
    caplog.set_level(logging.INFO)
    called = False

    async def on_timeout() -> None:
        nonlocal called
        called = True

    guard = MaxCallDurationGuard(
        call_id="call-1",
        room_name="room-1",
        worker_id="worker-1",
        max_duration_seconds=0.03,
        warning_seconds=0.01,
        on_timeout=on_timeout,
    )

    guard.start()
    await asyncio.sleep(0.06)
    await guard.stop()

    assert called is True
    events = [record.getMessage() for record in caplog.records]
    assert "call_timeout_warning" in events
    assert "call_timeout_terminated" in events
    assert "call_timeout" in {
        alert["event"] for alert in DEPLOYMENT_METRICS.snapshot()["recent_alerts"]
    }


def test_sarvam_tts_language_updates_when_runtime_language_switches(monkeypatch, caplog) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "sarvam-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    caplog.set_level(logging.INFO)
    agent = SimpleClinicAgent(
        instructions="You are a receptionist.",
        business_config=BusinessConfig(
            name="Clinic",
            business_type="clinic",
            services=(),
            faqs=(),
            receptionist_tone="warm",
            refusal_behavior="Clinic questions only.",
            receptionist_personality="calm",
            context_path=None,
        ),
        tts_language_code="en-IN",
        tts_model="bulbul:v3",
        tts_speaker="kavya",
    )

    agent._update_tts_language("hi-IN", active_language="hindi")

    assert agent._current_tts_language_code == "hi-IN"
    assert getattr(agent.tts, "_opts").target_language_code == "hi-IN"
    assert "tts_language_updated" in [record.getMessage() for record in caplog.records]


class _FakeRoom:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def on(self, event_name: str):
        def register(handler):
            self.handlers[event_name] = handler
            return handler

        return register

    def emit(self, event_name: str, *args, **kwargs) -> None:
        self.handlers[event_name](*args, **kwargs)


class _Participant:
    def __init__(self, identity: str) -> None:
        self.identity = identity
