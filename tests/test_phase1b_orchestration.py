from __future__ import annotations

import asyncio
import logging

from voice_agent.agent_phase1d import SpeechWorkItem, TranscriptWorkItem, _consume_transcripts
from voice_agent.audio import PcmFrame
from voice_agent.config import BusinessConfig
from voice_agent.conversation.orchestrator import ConversationOrchestrator
from voice_agent.conversation.recovery import build_interruption_recovery
from voice_agent.conversation.silence_handler import SilenceHandler
from voice_agent.conversation.states import BookingStage, ConversationState, PendingAction
from voice_agent.conversation.turn_manager import ResponseRepetitionGuard
from voice_agent.conversational_booking import ConversationalBookingFlow
from voice_agent.interruption import RealtimeInterruptionManager
from voice_agent.language import SessionLanguageRouter, default_language_snapshot
from voice_agent.providers.openai_text import AIResponse
from voice_agent.session_memory import CallSessionMemory


def test_conversation_state_machine_allows_deterministic_flow() -> None:
    orchestrator = ConversationOrchestrator(initial_state=ConversationState.IDLE)

    assert orchestrator.transition(
        ConversationState.GREETING,
        reason="call_started",
        pending_action=PendingAction.PLAY_RESPONSE,
    )
    assert orchestrator.transition(
        ConversationState.LISTENING,
        reason="greeting_complete",
        pending_action=PendingAction.WAIT_FOR_CALLER,
    )
    assert orchestrator.transition(
        ConversationState.THINKING,
        reason="final_transcript",
        pending_action=PendingAction.GENERATE_RESPONSE,
    )
    assert orchestrator.transition(
        ConversationState.SPEAKING,
        reason="response_ready",
        pending_action=PendingAction.PLAY_RESPONSE,
    )
    assert orchestrator.transition(
        ConversationState.LISTENING,
        reason="playback_complete",
        pending_action=PendingAction.WAIT_FOR_CALLER,
    )
    assert orchestrator.transition(ConversationState.GREETING, reason="invalid_restart") is False

    snapshot = orchestrator.snapshot()
    assert snapshot.current_state == ConversationState.LISTENING
    assert snapshot.previous_state == ConversationState.SPEAKING
    assert snapshot.pending_action == PendingAction.WAIT_FOR_CALLER


def test_interruption_cancels_playback_and_logs_recovery_events(caplog) -> None:
    caplog.set_level(logging.INFO)
    stop_calls: list[int] = []
    manager = RealtimeInterruptionManager(speech_rms_threshold=100.0)
    manager.set_playback_stop_callback(lambda: stop_calls.append(35) or 35)
    assert manager.mark_speaking(0, request_id="req-1", response_id="resp-1")

    manager.observe_caller_audio(
        PcmFrame(
            data=b"\xe8\x03" * 160,
            sample_rate=16000,
            channels=1,
            samples_per_channel=160,
        )
    )

    events = [record.getMessage() for record in caplog.records]
    assert manager.state == ConversationState.LISTENING
    assert manager.current_generation == 1
    assert stop_calls == [35]
    assert "caller_speaking_started" in events
    assert "interruption_detected" in events
    assert "playback_cancelled" in events
    assert "ai_speaking_stopped" in events
    assert "conversation_recovered" in events


def test_booking_progression_tracks_structured_stages() -> None:
    asyncio.run(_run_booking_progression_test())


