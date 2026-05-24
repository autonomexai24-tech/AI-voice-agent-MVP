"""LiveKit Agents worker entrypoint — Official Sarvam plugin architecture.

Run with:
    python -m voice_agent.worker dev          # development mode (single job)
    python -m voice_agent.worker start        # production mode (accepts jobs)

The worker registers with LiveKit Cloud and receives job dispatches
when SIP calls arrive via telephony dispatch rules.  Each dispatched
job provides a dynamic room (e.g. 917676808950_mTyE6avLmZmo) that
already contains the SIP caller participant.

Architecture:
    LiveKit SIP → LiveKit AgentSession → Sarvam STT → OpenAI → Sarvam TTS → playback

All realtime orchestration (VAD, endpointing, interruptions, turn-taking,
playback) is handled by AgentSession + Sarvam STT flush_signal=True.
"""
from __future__ import annotations

import asyncio
import atexit
from datetime import datetime, timezone
import os
import signal

from livekit.agents import AutoSubscribe, JobContext, JobRequest, WorkerOptions, cli
from livekit.agents.voice import AgentSession

from database.persistence import build_runtime_persistence_service
from database.session import DatabaseSettings
from voice_agent.agent_v2 import SimpleClinicAgent, build_instructions
from voice_agent.business_prompt import BusinessPromptOrchestrator
from voice_agent.config import ConfigError, load_config, load_project_dotenv
from voice_agent.logging_config import configure_logging, get_logger, log_error, log_event
from voice_agent.notifications import BookingConfirmationNotificationOrchestrator
from voice_agent.optimization.runtime_latency_optimizer import LATENCY_TARGETS_MS
from voice_agent.providers.fast2sms import Fast2SMSClient
from voice_agent.runtime_context import build_runtime_context_loader
from voice_agent.runtime_metrics import log_deployment_event, record_infrastructure_alert
from voice_agent.runtime_persistence import safe_enqueue
from voice_agent.telephony_resilience import (
    MaxCallDurationGuard,
    WorkerHeartbeatService,
    WorkerRuntimeState,
    log_lifecycle_event,
    register_room_lifecycle_handlers,
)

logger = get_logger(__name__)
_WORKER_STATE = WorkerRuntimeState(
    worker_id=os.getenv("WORKER_ID", f"voice-worker-{os.getpid()}")
)
_HEARTBEAT_SERVICE: WorkerHeartbeatService | None = None
_SIGNALS_INSTALLED = False


async def _request_handler(req: JobRequest) -> None:
    """Log every incoming job request before auto-accepting."""
    if _WORKER_STATE.is_draining:
        log_event(
            logger,
            "worker_job_request_rejected",
            job_id=req.id,
            room_name=req.room.name if req.room else None,
            agent_name=req.agent_name,
            reason="worker_draining",
            worker_id=_WORKER_STATE.worker_id,
        )
        await req.reject()
        return

    log_event(
        logger,
        "worker_job_request_received",
        job_id=req.id,
        room_name=req.room.name if req.room else None,
        agent_name=req.agent_name,
    )
    await req.accept()
    log_event(logger, "worker_job_request_accepted", job_id=req.id)


