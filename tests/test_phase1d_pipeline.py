from __future__ import annotations

import asyncio
from contextlib import suppress

from voice_agent.agent_phase1d import (
    PlaybackWorkItem,
    SpeechWorkItem,
    TranscriptWorkItem,
    _TranscriptQueueHandler,
    _consume_playback,
    _consume_speech,
    _consume_transcripts,
)
from voice_agent.audio import PcmAudio, PcmFrame
from voice_agent.interruption import ConversationState, RealtimeInterruptionManager
from voice_agent.language import SessionLanguageRouter
from voice_agent.providers.openai_text import AIResponse
from voice_agent.providers.sarvam_stt import Transcript


def test_phase1d_consumers_chain_text_to_tts_to_playback_frames() -> None:
    asyncio.run(_run_phase1d_consumer_chain_test())


async def _run_phase1d_consumer_chain_test() -> None:
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue()
    speech_queue: asyncio.Queue[SpeechWorkItem | None] = asyncio.Queue()
    playback_queue: asyncio.Queue[PlaybackWorkItem | None] = asyncio.Queue()
    openai_client = _FakeOpenAIClient()
    tts_client = _FakeTTSClient()
    audio_session = _FakeAudioSession()

    await transcript_queue.put(TranscriptWorkItem(text="Can you help me?", request_id="req-1"))
    await transcript_queue.put(None)

    await asyncio.gather(
        _consume_transcripts(openai_client, transcript_queue, speech_queue),
        _consume_speech(
            tts_client,
            speech_queue,
            playback_queue,
            output_sample_rate=48000,
        ),
        _consume_playback(audio_session, playback_queue, frame_ms=20),
    )

    assert openai_client.transcripts == ["Can you help me?"]
    assert tts_client.texts == ["Sure, I can help. What do you need?"]
    assert tts_client.language_codes == ["en-IN"]
    assert len(audio_session.frames) == 5
    assert {frame.sample_rate for frame in audio_session.frames} == {48000}
    assert {frame.channels for frame in audio_session.frames} == {1}
    assert {frame.samples_per_channel for frame in audio_session.frames} == {960}


def test_transcript_handler_only_queues_final_non_empty_transcripts() -> None:
    asyncio.run(_run_transcript_handler_test())


async def _run_transcript_handler_test() -> None:
    queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue()
    handler = _TranscriptQueueHandler(queue)

    await handler.handle(
        Transcript(
            text="partial",
            request_id="req-1",
            is_final=False,
            metrics={},
            raw_type="data",
        )
    )
    await handler.handle(
        Transcript(
            text="   ",
            request_id="req-2",
            is_final=True,
            metrics={},
            raw_type="data",
        )
    )
    await handler.handle(
        Transcript(
            text=" final response ",
            request_id="req-3",
            is_final=True,
            metrics={},
            raw_type="data",
        )
    )

    item = queue.get_nowait()
    assert item == TranscriptWorkItem(text="final response", request_id="req-3")
    assert queue.empty()


def test_interruption_manager_stops_playback_flushes_queues_and_cancels_tts() -> None:
    asyncio.run(_run_interruption_manager_test())


async def _run_interruption_manager_test() -> None:
    speech_queue: asyncio.Queue[object | None] = asyncio.Queue()
    playback_queue: asyncio.Queue[object | None] = asyncio.Queue()
    await speech_queue.put(object())
    await playback_queue.put(object())

    stop_calls: list[int] = []
    manager = RealtimeInterruptionManager(
        speech_queue=speech_queue,
        playback_queue=playback_queue,
        speech_rms_threshold=100.0,
    )
    manager.set_playback_stop_callback(lambda: stop_calls.append(75) or 75)

    async def _never() -> None:
        await asyncio.Future()

    tts_task = asyncio.create_task(_never(), name="test-active-tts")
    manager.mark_speaking(0, request_id="req-1", response_id="resp-1")
    manager.track_tts_task(
        tts_task,
        generation=0,
        request_id="req-1",
        response_id="resp-1",
    )

    manager.observe_caller_audio(
        PcmFrame(
            data=b"\xe8\x03" * 160,
            sample_rate=16000,
            channels=1,
            samples_per_channel=160,
        )
    )

    with suppress(asyncio.CancelledError):
        await tts_task

    assert manager.state == ConversationState.LISTENING
    assert manager.current_generation == 1
    assert manager.was_interrupted(0)
    assert stop_calls == [75]
    assert tts_task.cancelled()
    assert speech_queue.empty()
    assert playback_queue.empty()


