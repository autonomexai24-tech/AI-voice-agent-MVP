from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable

from livekit import rtc

from voice_agent.audio import PcmFrame
from voice_agent.config import LiveKitConfig
from voice_agent.livekit_auth import build_livekit_token
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


class LiveKitGreetingPublisher:
    def __init__(self, config: LiveKitConfig, *, sample_rate: int, channels: int) -> None:
        self._config = config
        self._sample_rate = sample_rate
        self._channels = channels
        self._room = rtc.Room()
        self._participant_connected = asyncio.Event()
        self._audio_source: rtc.AudioSource | None = None

        @self._room.on("participant_connected")
        def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
            log_event(
                logger,
                "livekit_remote_participant_connected",
                participant_identity=participant.identity,
            )
            self._participant_connected.set()

        @self._room.on("participant_disconnected")
        def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            log_event(
                logger,
                "livekit_remote_participant_disconnected",
                participant_identity=participant.identity,
            )

    async def __aenter__(self) -> "LiveKitGreetingPublisher":
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        token = self._build_token()
        log_event(
            logger,
            "livekit_connect_started",
            url=self._config.url,
            room_name=self._config.room_name,
            identity=self._config.identity,
        )
        await self._room.connect(self._config.url, token)
        log_event(
            logger,
            "livekit_connect_completed",
            room_name=self._config.room_name,
            local_identity=self._config.identity,
        )

    async def wait_for_remote_participant(self) -> None:
        if self._room.remote_participants:
            log_event(
                logger,
                "livekit_remote_participant_already_present",
                participant_count=len(self._room.remote_participants),
            )
            return

        log_event(
            logger,
            "livekit_waiting_for_remote_participant",
            timeout_seconds=self._config.wait_for_participant_timeout_seconds,
        )
        await asyncio.wait_for(
            self._participant_connected.wait(),
            timeout=self._config.wait_for_participant_timeout_seconds,
        )

    async def publish_audio_track(self) -> None:
        self._audio_source = rtc.AudioSource(
            self._sample_rate,
            self._channels,
            queue_size_ms=self._config.audio_queue_ms,
        )
        track = rtc.LocalAudioTrack.create_audio_track("ai-greeting", self._audio_source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self._room.local_participant.publish_track(track, options)
        log_event(
            logger,
            "livekit_audio_track_published",
            sample_rate=self._sample_rate,
            channels=self._channels,
            queue_size_ms=self._config.audio_queue_ms,
        )

    async def play_frames(self, frames: Iterable[PcmFrame]) -> None:
        if self._audio_source is None:
            raise RuntimeError("audio track must be published before playback")

        frame_count = 0
        for frame in frames:
            audio_frame = rtc.AudioFrame(
                data=frame.data,
                sample_rate=frame.sample_rate,
                num_channels=frame.channels,
                samples_per_channel=frame.samples_per_channel,
            )
            await self._audio_source.capture_frame(audio_frame)
            frame_count += 1

        wait_for_playout = getattr(self._audio_source, "wait_for_playout", None)
        if wait_for_playout is not None:
            maybe_awaitable = wait_for_playout()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

        log_event(logger, "livekit_greeting_playback_completed", frame_count=frame_count)

    async def disconnect(self) -> None:
        log_event(logger, "livekit_disconnect_started", room_name=self._config.room_name)
        maybe_awaitable = self._room.disconnect()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
        log_event(logger, "livekit_disconnect_completed", room_name=self._config.room_name)

    def _build_token(self) -> str:
        return build_livekit_token(self._config)
