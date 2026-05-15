from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass

from voice_agent.audio import PcmFrame
from voice_agent.config import AgentConfig
from voice_agent.livekit_inbound import LiveKitInboundAudioSubscriber
from voice_agent.logging_config import get_logger, log_error, log_event
from voice_agent.providers.openai_text import OpenAIResponseClient
from voice_agent.providers.sarvam_stt import SarvamStreamingSTTClient, Transcript

logger = get_logger(__name__)


@dataclass(frozen=True)
class TranscriptWorkItem:
    text: str
    request_id: str | None


async def run_phase_1c(config: AgentConfig) -> None:
    audio_queue: asyncio.Queue[PcmFrame | None] = asyncio.Queue(
        maxsize=config.sarvam_stt.queue_max_chunks
    )
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue(
        maxsize=config.openai.queue_max_items
    )
    stt_client = SarvamStreamingSTTClient(config.sarvam_stt)
    openai_client = OpenAIResponseClient(
        config.openai,
        business_config=config.business,
        calcom_config=config.calcom,
        fast2sms_config=config.fast2sms,
    )
    await openai_client.load_business_prompt()
    transcript_handler = _TranscriptQueueHandler(transcript_queue)

    log_event(
        logger,
        "phase_1c_started",
        room_name=config.livekit.room_name,
        stt_sample_rate=config.sarvam_stt.sample_rate,
        openai_model=config.openai.model,
        transcript_queue_max_items=config.openai.queue_max_items,
    )

    async with LiveKitInboundAudioSubscriber(
        config.livekit,
        audio_queue=audio_queue,
        sample_rate=config.sarvam_stt.sample_rate,
        channels=1,
        frame_ms=config.audio.frame_ms,
    ) as subscriber:
        stt_task = asyncio.create_task(
            stt_client.stream_transcripts(audio_queue, transcript_handler.handle),
            name="sarvam-stt-stream",
        )
        openai_task = asyncio.create_task(
            _consume_transcripts(openai_client, transcript_queue),
            name="openai-response-consumer",
        )
        wait_for_audio_task = asyncio.create_task(
            subscriber.wait_for_audio_track(),
            name="livekit-wait-for-audio-track",
        )
        try:
            done, _pending = await asyncio.wait(
                {wait_for_audio_task, stt_task, openai_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stt_task in done:
                await stt_task
            if openai_task in done:
                await openai_task
            await wait_for_audio_task
            await stt_task
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
                _close_transcript_queue(transcript_queue)
                openai_task.cancel()
                with suppress(asyncio.CancelledError):
                    await openai_task
            await openai_client.aclose()

    log_event(logger, "phase_1c_completed", room_name=config.livekit.room_name)


class _TranscriptQueueHandler:
    def __init__(self, transcript_queue: asyncio.Queue[TranscriptWorkItem | None]) -> None:
        self._transcript_queue = transcript_queue
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
        if not transcript.text.strip():
            return
        if not transcript.is_final:
            return

        item = TranscriptWorkItem(text=transcript.text.strip(), request_id=transcript.request_id)
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
) -> None:
    while True:
        item = await transcript_queue.get()
        try:
            if item is None:
                return

            try:
                response = await openai_client.generate_response(
                    item.text,
                    request_id=item.request_id,
                )
            except Exception as exc:
                log_error(
                    logger,
                    "openai_response_failed",
                    request_id=item.request_id,
                    error=str(exc),
                )
                continue

            log_event(
                logger,
                "ai_response",
                response=response.text,
                model=response.model,
                response_id=response.response_id,
                request_id=item.request_id,
            )
        finally:
            transcript_queue.task_done()


def _close_transcript_queue(transcript_queue: asyncio.Queue[TranscriptWorkItem | None]) -> None:
    try:
        transcript_queue.put_nowait(None)
    except asyncio.QueueFull:
        with suppress(asyncio.QueueEmpty):
            transcript_queue.get_nowait()
            transcript_queue.task_done()
        transcript_queue.put_nowait(None)
