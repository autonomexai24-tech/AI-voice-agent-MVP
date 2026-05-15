from __future__ import annotations

import asyncio
from contextlib import suppress

from voice_agent.config import AgentConfig
from voice_agent.audio import PcmFrame
from voice_agent.livekit_inbound import LiveKitInboundAudioSubscriber
from voice_agent.logging_config import get_logger, log_event
from voice_agent.providers.sarvam_stt import SarvamStreamingSTTClient, Transcript

logger = get_logger(__name__)


async def run_phase_1b(config: AgentConfig) -> None:
    audio_queue: asyncio.Queue[PcmFrame | None] = asyncio.Queue(
        maxsize=config.sarvam_stt.queue_max_chunks
    )
    stt_client = SarvamStreamingSTTClient(config.sarvam_stt)

    log_event(
        logger,
        "phase_1b_started",
        room_name=config.livekit.room_name,
        stt_sample_rate=config.sarvam_stt.sample_rate,
        frame_ms=config.audio.frame_ms,
        queue_max_chunks=config.sarvam_stt.queue_max_chunks,
    )

    stt_task: asyncio.Task[None] | None = None
    async with LiveKitInboundAudioSubscriber(
        config.livekit,
        audio_queue=audio_queue,
        sample_rate=config.sarvam_stt.sample_rate,
        channels=1,
        frame_ms=config.audio.frame_ms,
    ) as subscriber:
        stt_task = asyncio.create_task(
            stt_client.stream_transcripts(audio_queue, _log_transcript),
            name="sarvam-stt-stream",
        )
        wait_for_audio_task = asyncio.create_task(
            subscriber.wait_for_audio_track(),
            name="livekit-wait-for-audio-track",
        )
        try:
            done, _pending = await asyncio.wait(
                {wait_for_audio_task, stt_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stt_task in done:
                await stt_task
            await wait_for_audio_task
            await stt_task
        finally:
            if not wait_for_audio_task.done():
                wait_for_audio_task.cancel()
                with suppress(asyncio.CancelledError):
                    await wait_for_audio_task
            if stt_task is not None and not stt_task.done():
                stt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stt_task

    log_event(logger, "phase_1b_completed", room_name=config.livekit.room_name)


async def _log_transcript(transcript: Transcript) -> None:
    log_event(
        logger,
        "caller_transcript",
        transcript=transcript.text,
        request_id=transcript.request_id,
        is_final=transcript.is_final,
        metrics=transcript.metrics,
        raw_type=transcript.raw_type,
    )