def test_final_transcript_interrupts_speaking_and_queues_new_generation() -> None:
    asyncio.run(_run_transcript_interruption_test())


async def _run_transcript_interruption_test() -> None:
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue()
    manager = RealtimeInterruptionManager()
    manager.set_playback_stop_callback(lambda: 0)
    manager.mark_speaking(0, request_id="req-old", response_id="resp-old")
    handler = _TranscriptQueueHandler(transcript_queue, interruption_manager=manager)

    await handler.handle(
        Transcript(
            text="Actually I need something else",
            request_id="req-new",
            is_final=True,
            metrics={},
            raw_type="data",
        )
    )

    item = transcript_queue.get_nowait()
    assert item == TranscriptWorkItem(
        text="Actually I need something else",
        request_id="req-new",
        generation=1,
    )
    assert manager.state == ConversationState.LISTENING


def test_active_tts_cancel_does_not_orphan_speech_consumer() -> None:
    asyncio.run(_run_tts_cancellation_test())


async def _run_tts_cancellation_test() -> None:
    speech_queue: asyncio.Queue[SpeechWorkItem | None] = asyncio.Queue()
    playback_queue: asyncio.Queue[PlaybackWorkItem | None] = asyncio.Queue()
    manager = RealtimeInterruptionManager(
        speech_queue=speech_queue,
        playback_queue=playback_queue,
    )
    manager.set_playback_stop_callback(lambda: 0)
    tts_client = _SlowTTSClient()

    await speech_queue.put(
        SpeechWorkItem(
            text="This response should be cancelled.",
            request_id="req-1",
            response_id="resp-1",
            model="gpt-4o-mini",
            generation=0,
        )
    )
    consumer = asyncio.create_task(
        _consume_speech(
            tts_client,
            speech_queue,
            playback_queue,
            output_sample_rate=48000,
            interruption_manager=manager,
        )
    )

    await tts_client.started.wait()
    manager.mark_speaking(0, request_id="req-1", response_id="resp-playing")
    manager.request_interruption(source="test")
    await speech_queue.put(None)
    await asyncio.wait_for(consumer, timeout=1)

    assert tts_client.cancelled
    assert playback_queue.get_nowait() is None


def test_playback_consumer_stops_current_audio_on_interruption() -> None:
    asyncio.run(_run_playback_interruption_test())


async def _run_playback_interruption_test() -> None:
    playback_queue: asyncio.Queue[PlaybackWorkItem | None] = asyncio.Queue()
    manager = RealtimeInterruptionManager(
        playback_queue=playback_queue,
        speech_rms_threshold=100.0,
    )
    audio_session = _InterruptingAudioSession(manager)
    manager.set_playback_stop_callback(audio_session.stop_output_playback)

    await playback_queue.put(
        PlaybackWorkItem(
            audio=PcmAudio(data=b"\x00\x00" * (960 * 3), sample_rate=48000, channels=1),
            request_id="req-1",
            response_id="resp-1",
            model="gpt-4o-mini",
            text_chars=24,
            generation=0,
        )
    )
    await playback_queue.put(None)

    await _consume_playback(
        audio_session,
        playback_queue,
        frame_ms=20,
        interruption_manager=manager,
    )

    assert len(audio_session.frames) == 1
    assert audio_session.stop_calls >= 1
    assert manager.state == ConversationState.LISTENING
    assert manager.current_generation == 1