async def entrypoint(ctx: JobContext) -> None:
    """Called by the LiveKit Agents framework for each dispatched job.

    Flow:
        1. Load infrastructure config from .env (API keys, models, URLs)
        2. Load business context from PostgreSQL (with .env fallback)
        3. Connect to room (audio-only)
        4. Build business instructions from runtime context snapshot
        5. Create SimpleClinicAgent with Sarvam STT/TTS + OpenAI LLM
        6. Start AgentSession with turn_detection="stt"

    Business context source priority:
        PostgreSQL (live) → .env fallback (if DB row missing or query fails)
    """
    load_project_dotenv()

    room_name = ctx.room.name
    call_id = room_name
    log_event(
        logger,
        "worker_job_received",
        room_name=room_name,
        call_id=call_id,
        worker_id=_WORKER_STATE.worker_id,
        job_id=ctx.job.id if ctx.job else None,
    )

    try:
        config = load_config(room_name=room_name, require_room_name=False)
    except ConfigError as exc:
        configure_logging()
        log_error(logger, "worker_config_invalid", error=str(exc))
        raise

    configure_logging(config.log_level)
    worker_state = _ensure_worker_state(config.worker.worker_id)
    heartbeat_service = await _ensure_heartbeat_service(
        DatabaseSettings.from_agent_config(config.database),
        worker_state,
        interval_seconds=config.worker.heartbeat_interval_seconds,
        stale_after_seconds=config.worker.stale_after_seconds,
    )
    worker_state.track_call_started(call_id)
    log_lifecycle_event(
        "call_started",
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_state.worker_id,
    )

    # --- Load business context from PostgreSQL (with .env fallback) ---
    db_settings = DatabaseSettings.from_agent_config(config.database)
    context_loader = build_runtime_context_loader(db_settings)

    if context_loader is not None:
        snapshot = await context_loader.load(
            env_fallback=config.business,
            env_greeting_prompt=config.greeting_text,
        )
    else:
        from voice_agent.runtime_context import RuntimeContextSnapshot
        snapshot = RuntimeContextSnapshot(
            business=config.business,
            context_source="env",
            settings_id=None,
            loaded_at=datetime.now(timezone.utc),
            latency_ms=0.0,
            default_language="english",
            greeting_prompt=config.greeting_text,
        )

    business = snapshot.business

    log_event(
        logger,
        "runtime_snapshot_created",
        call_id=call_id,
        room_name=room_name,
        worker_id=worker_state.worker_id,
        context_source=snapshot.context_source,
        settings_id=snapshot.settings_id,
        runtime_loaded_at=snapshot.loaded_at.isoformat(),
        runtime_load_latency_ms=snapshot.latency_ms,
        business_name=business.name,
        business_type=business.business_type,
        services=list(business.services),
        services_count=len(business.services),
        faqs_count=len(business.faqs),
        receptionist_tone=business.receptionist_tone,
        receptionist_personality=business.receptionist_personality,
        refusal_behavior=business.refusal_behavior,
        default_language=snapshot.default_language,
        greeting_prompt=snapshot.greeting_prompt,
    )

    log_event(
        logger,
        "worker_connecting_to_room",
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_state.worker_id,
        business_name=business.name,
        services_count=len(business.services),
        context_source=snapshot.context_source,
        settings_id=snapshot.settings_id,
    )

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    register_room_lifecycle_handlers(
        ctx.room,
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_state.worker_id,
    )

    log_event(
        logger,
        "worker_room_connected",
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_state.worker_id,
        remote_participant_count=len(ctx.room.remote_participants),
        participants=[
            p.identity for p in ctx.room.remote_participants.values()
        ],
    )
    log_deployment_event(
        logger,
        "worker_ready",
        worker_id=worker_state.worker_id,
        room_name=room_name,
        call_id=call_id,
        remote_participant_count=len(ctx.room.remote_participants),
    )

    # --- Build business instructions from runtime context ---
    orchestrator = BusinessPromptOrchestrator(business, context_source=snapshot.context_source)
    business = await orchestrator.load()
    instructions = build_instructions(business)

    log_event(
        logger,
        "worker_instructions_built",
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_state.worker_id,
        business_name=business.name,
        services_count=len(business.services),
        faqs_count=len(business.faqs),
        instructions_chars=len(instructions),
        context_source=snapshot.context_source,
    )

    # --- Create agent with official Sarvam + OpenAI plugins ---
    persistence_service = None
    try:
        persistence_service = build_runtime_persistence_service(
            DatabaseSettings.from_agent_config(config.database)
        )
    except Exception as exc:
        log_event(
            logger,
            "db_write_failed",
            event_type="runtime_persistence_startup",
            room_name=room_name,
            error_type=type(exc).__name__,
        )

    notification_orchestrator = (
        BookingConfirmationNotificationOrchestrator(
            sms_provider=Fast2SMSClient(config.fast2sms),
            queue_max_items=config.fast2sms.queue_max_items,
            drain_timeout_seconds=config.fast2sms.drain_timeout_seconds,
            delivery_store=persistence_service,
        )
        if config.fast2sms.is_configured
        else None
    )

    agent = SimpleClinicAgent(
        instructions=instructions,
        stt_language="unknown",
        stt_model=config.sarvam_stt.model,
        llm_model=config.openai.model,
        tts_language_code=config.sarvam.language_code,
        tts_model=config.sarvam.model,
        tts_speaker=config.sarvam.speaker,
        business_config=business,
        calcom_config=config.calcom,
        booking_notification_sink=notification_orchestrator,
        session_id=room_name,
        persistence_sink=persistence_service,
    )

    # --- Start session with STT-native turn detection ---
    # DO NOT pass vad= parameter; Sarvam handles VAD internally.
    session = AgentSession(
        turn_detection="stt",
        min_endpointing_delay=0.07,
    )

    log_event(
        logger,
        "worker_session_starting",
        room_name=room_name,
        call_id=call_id,
        worker_id=worker_state.worker_id,
        turn_detection="stt",
        min_endpointing_delay=0.07,
        streaming_first=True,
        concurrency_target_ai_calls=5,
        concurrency_target_human_operators=2,
        latency_targets_ms=LATENCY_TARGETS_MS,
    )

    started_at = datetime.now(timezone.utc)
    termination_reason: str | None = None
    timed_out = False
    if persistence_service is not None:
        await persistence_service.start()
        safe_enqueue(
            persistence_service,
            "enqueue_call_started",
            call_id=call_id,
            room_id=room_name,
            language=agent.session_memory.language,
            worker_id=worker_state.worker_id,
            status="active",
            last_lifecycle_event="call_started",
        )
    if heartbeat_service is not None:
        await heartbeat_service.emit_once()

    async def terminate_for_timeout() -> None:
        nonlocal termination_reason, timed_out
        timed_out = True
        termination_reason = "max_call_duration_exceeded"
        try:
            session.generate_reply(
                instructions=(
                    "Tell the caller briefly and professionally that this call has reached "
                    "the maximum allowed duration and will now end. Do not introduce new "
                    "booking or escalation steps."
                ),
                allow_interruptions=False,
            )
            await asyncio.sleep(2.0)
        except Exception as exc:
            log_event(
                logger,
                "call_timeout_message_failed",
                room_name=room_name,
                call_id=call_id,
                worker_id=worker_state.worker_id,
                error_type=type(exc).__name__,
            )
        await session.shutdown(drain=True)

    duration_guard = MaxCallDurationGuard(
        call_id=call_id,
        room_name=room_name,
        worker_id=worker_state.worker_id,
        max_duration_seconds=config.worker.max_call_duration_seconds,
        warning_seconds=config.worker.call_timeout_warning_seconds,
        on_timeout=terminate_for_timeout,
    )

    try:
        duration_guard.start()
        await session.start(
            agent=agent,
            room=ctx.room,
        )

        log_event(
            logger,
            "worker_session_started",
            room_name=room_name,
            call_id=call_id,
            worker_id=worker_state.worker_id,
        )
    finally:
        await duration_guard.stop()
        await _close_session(session, call_id=call_id, room_name=room_name, worker_id=worker_state.worker_id)
        if persistence_service is not None:
            ended_at = datetime.now(timezone.utc)
            booking_outcome = (
                "confirmed"
                if agent.session_memory.booking.confirmation_completed
                else ("timeout" if timed_out else None)
            )
            safe_enqueue(
                persistence_service,
                "enqueue_call_ended",
                call_id=call_id,
                ended_at=ended_at,
                duration_seconds=max(0, int((ended_at - started_at).total_seconds())),
                booking_outcome=booking_outcome,
                escalation_triggered=agent.session_memory.escalation_triggered,
                status="timeout_terminated" if timed_out else "ended",
                worker_id=worker_state.worker_id,
                termination_reason=termination_reason,
                last_lifecycle_event=(
                    "call_timeout_terminated" if timed_out else "call_ended"
                ),
            )
            await persistence_service.stop(
                drain_timeout_seconds=config.database.drain_timeout_seconds
            )
        if notification_orchestrator is not None:
            await notification_orchestrator.aclose()
        worker_state.track_call_ended(call_id)
        if heartbeat_service is not None:
            await heartbeat_service.emit_once()
        log_lifecycle_event(
            "call_ended",
            room_name=room_name,
            call_id=call_id,
            worker_id=worker_state.worker_id,
            reason=termination_reason,
        )