async def _run_booking_progression_test() -> None:
    memory = CallSessionMemory(session_id="room-stage")
    flow = ConversationalBookingFlow(_business_config())

    first = await flow.handle_turn(
        "I want to book dental cleaning",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-1",
    )
    second = await flow.handle_turn(
        "Ravi Kumar",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-2",
    )
    third = await flow.handle_turn(
        "9876543210",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-3",
    )
    fourth = await flow.handle_turn(
        "tomorrow evening",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-4",
    )
    fifth = await flow.handle_turn(
        "any doctor is fine",
        memory=memory,
        language=default_language_snapshot(),
        request_id="req-5",
    )

    assert first.booking_stage == BookingStage.CUSTOMER_COLLECTION.value
    assert first.response_text == "Sure. May I have your name?"
    assert second.booking_stage == BookingStage.PHONE_COLLECTION.value
    assert second.response_text == "And your phone number, please?"
    assert third.booking_stage == BookingStage.DATE_COLLECTION.value
    assert third.response_text == "Which date would you prefer?"
    assert fourth.booking_stage == BookingStage.DOCTOR_COLLECTION.value
    assert fourth.response_text == "Do you have a doctor preference, or is any doctor okay?"
    assert fifth.booking_stage == BookingStage.NOTES_COLLECTION.value
    assert fifth.response_text == "Any notes I should add for the visit?"
    assert memory.booking.caller_name == "Ravi Kumar"
    assert memory.booking.phone_number == "+919876543210"
    assert memory.booking.selected_service == "dental cleaning"
    assert memory.booking.preferred_date == "tomorrow"
    assert memory.booking.preferred_time == "evening"
    assert memory.booking.doctor_preference == "any doctor"


def test_silence_handler_recovers_without_spamming() -> None:
    now = 100.0

    def clock() -> float:
        return now

    handler = SilenceHandler(silence_seconds=5.0, max_prompts=2, clock=clock)
    assert handler.maybe_prompt(state=ConversationState.LISTENING) is None

    now = 106.0
    assert handler.maybe_prompt(state=ConversationState.LISTENING) == "Take your time."
    assert handler.maybe_prompt(state=ConversationState.LISTENING) is None

    now = 112.0
    assert handler.maybe_prompt(state=ConversationState.LISTENING) == (
        "I'm here. Whenever you're ready."
    )

    now = 118.0
    assert handler.maybe_prompt(state=ConversationState.LISTENING) is None


def test_repetition_guard_suppresses_duplicate_responses() -> None:
    guard = ResponseRepetitionGuard()

    assert guard.should_emit("Got it. I have the details noted.") is True
    assert guard.should_emit("  Got it.   I have the details noted. ") is False
    assert guard.should_emit("Sure. May I have your name?") is True


def test_multilingual_interruption_recovery_preserves_style() -> None:
    async def run() -> None:
        router = SessionLanguageRouter(initial_language="english")
        language = (
            await router.route_text(
                "mujhe appointment chahiye",
                request_id="req-hi",
                is_final=True,
            )
        ).snapshot

        assert language.active_language == "hinglish"
        assert build_interruption_recovery("Tomorrow evening.", language=language) == (
            "Got it. Tomorrow evening."
        )

    asyncio.run(run())


def test_duplicate_ai_response_is_not_played_after_orchestration() -> None:
    asyncio.run(_run_duplicate_response_suppression_test())


async def _run_duplicate_response_suppression_test() -> None:
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue()
    speech_queue: asyncio.Queue[SpeechWorkItem | None] = asyncio.Queue()
    manager = RealtimeInterruptionManager()
    openai_client = _DuplicateOpenAIClient()

    await transcript_queue.put(TranscriptWorkItem(text="hello", request_id="req-1"))
    await transcript_queue.put(TranscriptWorkItem(text="hello again", request_id="req-2"))
    await transcript_queue.put(None)

    await _consume_transcripts(
        openai_client,
        transcript_queue,
        speech_queue,
        interruption_manager=manager,
    )

    first = await speech_queue.get()
    close = await speech_queue.get()

    assert isinstance(first, SpeechWorkItem)
    assert first.text == "Sure. May I have your name?"
    assert close is None
    assert speech_queue.empty()


def test_invalid_state_transition_is_observable(caplog) -> None:
    caplog.set_level(logging.INFO)
    orchestrator = ConversationOrchestrator(initial_state=ConversationState.LISTENING)

    assert orchestrator.transition(ConversationState.GREETING, reason="late_greeting") is False

    events = [record.getMessage() for record in caplog.records]
    assert "invalid_state_transition" in events
    assert "conversation_state_transition_blocked" in events


