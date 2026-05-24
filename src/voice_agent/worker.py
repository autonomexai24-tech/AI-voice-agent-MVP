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

from datetime import datetime, timezone

from livekit.agents import AutoSubscribe, JobContext, JobRequest, WorkerOptions, cli
from livekit.agents.voice import AgentSession

from database.persistence import build_runtime_persistence_service
from database.session import DatabaseSettings
from voice_agent.agent_v2 import SimpleClinicAgent, build_instructions
from voice_agent.business_prompt import BusinessPromptOrchestrator
from voice_agent.config import ConfigError, load_config, load_project_dotenv
from voice_agent.logging_config import configure_logging, get_logger, log_error, log_event
from voice_agent.optimization.runtime_latency_optimizer import LATENCY_TARGETS_MS
from voice_agent.runtime_context import build_runtime_context_loader
from voice_agent.runtime_persistence import safe_enqueue

logger = get_logger(__name__)


async def _request_handler(req: JobRequest) -> None:
    """Log every incoming job request before auto-accepting."""
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
    log_event(
        logger,
        "worker_job_received",
        room_name=room_name,
        job_id=ctx.job.id if ctx.job else None,
    )

    try:
        config = load_config(room_name=room_name, require_room_name=False)
    except ConfigError as exc:
        configure_logging()
        log_error(logger, "worker_config_invalid", error=str(exc))
        raise

    configure_logging(config.log_level)

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
        call_id=room_name,
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
        business_name=business.name,
        services_count=len(business.services),
        context_source=snapshot.context_source,
        settings_id=snapshot.settings_id,
    )

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    log_event(
        logger,
        "worker_room_connected",
        room_name=room_name,
        remote_participant_count=len(ctx.room.remote_participants),
        participants=[
            p.identity for p in ctx.room.remote_participants.values()
        ],
    )

    # --- Build business instructions from runtime context ---
    orchestrator = BusinessPromptOrchestrator(business, context_source=snapshot.context_source)
    business = await orchestrator.load()
    instructions = build_instructions(business)

    log_event(
        logger,
        "worker_instructions_built",
        room_name=room_name,
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

    agent = SimpleClinicAgent(
        instructions=instructions,
        stt_language="unknown",
        stt_model=config.sarvam_stt.model,
        llm_model=config.openai.model,
        tts_language_code=config.sarvam.language_code,
        tts_model=config.sarvam.model,
        tts_speaker=config.sarvam.speaker,
        business_config=business,
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
        turn_detection="stt",
        min_endpointing_delay=0.07,
        streaming_first=True,
        concurrency_target_ai_calls=5,
        concurrency_target_human_operators=2,
        latency_targets_ms=LATENCY_TARGETS_MS,
    )

    started_at = datetime.now(timezone.utc)
    if persistence_service is not None:
        await persistence_service.start()
        safe_enqueue(
            persistence_service,
            "enqueue_call_started",
            call_id=room_name,
            room_id=room_name,
            language=agent.session_memory.language,
        )

    try:
        await session.start(
            agent=agent,
            room=ctx.room,
        )

        log_event(logger, "worker_session_started", room_name=room_name)
    finally:
        if persistence_service is not None:
            ended_at = datetime.now(timezone.utc)
            booking_outcome = (
                "confirmed"
                if agent.session_memory.booking.confirmation_completed
                else None
            )
            safe_enqueue(
                persistence_service,
                "enqueue_call_ended",
                call_id=room_name,
                ended_at=ended_at,
                duration_seconds=max(0, int((ended_at - started_at).total_seconds())),
                booking_outcome=booking_outcome,
                escalation_triggered=agent.session_memory.escalation_triggered,
            )
            await persistence_service.stop(
                drain_timeout_seconds=config.database.drain_timeout_seconds
            )


def main() -> None:
    load_project_dotenv()
    configure_logging()

    log_event(
        logger,
        "worker_starting",
        agent_name="voice-calling-agent",
    )

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=_request_handler,
            agent_name="voice-calling-agent",
        ),
    )


if __name__ == "__main__":
    main()
