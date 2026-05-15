from __future__ import annotations

import asyncio
from contextlib import suppress

from voice_agent.audio import iter_frames, prepare_for_livekit
from voice_agent.config import AgentConfig
from voice_agent.livekit_audio import LiveKitGreetingPublisher
from voice_agent.logging_config import get_logger, log_event
from voice_agent.providers.sarvam_tts import SarvamTTSClient

logger = get_logger(__name__)


async def run_phase_1a(config: AgentConfig) -> None:
    log_event(
        logger,
        "phase_1a_started",
        room_name=config.livekit.room_name,
        output_sample_rate=config.audio.output_sample_rate,
        frame_ms=config.audio.frame_ms,
    )

    async with SarvamTTSClient(config.sarvam) as tts:
        tts_task = asyncio.create_task(tts.synthesize_pcm(config.greeting_text))
        try:
            async with LiveKitGreetingPublisher(
                config.livekit,
                sample_rate=config.audio.output_sample_rate,
                channels=1,
            ) as publisher:
                await publisher.wait_for_remote_participant()
                await publisher.publish_audio_track()

                sarvam_audio = await tts_task
                livekit_audio = prepare_for_livekit(
                    sarvam_audio,
                    output_sample_rate=config.audio.output_sample_rate,
                    output_channels=1,
                )
                log_event(
                    logger,
                    "greeting_audio_ready",
                    duration_ms=round(livekit_audio.duration_seconds * 1000),
                    sample_rate=livekit_audio.sample_rate,
                    channels=livekit_audio.channels,
                )

                await publisher.play_frames(iter_frames(livekit_audio, frame_ms=config.audio.frame_ms))

                if not config.disconnect_after_greeting:
                    log_event(logger, "phase_1a_idle_after_greeting")
                    await asyncio.Event().wait()
        finally:
            if not tts_task.done():
                tts_task.cancel()
                with suppress(asyncio.CancelledError):
                    await tts_task

    log_event(logger, "phase_1a_completed", room_name=config.livekit.room_name)
