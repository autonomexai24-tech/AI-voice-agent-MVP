from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone

from database.persistence import build_runtime_persistence_service
from database.session import DatabaseSettings
from voice_agent.audio import PcmAudio, PcmFrame, iter_frames, prepare_for_livekit
from voice_agent.config import AgentConfig
from voice_agent.interruption import RealtimeInterruptionManager
from voice_agent.language import (
    SessionLanguageRouter,
    SessionLanguageSnapshot,
    default_language_snapshot,
    language_from_sarvam_code,
)
from livekit import rtc
from voice_agent.livekit_inbound import LiveKitInboundAudioSubscriber
from voice_agent.logging_config import get_logger, log_error, log_event
from voice_agent.providers.openai_text import OpenAIResponseClient
from voice_agent.providers.sarvam_stt import SarvamStreamingSTTClient, Transcript
from voice_agent.providers.sarvam_tts import SarvamTTSClient
from voice_agent.runtime_persistence import safe_enqueue

logger = get_logger(__name__)

SPEECH_QUEUE_MAX_ITEMS = 4
PLAYBACK_QUEUE_MAX_ITEMS = 4


@dataclass(frozen=True)
class TranscriptWorkItem:
    text: str
    request_id: str | None
    generation: int = 0
    language: SessionLanguageSnapshot = field(default_factory=default_language_snapshot)


@dataclass(frozen=True)
class SpeechWorkItem:
    text: str
    request_id: str | None
    response_id: str | None
    model: str
    generation: int = 0
    language: SessionLanguageSnapshot = field(default_factory=default_language_snapshot)


@dataclass(frozen=True)
class PlaybackWorkItem:
    audio: PcmAudio
    request_id: str | None
    response_id: str | None
    model: str
    text_chars: int
    generation: int = 0
    language: SessionLanguageSnapshot = field(default_factory=default_language_snapshot)