def main() -> None:
    load_project_dotenv()
    configure_logging()
    config = load_config(require_room_name=False)
    worker_state = _ensure_worker_state(config.worker.worker_id)
    _install_signal_handlers(worker_state, config.worker.drain_timeout_seconds)

    log_event(
        logger,
        "worker_starting",
        agent_name="voice-calling-agent",
        worker_id=worker_state.worker_id,
        heartbeat_interval_seconds=config.worker.heartbeat_interval_seconds,
        stale_after_seconds=config.worker.stale_after_seconds,
        drain_timeout_seconds=config.worker.drain_timeout_seconds,
        max_call_duration_seconds=config.worker.max_call_duration_seconds,
    )
    log_deployment_event(
        logger,
        "worker_started",
        worker_id=worker_state.worker_id,
        agent_name="voice-calling-agent",
        heartbeat_interval_seconds=config.worker.heartbeat_interval_seconds,
        stale_after_seconds=config.worker.stale_after_seconds,
        drain_timeout_seconds=config.worker.drain_timeout_seconds,
        max_call_duration_seconds=config.worker.max_call_duration_seconds,
    )

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=_request_handler,
            agent_name="voice-calling-agent",
            drain_timeout=int(config.worker.drain_timeout_seconds),
            host="0.0.0.0",
            port=config.worker.health_port,
        ),
    )


