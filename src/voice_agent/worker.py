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
        1. Load config from .env
        2. Connect to room (audio-only)
        3. Build business instructions from config
        4. Create SimpleClinicAgent with Sarvam STT/TTS + OpenAI LLM
        5. Start AgentSession with turn_detection="stt"
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

    log_event(
        logger,
        "worker_connecting_to_room",
        room_name=room_name,
        business_name=config.business.name,
        services_count=len(config.business.services),
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

    # --- Build business instructions ---
    orchestrator = BusinessPromptOrchestrator(config.business)
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
                escalation_triggered=False,
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