async def run_phase_1d(config: AgentConfig, *, room: "rtc.Room | None" = None) -> None:
    audio_queue: asyncio.Queue[PcmFrame | None] = asyncio.Queue(
        maxsize=config.sarvam_stt.queue_max_chunks
    )
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue(
        maxsize=config.openai.queue_max_items
    )
    speech_queue: asyncio.Queue[SpeechWorkItem | None] = asyncio.Queue(
        maxsize=min(config.openai.queue_max_items, SPEECH_QUEUE_MAX_ITEMS)
    )
    playback_queue: asyncio.Queue[PlaybackWorkItem | None] = asyncio.Queue(
        maxsize=min(config.openai.queue_max_items, PLAYBACK_QUEUE_MAX_ITEMS)
    )
    stt_client = SarvamStreamingSTTClient(config.sarvam_stt)
    persistence_service = None
    try:
        persistence_service = build_runtime_persistence_service(
            DatabaseSettings.from_agent_config(config.database)
        )
    except Exception as exc:
        log_event(
            logger,
            "db_write_failed",
            event_type="phase_1d_persistence_startup",
            room_name=config.livekit.room_name,
            error_type=type(exc).__name__,
        )

    started_at = datetime.now(timezone.utc)
    if persistence_service is not None:
        await persistence_service.start()
        safe_enqueue(
            persistence_service,
            "enqueue_call_started",
            call_id=config.livekit.room_name,
            room_id=config.livekit.room_name,
            started_at=started_at,
        )

    openai_client = OpenAIResponseClient(
        config.openai,
        business_config=config.business,
        calcom_config=config.calcom,
        fast2sms_config=config.fast2sms,
        session_id=config.livekit.room_name,
        persistence_sink=persistence_service,
    )
    await openai_client.load_business_prompt()
    language_router = SessionLanguageRouter(
        initial_language=language_from_sarvam_code(config.sarvam.language_code),
        default_speaker=config.sarvam.speaker,
    )
    interruption_manager = RealtimeInterruptionManager(
        speech_queue=speech_queue,
        playback_queue=playback_queue,
    )
    transcript_handler = _TranscriptQueueHandler(
        transcript_queue,
        interruption_manager=interruption_manager,
        language_router=language_router,
    )

    log_event(
        logger,
        "phase_1d_started",
        room_name=config.livekit.room_name,
        stt_sample_rate=config.sarvam_stt.sample_rate,
        openai_model=config.openai.model,
        tts_model=config.sarvam.model,
        tts_speaker=config.sarvam.speaker,
        output_sample_rate=config.audio.output_sample_rate,
        frame_ms=config.audio.frame_ms,
        transcript_queue_max_items=config.openai.queue_max_items,
        speech_queue_max_items=speech_queue.maxsize,
        playback_queue_max_items=playback_queue.maxsize,
        realtime_interruptions_enabled=True,
        active_language=language_router.snapshot().active_language,
        tts_language_code=language_router.snapshot().sarvam_language_code,
    )

    try:
        async with SarvamTTSClient(config.sarvam) as tts_client:
            async with LiveKitInboundAudioSubscriber(
                config.livekit,
                audio_queue=audio_queue,
                sample_rate=config.sarvam_stt.sample_rate,
                channels=1,
                frame_ms=config.audio.frame_ms,
                audio_observer=interruption_manager.observe_caller_audio,
                room=room,
            ) as audio_session:
                await audio_session.publish_output_audio_track(
                    sample_rate=config.audio.output_sample_rate,
                    channels=1,
                    track_name="ai-response",
                )
                interruption_manager.set_playback_stop_callback(audio_session.stop_output_playback)

                stt_task = asyncio.create_task(
                    stt_client.stream_transcripts(audio_queue, transcript_handler.handle),
                    name="sarvam-stt-stream",
                )
                openai_task = asyncio.create_task(
                    _consume_transcripts(
                        openai_client,
                        transcript_queue,
                        speech_queue,
                        interruption_manager=interruption_manager,
                        language_router=language_router,
                    ),
                    name="openai-response-consumer",
                )
                tts_task = asyncio.create_task(
                    _consume_speech(
                        tts_client,
                        speech_queue,
                        playback_queue,
                        output_sample_rate=config.audio.output_sample_rate,
                        interruption_manager=interruption_manager,
                        language_router=language_router,
                    ),
                    name="sarvam-tts-consumer",
                )
                playback_task = asyncio.create_task(
                    _consume_playback(
                        audio_session,
                        playback_queue,
                        frame_ms=config.audio.frame_ms,
                        interruption_manager=interruption_manager,
                        language_router=language_router,
                    ),
                    name="livekit-response-playback-consumer",
                )
                wait_for_audio_task = asyncio.create_task(
                    audio_session.wait_for_audio_track(),
                    name="livekit-wait-for-audio-track",
                )

                core_tasks = {stt_task, openai_task, tts_task, playback_task}
                all_tasks = core_tasks | {wait_for_audio_task}
                try:
                    done, _pending = await asyncio.wait(
                        all_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if wait_for_audio_task not in done:
                        await _raise_first_completed(done)

                    await wait_for_audio_task
                    done, _pending = await asyncio.wait(
                        core_tasks,
                        return_when=asyncio.FIRST_COMPLETED,
                    )

                    if stt_task in done:
                        await stt_task
                        await _close_queue(transcript_queue)
                        await openai_task
                        await tts_task
                        await playback_task
                    else:
                        await _raise_first_completed(done)
                finally:
                    if not wait_for_audio_task.done():
                        wait_for_audio_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await wait_for_audio_task
                    if not stt_task.done():
                        stt_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await stt_task
                    if not openai_task.done():
                        _request_queue_close(transcript_queue)
                        openai_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await openai_task
                    if not tts_task.done():
                        _request_queue_close(speech_queue)
                        tts_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await tts_task
                    if not playback_task.done():
                        _request_queue_close(playback_queue)
                        playback_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await playback_task
    finally:
        await openai_client.aclose()
        if persistence_service is not None:
            ended_at = datetime.now(timezone.utc)
            safe_enqueue(
                persistence_service,
                "enqueue_call_ended",
                call_id=config.livekit.room_name,
                ended_at=ended_at,
                duration_seconds=max(0, int((ended_at - started_at).total_seconds())),
                booking_outcome=None,
                escalation_triggered=False,
            )
            await persistence_service.stop(
                drain_timeout_seconds=config.database.drain_timeout_seconds
            )

    log_event(logger, "phase_1d_completed", room_name=config.livekit.room_name)


class _TranscriptQueueHandler:
    def __init__(
        self,
        transcript_queue: asyncio.Queue[TranscriptWorkItem | None],
        *,
        interruption_manager: RealtimeInterruptionManager | None = None,
        language_router: SessionLanguageRouter | None = None,
    ) -> None:
        self._transcript_queue = transcript_queue
        self._interruption_manager = interruption_manager
        self._language_router = language_router
        self._dropped_transcripts = 0

    async def handle(self, transcript: Transcript) -> None:
        log_event(
            logger,
            "caller_transcript",
            transcript=transcript.text,
            request_id=transcript.request_id,
            is_final=transcript.is_final,
            metrics=transcript.metrics,
            raw_type=transcript.raw_type,
        )
        text = transcript.text.strip()
        if not text:
            return

        language = default_language_snapshot()
        language_switched = False
        if self._language_router is not None:
            language_result = await self._language_router.route_text(
                text,
                request_id=transcript.request_id,
                is_final=transcript.is_final,
            )
            language = language_result.snapshot
            language_switched = language_result.switched

        if language_switched and self._interruption_manager is not None:
            self._interruption_manager.abort_pending_output(
                source="language_switch",
                reason="caller_language_changed",
                request_id=transcript.request_id,
                detected_language=language.active_language,
                dominant_language=language.dominant_language,
                language_confidence=language.confidence,
                language_generation=language.generation,
            )

        if self._interruption_manager is not None:
            if not language_switched:
                self._interruption_manager.observe_caller_transcript(
                    text=text,
                    request_id=transcript.request_id,
                    is_final=transcript.is_final,
                )

        if not transcript.is_final:
            return

        item = TranscriptWorkItem(
            text=text,
            request_id=transcript.request_id,
            generation=(
                self._interruption_manager.current_generation
                if self._interruption_manager is not None
                else 0
            ),
            language=language,
        )
        try:
            self._transcript_queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            self._dropped_transcripts += 1
            with suppress(asyncio.QueueEmpty):
                self._transcript_queue.get_nowait()
                self._transcript_queue.task_done()
            self._transcript_queue.put_nowait(item)

        log_event(
            logger,
            "transcript_queue_overflow",
            dropped_transcripts=self._dropped_transcripts,
            queue_maxsize=self._transcript_queue.maxsize,
        )


async def _consume_transcripts(
    openai_client: OpenAIResponseClient,
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None],
    speech_queue: asyncio.Queue[SpeechWorkItem | None],
    *,
    interruption_manager: RealtimeInterruptionManager | None = None,
    language_router: SessionLanguageRouter | None = None,
) -> None:
    while True:
        item = await transcript_queue.get()
        try:
            if item is None:
                await _close_queue(speech_queue)
                return

            if language_router is not None and not language_router.is_current(
                item.language.generation
            ):
                log_event(
                    logger,
                    "stale_language_transcript_discarded",
                    request_id=item.request_id,
                    transcript_language=item.language.active_language,
                    language_generation=item.language.generation,
                    current_language_generation=language_router.current_generation,
                )
                continue

            if interruption_manager is not None and not interruption_manager.mark_thinking(
                item.generation,
                request_id=item.request_id,
            ):
                log_event(
                    logger,
                    "stale_transcript_discarded",
                    request_id=item.request_id,
                    generation=item.generation,
                    current_generation=interruption_manager.current_generation,
                )
                continue

            response_task: asyncio.Task | None = None
            try:
                response_task = asyncio.create_task(
                    openai_client.generate_response(
                        item.text,
                        language=item.language,
                        request_id=item.request_id,
                    ),
                    name=f"openai-response-request-{item.request_id or 'unknown'}",
                )
                if interruption_manager is not None:
                    interruption_manager.track_response_task(
                        response_task,
                        generation=item.generation,
                        request_id=item.request_id,
                    )
                try:
                    response = await response_task
                except asyncio.CancelledError:
                    if _current_task_cancel_requested():
                        if not response_task.done():
                            response_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await response_task
                        raise

                    log_event(
                        logger,
                        "openai_response_cancelled",
                        request_id=item.request_id,
                        generation=item.generation,
                        current_generation=(
                            interruption_manager.current_generation
                            if interruption_manager is not None
                            else None
                        ),
                    )
                    continue
                finally:
                    if interruption_manager is not None and response_task is not None:
                        interruption_manager.clear_response_task(response_task)
            except Exception as exc:
                log_error(
                    logger,
                    "openai_response_failed",
                    request_id=item.request_id,
                    error=str(exc),
                )
                if interruption_manager is not None:
                    interruption_manager.mark_listening(
                        item.generation,
                        reason="openai_response_failed",
                        request_id=item.request_id,
                    )
                continue

            if interruption_manager is not None and interruption_manager.is_stale(item.generation):
                log_event(
                    logger,
                    "stale_ai_response_discarded",
                    request_id=item.request_id,
                    response_id=response.response_id,
                    generation=item.generation,
                    current_generation=interruption_manager.current_generation,
                )
                continue

            if language_router is not None and not language_router.is_current(
                item.language.generation
            ):
                log_event(
                    logger,
                    "stale_language_ai_response_discarded",
                    request_id=item.request_id,
                    response_id=response.response_id,
                    response_language=response.language,
                    language_generation=item.language.generation,
                    current_language_generation=language_router.current_generation,
                )
                continue

            if interruption_manager is not None and not interruption_manager.should_emit_ai_response(
                response.text,
                request_id=item.request_id,
                response_id=response.response_id,
            ):
                interruption_manager.mark_listening(
                    item.generation,
                    reason="duplicate_ai_response_suppressed",
                    request_id=item.request_id,
                    response_id=response.response_id,
                )
                continue

            log_event(
                logger,
                "ai_response",
                response=response.text,
                model=response.model,
                response_id=response.response_id,
                request_id=item.request_id,
                response_language=item.language.active_language,
                dominant_language=item.language.dominant_language,
                language_confidence=item.language.confidence,
                language_generation=item.language.generation,
            )
            await _put_if_current(
                speech_queue,
                SpeechWorkItem(
                    text=response.text,
                    model=response.model,
                    response_id=response.response_id,
                    request_id=item.request_id,
                    generation=item.generation,
                    language=item.language,
                ),
                generation=item.generation,
                language_generation=item.language.generation,
                queue_name="speech_queue",
                interruption_manager=interruption_manager,
                language_router=language_router,
            )
        finally:
            transcript_queue.task_done()