def _ensure_worker_state(worker_id: str) -> WorkerRuntimeState:
    global _WORKER_STATE
    if _WORKER_STATE.worker_id != worker_id:
        _WORKER_STATE = WorkerRuntimeState(worker_id=worker_id)
    return _WORKER_STATE


async def _ensure_heartbeat_service(
    settings: DatabaseSettings,
    worker_state: WorkerRuntimeState,
    *,
    interval_seconds: float,
    stale_after_seconds: float,
) -> WorkerHeartbeatService | None:
    global _HEARTBEAT_SERVICE
    if not settings.is_configured:
        return None
    if _HEARTBEAT_SERVICE is None or not _HEARTBEAT_SERVICE.is_running:
        _HEARTBEAT_SERVICE = WorkerHeartbeatService(
            settings=settings,
            worker_state=worker_state,
            interval_seconds=interval_seconds,
            stale_after_seconds=stale_after_seconds,
        )
        await _HEARTBEAT_SERVICE.start()
    return _HEARTBEAT_SERVICE


async def _close_session(
    session: AgentSession,
    *,
    call_id: str,
    room_name: str,
    worker_id: str,
) -> None:
    try:
        await session.aclose()
        log_event(
            logger,
            "agent_session_cleanup_completed",
            room_name=room_name,
            call_id=call_id,
            worker_id=worker_id,
        )
    except Exception as exc:
        log_event(
            logger,
            "agent_session_cleanup_failed",
            room_name=room_name,
            call_id=call_id,
            worker_id=worker_id,
            error_type=type(exc).__name__,
        )


def _install_signal_handlers(
    worker_state: WorkerRuntimeState,
    drain_timeout_seconds: float,
) -> None:
    global _SIGNALS_INSTALLED
    if _SIGNALS_INSTALLED:
        return

    def handle_shutdown(signum: int, _frame: object) -> None:
        worker_state.start_draining()
        log_event(
            logger,
            "worker_shutdown_started",
            worker_id=worker_state.worker_id,
            signal=signum,
            active_session_count=worker_state.active_session_count,
            drain_timeout_seconds=drain_timeout_seconds,
        )
        log_deployment_event(
            logger,
            "worker_shutdown_started",
            worker_id=worker_state.worker_id,
            signal=signum,
            active_session_count=worker_state.active_session_count,
            drain_timeout_seconds=drain_timeout_seconds,
        )

    for signal_name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, signal_name):
            signal.signal(getattr(signal, signal_name), handle_shutdown)

    def log_shutdown_completed() -> None:
        if worker_state.active_session_count > 0:
            record_infrastructure_alert(
                logger,
                "worker_shutdown_timeout",
                worker_id=worker_state.worker_id,
                active_session_count=worker_state.active_session_count,
                drain_timeout_seconds=drain_timeout_seconds,
            )
        log_event(
            logger,
            "worker_shutdown_completed",
            worker_id=worker_state.worker_id,
            active_session_count=worker_state.active_session_count,
        )
        log_deployment_event(
            logger,
            "worker_shutdown_completed",
            worker_id=worker_state.worker_id,
            active_session_count=worker_state.active_session_count,
        )

    atexit.register(log_shutdown_completed)
    _SIGNALS_INSTALLED = True


if __name__ == "__main__":
    main()