def test_language_router_discards_stale_transcript_generation() -> None:
    asyncio.run(_run_stale_language_transcript_test())


async def _run_stale_language_transcript_test() -> None:
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue()
    speech_queue: asyncio.Queue[SpeechWorkItem | None] = asyncio.Queue()
    openai_client = _FakeOpenAIClient()
    language_router = SessionLanguageRouter(initial_language="english")

    english_language = language_router.snapshot()
    hindi_result = await language_router.route_text(
        "\u092e\u0941\u091d\u0947 \u092e\u0926\u0926 \u091a\u093e\u0939\u093f\u090f",
        request_id="req-hi",
        is_final=True,
    )

    await transcript_queue.put(
        TranscriptWorkItem(
            text="Can you help me?",
            request_id="req-en",
            language=english_language,
        )
    )
    await transcript_queue.put(
        TranscriptWorkItem(
            text="\u092e\u0941\u091d\u0947 \u092e\u0926\u0926 \u091a\u093e\u0939\u093f\u090f",
            request_id="req-hi",
            language=hindi_result.snapshot,
        )
    )
    await transcript_queue.put(None)

    await _consume_transcripts(
        openai_client,
        transcript_queue,
        speech_queue,
        language_router=language_router,
    )

    speech_item = await speech_queue.get()
    close_item = await speech_queue.get()

    assert openai_client.transcripts == [
        "\u092e\u0941\u091d\u0947 \u092e\u0926\u0926 \u091a\u093e\u0939\u093f\u090f"
    ]
    assert openai_client.languages == ["hindi"]
    assert isinstance(speech_item, SpeechWorkItem)
    assert speech_item.language.active_language == "hindi"
    assert close_item is None


def test_transcript_handler_language_switch_aborts_pending_output() -> None:
    asyncio.run(_run_language_switch_abort_test())


async def _run_language_switch_abort_test() -> None:
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue()
    speech_queue: asyncio.Queue[object | None] = asyncio.Queue()
    playback_queue: asyncio.Queue[object | None] = asyncio.Queue()
    await speech_queue.put(object())
    await playback_queue.put(object())

    language_router = SessionLanguageRouter(initial_language="english")
    manager = RealtimeInterruptionManager(
        speech_queue=speech_queue,
        playback_queue=playback_queue,
    )
    stop_calls: list[int] = []
    manager.set_playback_stop_callback(lambda: stop_calls.append(40) or 40)
    handler = _TranscriptQueueHandler(
        transcript_queue,
        interruption_manager=manager,
        language_router=language_router,
    )

    await handler.handle(
        Transcript(
            text="\u0928\u092e\u0938\u094d\u0924\u0947, \u092e\u0926\u0926 \u091a\u093e\u0939\u093f\u090f",
            request_id="req-hi",
            is_final=True,
            metrics={},
            raw_type="data",
        )
    )

    item = transcript_queue.get_nowait()

    assert item.language.active_language == "hindi"
    assert item.generation == manager.current_generation
    assert manager.current_generation == 1
    assert speech_queue.empty()
    assert playback_queue.empty()
    assert stop_calls == [40]


def test_language_switch_cancels_inflight_openai_response() -> None:
    asyncio.run(_run_language_switch_cancels_openai_test())