async def _consume_speech(
    tts_client: SarvamTTSClient,
    speech_queue: asyncio.Queue[SpeechWorkItem | None],
    playback_queue: asyncio.Queue[PlaybackWorkItem | None],
    *,
    output_sample_rate: int,
    interruption_manager: RealtimeInterruptionManager | None = None,
    language_router: SessionLanguageRouter | None = None,
) -> None:
    while True:
        item = await speech_queue.get()
        try:
            if item is None:
                await _close_queue(playback_queue)
                return

            if language_router is not None and not language_router.is_current(
                item.language.generation
            ):
                log_event(
                    logger,
                    "stale_language_speech_discarded",
                    request_id=item.request_id,
                    response_id=item.response_id,
                    response_language=item.language.active_language,
                    language_generation=item.language.generation,
                    current_language_generation=language_router.current_generation,
                )
                continue

            if interruption_manager is not None and interruption_manager.is_stale(item.generation):
                log_event(
                    logger,
                    "stale_speech_discarded",
                    request_id=item.request_id,
                    response_id=item.response_id,
                    generation=item.generation,
                    current_generation=interruption_manager.current_generation,
                )
                continue

            try:
                log_event(
                    logger,
                    "tts_language_routing",
                    request_id=item.request_id,
                    response_id=item.response_id,
                    response_language=item.language.active_language,
                    dominant_language=item.language.dominant_language,
                    language_confidence=item.language.confidence,
                    language_generation=item.language.generation,
                    target_language_code=item.language.sarvam_language_code,
                    speaker=item.language.speaker,
                )
                tts_task = asyncio.create_task(
                    tts_client.synthesize_pcm(
                        item.text,
                        language_code=item.language.sarvam_language_code,
                        speaker=item.language.speaker,
                        route_language=item.language.active_language,
                    ),
                    name=f"sarvam-tts-request-{item.request_id or 'unknown'}",
                )
                if interruption_manager is not None:
                    interruption_manager.track_tts_task(
                        tts_task,
                        generation=item.generation,
                        request_id=item.request_id,
                        response_id=item.response_id,
                    )
                try:
                    sarvam_audio = await tts_task
                except asyncio.CancelledError:
                    if _current_task_cancel_requested():
                        if not tts_task.done():
                            tts_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await tts_task
                        raise

                    log_event(
                        logger,
                        "tts_cancelled",
                        request_id=item.request_id,
                        response_id=item.response_id,
                        generation=item.generation,
                        current_generation=(
                            interruption_manager.current_generation
                            if interruption_manager is not None
                            else None
                        ),
                    )
                    continue
                finally:
                    if interruption_manager is not None:
                        interruption_manager.clear_tts_task(tts_task)

                if interruption_manager is not None and interruption_manager.is_stale(item.generation):
                    log_event(
                        logger,
                        "stale_tts_audio_discarded",
                        request_id=item.request_id,
                        response_id=item.response_id,
                        generation=item.generation,
                        current_generation=interruption_manager.current_generation,
                    )
                    continue

                if language_router is not None and not language_router.is_current(
                    item.language.generation
                ):
                    log_event(
                        logger,
                        "stale_language_tts_audio_discarded",
                        request_id=item.request_id,
                        response_id=item.response_id,
                        response_language=item.language.active_language,
                        language_generation=item.language.generation,
                        current_language_generation=language_router.current_generation,
                    )
                    continue

                livekit_audio = prepare_for_livekit(
                    sarvam_audio,
                    output_sample_rate=output_sample_rate,
                    output_channels=1,
                )
            except Exception as exc:
                log_error(
                    logger,
                    "sarvam_tts_response_failed",
                    request_id=item.request_id,
                    response_id=item.response_id,
                    error=str(exc),
                )
                if interruption_manager is not None:
                    interruption_manager.mark_listening(
                        item.generation,
                        reason="tts_response_failed",
                        request_id=item.request_id,
                        response_id=item.response_id,
                    )
                continue

            log_event(
                logger,
                "ai_speech_ready",
                request_id=item.request_id,
                response_id=item.response_id,
                model=item.model,
                response_language=item.language.active_language,
                target_language_code=item.language.sarvam_language_code,
                speaker=item.language.speaker,
                duration_ms=round(livekit_audio.duration_seconds * 1000),
                sample_rate=livekit_audio.sample_rate,
                channels=livekit_audio.channels,
            )
            await _put_if_current(
                playback_queue,
                PlaybackWorkItem(
                    audio=livekit_audio,
                    request_id=item.request_id,
                    response_id=item.response_id,
                    model=item.model,
                    text_chars=len(item.text),
                    generation=item.generation,
                    language=item.language,
                ),
                generation=item.generation,
                language_generation=item.language.generation,
                queue_name="playback_queue",
                interruption_manager=interruption_manager,
                language_router=language_router,
            )
        finally:
            speech_queue.task_done()