def test_interruption_during_thinking_cancels_active_generation(caplog) -> None:
    async def run() -> None:
        caplog.set_level(logging.INFO)
        manager = RealtimeInterruptionManager(speech_rms_threshold=100.0)
        assert manager.mark_thinking(0, request_id="req-think")
        task = asyncio.create_task(asyncio.sleep(30), name="response-task")
        manager.track_response_task(task, generation=0, request_id="req-think")

        manager.observe_caller_audio(
            PcmFrame(
                data=b"\xe8\x03" * 160,
                sample_rate=16000,
                channels=1,
                samples_per_channel=160,
            )
        )

        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()
        assert manager.current_generation == 1
        assert manager.state == ConversationState.LISTENING

    asyncio.run(run())
    events = [record.getMessage() for record in caplog.records]
    assert "interruption_detected" in events
    assert "openai_response_cancelled" in events
    assert "stale_generation_cancelled" in events
    assert "interruption_recovery_started" in events
    assert "interruption_recovery_completed" in events


def test_stale_and_duplicate_playback_are_blocked(caplog) -> None:
    caplog.set_level(logging.INFO)
    manager = RealtimeInterruptionManager()

    assert manager.mark_speaking(0, request_id="req-1", response_id="resp-1")
    assert manager.mark_speaking(0, request_id="req-1", response_id="resp-dup") is False
    manager.abort_pending_output(source="test", reason="stale_generation", request_id="req-1")
    assert manager.mark_speaking(0, request_id="req-old", response_id="resp-old") is False

    events = [record.getMessage() for record in caplog.records]
    assert "stale_playback_blocked" in events
    assert "stale_generation_cancelled" in events
    assert "playback_cleanup_completed" in events


def test_rapid_interruptions_recover_without_replay_loop(caplog) -> None:
    caplog.set_level(logging.INFO)
    stop_calls: list[int] = []
    manager = RealtimeInterruptionManager(speech_rms_threshold=100.0)
    manager.set_playback_stop_callback(lambda: stop_calls.append(10) or 10)

    assert manager.mark_speaking(0, request_id="req-1", response_id="resp-1")
    assert manager.request_interruption(source="first") is True
    assert manager.request_interruption(source="ignored") is False
    assert manager.mark_speaking(1, request_id="req-2", response_id="resp-2")
    assert manager.request_interruption(source="second") is True

    assert manager.state == ConversationState.LISTENING
    assert manager.current_generation == 2
    assert stop_calls == [10, 10]
    events = [record.getMessage() for record in caplog.records]
    assert events.count("interruption_detected") == 2
    assert "state_guard_triggered" in events


def test_silence_recovery_blocks_active_speech_and_logs(caplog) -> None:
    now = 200.0

    def clock() -> float:
        return now

    caplog.set_level(logging.INFO)
    handler = SilenceHandler(silence_seconds=5.0, max_prompts=1, clock=clock)

    now = 206.0
    assert (
        handler.maybe_prompt(
            state=ConversationState.LISTENING,
            caller_speaking=True,
            request_id="req-silence",
        )
        is None
    )
    now = 212.0
    assert handler.maybe_prompt(state=ConversationState.LISTENING, request_id="req-silence") == (
        "Take your time."
    )

    events = [record.getMessage() for record in caplog.records]
    assert "state_guard_triggered" in events
    assert "silence_recovery_started" in events
    assert "silence_recovery_completed" in events


def test_recovery_repetition_guard_blocks_duplicate_recovery(caplog) -> None:
    caplog.set_level(logging.INFO)
    guard = ResponseRepetitionGuard()

    assert guard.should_emit("Got it. Tomorrow evening.", category="recovery") is True
    assert guard.should_emit("Got it. Tomorrow evening.", category="recovery") is False

    events = [record.getMessage() for record in caplog.records]
    assert "duplicate_response_blocked" in events


class _DuplicateOpenAIClient:
    async def generate_response(
        self,
        transcript: str,
        *,
        language=None,
        request_id=None,
    ) -> AIResponse:
        return AIResponse(
            text="Sure. May I have your name?",
            model="gpt-4o-mini",
            response_id=request_id,
            language=language.active_language if language is not None else "english",
        )


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment"),
        faqs=(),
        receptionist_tone="warm",
        refusal_behavior="Sorry, clinic questions only.",
        receptionist_personality="calm",
        context_path=None,
    )