async def _run_language_switch_cancels_openai_test() -> None:
    transcript_queue: asyncio.Queue[TranscriptWorkItem | None] = asyncio.Queue()
    speech_queue: asyncio.Queue[SpeechWorkItem | None] = asyncio.Queue()
    language_router = SessionLanguageRouter(initial_language="english")
    manager = RealtimeInterruptionManager(speech_queue=speech_queue)
    openai_client = _SlowSwitchingOpenAIClient()
    handler = _TranscriptQueueHandler(
        transcript_queue,
        interruption_manager=manager,
        language_router=language_router,
    )

    await transcript_queue.put(
        TranscriptWorkItem(
            text="Can you help me?",
            request_id="req-en",
            generation=manager.current_generation,
            language=language_router.snapshot(),
        )
    )
    consumer = asyncio.create_task(
        _consume_transcripts(
            openai_client,
            transcript_queue,
            speech_queue,
            interruption_manager=manager,
            language_router=language_router,
        )
    )

    await openai_client.started.wait()
    await handler.handle(
        Transcript(
            text="\u092e\u0941\u091d\u0947 \u092e\u0926\u0926 \u091a\u093e\u0939\u093f\u090f",
            request_id="req-hi",
            is_final=True,
            metrics={},
            raw_type="data",
        )
    )
    await transcript_queue.put(None)
    await asyncio.wait_for(consumer, timeout=1)

    speech_item = await speech_queue.get()
    close_item = await speech_queue.get()

    assert openai_client.cancelled is True
    assert openai_client.transcripts == [
        "Can you help me?",
        "\u092e\u0941\u091d\u0947 \u092e\u0926\u0926 \u091a\u093e\u0939\u093f\u090f",
    ]
    assert isinstance(speech_item, SpeechWorkItem)
    assert speech_item.request_id == "req-hi"
    assert speech_item.language.active_language == "hindi"
    assert close_item is None


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.transcripts: list[str] = []
        self.languages: list[str] = []

    async def generate_response(
        self,
        transcript: str,
        *,
        language=None,
        request_id=None,
    ) -> AIResponse:
        self.transcripts.append(transcript)
        self.languages.append(language.active_language if language is not None else "unknown")
        return AIResponse(
            text="Sure, I can help. What do you need?",
            model="gpt-4o-mini",
            response_id="resp-1",
            language=language.active_language if language is not None else "english",
        )


class _FakeTTSClient:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.language_codes: list[str] = []
        self.speakers: list[str] = []

    async def synthesize_pcm(
        self,
        text: str,
        *,
        language_code: str | None = None,
        speaker: str | None = None,
        route_language: str | None = None,
    ) -> PcmAudio:
        self.texts.append(text)
        self.language_codes.append(language_code or "")
        self.speakers.append(speaker or "")
        return PcmAudio(data=b"\x00\x00" * 1600, sample_rate=16000, channels=1)


class _SlowTTSClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def synthesize_pcm(self, _text: str, **_kwargs) -> PcmAudio:
        self.started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _SlowSwitchingOpenAIClient:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False
        self.transcripts: list[str] = []

    async def generate_response(
        self,
        transcript: str,
        *,
        language=None,
        request_id=None,
    ) -> AIResponse:
        self.transcripts.append(transcript)
        if len(self.transcripts) == 1:
            self.started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return AIResponse(
            text="\u0920\u0940\u0915 \u0939\u0948, \u092e\u0948\u0902 \u092e\u0926\u0926 \u0915\u0930\u0924\u093e \u0939\u0942\u0902.",
            model="gpt-4o-mini",
            response_id="resp-hi",
            language=language.active_language if language is not None else "hindi",
        )


class _FakeAudioSession:
    def __init__(self) -> None:
        self.frames: list[PcmFrame] = []

    async def play_output_frames(self, frames) -> int:
        self.frames.extend(frames)
        return len(self.frames)


class _InterruptingAudioSession:
    def __init__(self, manager: RealtimeInterruptionManager) -> None:
        self._manager = manager
        self.frames: list[PcmFrame] = []
        self.stop_calls = 0

    async def play_output_frames(self, frames, *, should_stop=None) -> int:
        for frame in frames:
            if should_stop is not None and should_stop():
                return len(self.frames)
            self.frames.append(frame)
            self._manager.observe_caller_audio(
                PcmFrame(
                    data=b"\xe8\x03" * 160,
                    sample_rate=16000,
                    channels=1,
                    samples_per_channel=160,
                )
            )
            if should_stop is not None and should_stop():
                return len(self.frames)
        return len(self.frames)

    def stop_output_playback(self) -> int:
        self.stop_calls += 1
        return 20