async def _consume_playback(
    audio_session: LiveKitInboundAudioSubscriber,
    playback_queue: asyncio.Queue[PlaybackWorkItem | None],
    *,
    frame_ms: int,
    interruption_manager: RealtimeInterruptionManager | None = None,
    language_router: SessionLanguageRouter | None = None,
) -> None:
    while True:
        item = await playback_queue.get()
        try:
            if item is None:
                return

            if language_router is not None and not language_router.is_current(
                item.language.generation
            ):
                log_event(
                    logger,
                    "stale_language_playback_discarded",
                    request_id=item.request_id,
                    response_id=item.response_id,
                    response_language=item.language.active_language,
                    language_generation=item.language.generation,
                    current_language_generation=language_router.current_generation,
                )
                continue

            if interruption_manager is not None and not interruption_manager.mark_speaking(
                item.generation,
                request_id=item.request_id,
                response_id=item.response_id,
            ):
                log_event(
                    logger,
                    "stale_playback_discarded",
                    request_id=item.request_id,
                    response_id=item.response_id,
                    generation=item.generation,
                    current_generation=interruption_manager.current_generation,
                )
                continue

            log_event(
                logger,
                "ai_playback_started",
                request_id=item.request_id,
                response_id=item.response_id,
                model=item.model,
                response_language=item.language.active_language,
                target_language_code=item.language.sarvam_language_code,
                speaker=item.language.speaker,
                duration_ms=round(item.audio.duration_seconds * 1000),
                text_chars=item.text_chars,
            )
            if interruption_manager is None:
                if language_router is None:
                    frame_count = await audio_session.play_output_frames(
                        iter_frames(item.audio, frame_ms=frame_ms)
                    )
                    playback_interrupted = False
                else:
                    frame_count = await audio_session.play_output_frames(
                        iter_frames(item.audio, frame_ms=frame_ms),
                        should_stop=lambda: language_router.should_stop_playback(
                            item.language.generation
                        ),
                    )
                    playback_interrupted = language_router.should_stop_playback(
                        item.language.generation
                    )
            else:
                frame_count = await audio_session.play_output_frames(
                    iter_frames(item.audio, frame_ms=frame_ms),
                    should_stop=lambda: (
                        interruption_manager.should_stop_playback(item.generation)
                        or (
                            language_router.should_stop_playback(item.language.generation)
                            if language_router is not None
                            else False
                        )
                    ),
                )
                playback_interrupted = (
                    interruption_manager.should_stop_playback(item.generation)
                    or (
                        language_router.should_stop_playback(item.language.generation)
                        if language_router is not None
                        else False
                    )
                )

            if playback_interrupted:
                with suppress(Exception):
                    audio_session.stop_output_playback()
                if interruption_manager is not None:
                    interruption_manager.conversation.mark_ai_speaking_stopped(
                        request_id=item.request_id,
                        response_id=item.response_id,
                        reason="playback_interrupted",
                        generation=item.generation,
                    )
                    interruption_manager.mark_listening(
                        item.generation,
                        reason="playback_interrupted",
                        request_id=item.request_id,
                        response_id=item.response_id,
                    )
                log_event(
                    logger,
                    "ai_playback_interrupted",
                    request_id=item.request_id,
                    response_id=item.response_id,
                    frame_count=frame_count,
                    generation=item.generation,
                    response_language=item.language.active_language,
                    language_generation=item.language.generation,
                    current_generation=(
                        interruption_manager.current_generation
                        if interruption_manager is not None
                        else None
                    ),
                )
                continue

            log_event(
                logger,
                "ai_playback_completed",
                request_id=item.request_id,
                response_id=item.response_id,
                frame_count=frame_count,
                response_language=item.language.active_language,
                language_generation=item.language.generation,
            )
            if interruption_manager is not None:
                interruption_manager.mark_playback_finished(
                    item.generation,
                    request_id=item.request_id,
                    response_id=item.response_id,
                )
        finally:
            playback_queue.task_done()


async def _put_if_current(
    queue: asyncio.Queue[object | None],
    item: object,
    *,
    generation: int,
    language_generation: int | None = None,
    queue_name: str,
    interruption_manager: RealtimeInterruptionManager | None,
    language_router: SessionLanguageRouter | None = None,
) -> bool:
    if language_router is not None and language_generation is not None:
        if not language_router.is_current(language_generation):
            log_event(
                logger,
                "stale_language_queue_item_discarded",
                queue_name=queue_name,
                language_generation=language_generation,
                current_language_generation=language_router.current_generation,
            )
            return False

    if interruption_manager is None and language_router is None:
        await queue.put(item)
        return True

    def _is_current() -> bool:
        output_current = (
            True if interruption_manager is None else interruption_manager.is_current(generation)
        )
        language_current = (
            True
            if language_router is None or language_generation is None
            else language_router.is_current(language_generation)
        )
        return output_current and language_current

    while _is_current():
        try:
            queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            await asyncio.sleep(0.01)

    log_event(
        logger,
        "stale_queue_item_discarded",
        queue_name=queue_name,
        generation=generation,
        current_generation=(
            interruption_manager.current_generation if interruption_manager is not None else None
        ),
        language_generation=language_generation,
        current_language_generation=(
            language_router.current_generation if language_router is not None else None
        ),
    )
    return False


def _current_task_cancel_requested() -> bool:
    task = asyncio.current_task()
    if task is None:
        return False
    return task.cancelling() > 0


async def _close_queue(queue: asyncio.Queue[object | None]) -> None:
    await queue.put(None)


def _request_queue_close(queue: asyncio.Queue[object | None]) -> None:
    try:
        queue.put_nowait(None)
    except asyncio.QueueFull:
        with suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            queue.task_done()
        queue.put_nowait(None)


async def _raise_first_completed(done: set[asyncio.Task[None]]) -> None:
    for task in done:
        await task
