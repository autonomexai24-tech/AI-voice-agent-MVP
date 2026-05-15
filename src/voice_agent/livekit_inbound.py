from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import Callable, Iterable

from livekit import rtc

from voice_agent.audio import PcmFrame
from voice_agent.config import LiveKitConfig
from voice_agent.livekit_auth import build_livekit_token
from voice_agent.logging_config import get_logger, log_error, log_event

logger = get_logger(__name__)

AudioQueue = asyncio.Queue[PcmFrame | None]
AudioObserver = Callable[[PcmFrame], None]
PlaybackStopCheck = Callable[[], bool]


class LiveKitInboundAudioSubscriber:
    def __init__(
        self,
        config: LiveKitConfig,
        *,
        audio_queue: AudioQueue,
        sample_rate: int,
        channels: int,
        frame_ms: int,
        audio_observer: AudioObserver | None = None,
        room: rtc.Room | None = None,
    ) -> None:
        self._config = config
        self._audio_queue = audio_queue
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_ms = frame_ms
        self._audio_observer = audio_observer
        self._external_room = room is not None
        self._room = room if room is not None else rtc.Room()
        self._audio_track_ready = asyncio.Event()
        self._output_audio_source: rtc.AudioSource | None = None
        self._track_tasks: set[asyncio.Task[None]] = set()
        self._streams: dict[str, rtc.AudioStream] = {}
        self._queue_closed = False
        self._dropped_frames = 0

        @self._room.on("participant_connected")
        def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
            log_event(
                logger,
                "livekit_remote_participant_connected",
                participant_identity=participant.identity,
            )

        @self._room.on("participant_disconnected")
        def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            log_event(
                logger,
                "livekit_remote_participant_disconnected",
                participant_identity=participant.identity,
            )

        @self._room.on("track_published")
        def _on_track_published(
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            log_event(
                logger,
                "livekit_track_published",
                participant_identity=participant.identity,
                publication_sid=publication.sid,
                track_kind=publication.kind,
                track_subscribed=publication.subscribed,
                track_available=publication.track is not None,
            )
            if publication.kind != rtc.TrackKind.KIND_AUDIO:
                return
            if publication.track is not None:
                self._start_audio_reader(publication.track, publication, participant)
            elif not publication.subscribed:
                publication.set_subscribed(True)
                log_event(
                    logger,
                    "livekit_audio_track_subscription_requested",
                    participant_identity=participant.identity,
                    publication_sid=publication.sid,
                    source="track_published_handler",
                )

        @self._room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            log_event(
                logger,
                "livekit_track_subscribed_event",
                participant_identity=participant.identity,
                publication_sid=publication.sid,
                track_sid=track.sid,
                track_kind=track.kind,
                is_audio=track.kind == rtc.TrackKind.KIND_AUDIO,
            )
            self._start_audio_reader(track, publication, participant)

        @self._room.on("track_unsubscribed")
        def _on_track_unsubscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            log_event(
                logger,
                "livekit_track_unsubscribed",
                participant_identity=participant.identity,
                publication_sid=publication.sid,
                track_sid=track.sid,
            )

    async def __aenter__(self) -> "LiveKitInboundAudioSubscriber":
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        if self._external_room:
            remote_count = len(self._room.remote_participants)
            log_event(
                logger,
                "livekit_using_external_room",
                room_name=getattr(self._room, 'name', '<unknown>'),
                remote_participant_count=remote_count,
            )
            self._subscribe_existing_audio_tracks()
            return

        token = build_livekit_token(self._config)
        log_event(
            logger,
            "livekit_connect_started",
            url=self._config.url,
            room_name=self._config.room_name,
            identity=self._config.identity,
        )
        await self._room.connect(self._config.url, token)
        remote_count = len(self._room.remote_participants)
        log_event(
            logger,
            "livekit_connect_completed",
            room_name=self._config.room_name,
            local_identity=self._config.identity,
            remote_participant_count=remote_count,
        )
        self._subscribe_existing_audio_tracks()

    async def publish_output_audio_track(
        self,
        *,
        sample_rate: int,
        channels: int,
        track_name: str = "ai-response",
    ) -> None:
        if self._output_audio_source is not None:
            return

        self._output_audio_source = rtc.AudioSource(
            sample_rate,
            channels,
            queue_size_ms=self._config.audio_queue_ms,
        )
        track = rtc.LocalAudioTrack.create_audio_track(track_name, self._output_audio_source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self._room.local_participant.publish_track(track, options)
        log_event(
            logger,
            "livekit_output_audio_track_published",
            track_name=track_name,
            sample_rate=sample_rate,
            channels=channels,
            queue_size_ms=self._config.audio_queue_ms,
        )

    async def play_output_frames(
        self,
        frames: Iterable[PcmFrame],
        *,
        should_stop: PlaybackStopCheck | None = None,
    ) -> int:
        if self._output_audio_source is None:
            raise RuntimeError("output audio track must be published before playback")

        frame_count = 0
        for frame in frames:
            if should_stop is not None and should_stop():
                return frame_count

            audio_frame = rtc.AudioFrame(
                data=frame.data,
                sample_rate=frame.sample_rate,
                num_channels=frame.channels,
                samples_per_channel=frame.samples_per_channel,
            )
            await self._output_audio_source.capture_frame(audio_frame)
            frame_count += 1
            if should_stop is not None and should_stop():
                return frame_count

        wait_for_playout = getattr(self._output_audio_source, "wait_for_playout", None)
        if wait_for_playout is not None:
            maybe_awaitable = wait_for_playout()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

        return frame_count

    def stop_output_playback(self) -> int:
        if self._output_audio_source is None:
            return 0

        queued_duration_ms = round(self._output_audio_source.queued_duration * 1000)
        clear_queue = getattr(self._output_audio_source, "clear_queue", None)
        if clear_queue is not None:
            clear_queue()
        return queued_duration_ms

    async def wait_for_audio_track(self) -> None:
        if self._audio_track_ready.is_set():
            return
        timeout = self._config.wait_for_participant_timeout_seconds
        log_event(
            logger,
            "livekit_waiting_for_audio_track",
            timeout_seconds=timeout,
            remote_participant_count=len(self._room.remote_participants),
        )
        poll_interval = min(5.0, timeout / 2)
        elapsed = 0.0
        while elapsed < timeout:
            remaining = timeout - elapsed
            wait_time = min(poll_interval, remaining)
            try:
                await asyncio.wait_for(
                    self._audio_track_ready.wait(),
                    timeout=wait_time,
                )
                return
            except asyncio.TimeoutError:
                elapsed += wait_time
                if self._audio_track_ready.is_set():
                    return
                self._subscribe_existing_audio_tracks()
                log_event(
                    logger,
                    "livekit_audio_track_poll",
                    elapsed_seconds=round(elapsed, 1),
                    timeout_seconds=timeout,
                    remote_participant_count=len(self._room.remote_participants),
                    active_streams=len(self._streams),
                    participants=[
                        {
                            "identity": p.identity,
                            "sid": p.sid,
                            "track_count": len(p.track_publications),
                            "audio_tracks": [
                                {
                                    "sid": pub.sid,
                                    "kind": pub.kind,
                                    "subscribed": pub.subscribed,
                                    "track_available": pub.track is not None,
                                }
                                for pub in p.track_publications.values()
                                if pub.kind == rtc.TrackKind.KIND_AUDIO
                            ],
                        }
                        for p in self._room.remote_participants.values()
                    ],
                )
        raise asyncio.TimeoutError(
            f"no audio track received within {timeout}s; "
            f"remote_participants={len(self._room.remote_participants)}, "
            f"active_streams={len(self._streams)}"
        )

    async def disconnect(self) -> None:
        await self._close_streams()
        if self._external_room:
            self._close_audio_queue()
            log_event(
                logger,
                "livekit_external_room_cleanup_completed",
                room_name=getattr(self._room, 'name', '<unknown>'),
            )
            return

        log_event(logger, "livekit_disconnect_started", room_name=self._config.room_name)
        maybe_awaitable = self._room.disconnect()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable
        self._close_audio_queue()
        log_event(logger, "livekit_disconnect_completed", room_name=self._config.room_name)

    def _subscribe_existing_audio_tracks(self) -> None:
        participants = list(self._room.remote_participants.values())
        if not participants:
            log_event(logger, "livekit_no_remote_participants_at_connect")
            return

        for participant in participants:
            publications = list(participant.track_publications.values())
            log_event(
                logger,
                "livekit_scanning_participant_tracks",
                participant_identity=participant.identity,
                participant_sid=participant.sid,
                publication_count=len(publications),
                track_kinds=[pub.kind for pub in publications],
            )
            for publication in publications:
                if publication.kind != rtc.TrackKind.KIND_AUDIO:
                    log_event(
                        logger,
                        "livekit_skipping_non_audio_track",
                        participant_identity=participant.identity,
                        publication_sid=publication.sid,
                        track_kind=publication.kind,
                    )
                    continue
                if publication.track is not None:
                    self._start_audio_reader(publication.track, publication, participant)
                elif not publication.subscribed:
                    publication.set_subscribed(True)
                    log_event(
                        logger,
                        "livekit_audio_track_subscription_requested",
                        participant_identity=participant.identity,
                        publication_sid=publication.sid,
                        source="existing_track_scan",
                    )
                else:
                    log_event(
                        logger,
                        "livekit_audio_track_subscription_in_flight",
                        participant_identity=participant.identity,
                        publication_sid=publication.sid,
                        subscribed=publication.subscribed,
                        track_available=False,
                    )

    def _start_audio_reader(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if track.kind != rtc.TrackKind.KIND_AUDIO:
            log_event(
                logger,
                "livekit_start_audio_reader_skipped_non_audio",
                participant_identity=participant.identity,
                track_sid=track.sid,
                track_kind=track.kind,
            )
            return
        if track.sid in self._streams:
            return

        stream = rtc.AudioStream.from_track(
            track=track,
            capacity=0,
            sample_rate=self._sample_rate,
            num_channels=self._channels,
            frame_size_ms=self._frame_ms,
        )
        self._streams[track.sid] = stream
        task = asyncio.create_task(
            self._read_audio_stream(stream, track, publication, participant),
            name=f"livekit-audio-reader-{track.sid}",
        )
        self._track_tasks.add(task)
        task.add_done_callback(self._track_tasks.discard)
        self._audio_track_ready.set()
        log_event(
            logger,
            "livekit_audio_track_subscribed",
            participant_identity=participant.identity,
            publication_sid=publication.sid,
            track_sid=track.sid,
            sample_rate=self._sample_rate,
            channels=self._channels,
            frame_ms=self._frame_ms,
        )

    async def _read_audio_stream(
        self,
        stream: rtc.AudioStream,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        frame_count = 0
        try:
            async for event in stream:
                frame = event.frame
                pcm_frame = PcmFrame(
                    data=frame.data.cast("B").tobytes(),
                    sample_rate=frame.sample_rate,
                    channels=frame.num_channels,
                    samples_per_channel=frame.samples_per_channel,
                )
                self._observe_audio_frame(pcm_frame)
                self._enqueue_audio_frame(pcm_frame)
                frame_count += 1
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()
            self._streams.pop(track.sid, None)
            log_event(
                logger,
                "livekit_audio_stream_closed",
                participant_identity=participant.identity,
                publication_sid=publication.sid,
                track_sid=track.sid,
                frame_count=frame_count,
            )
            if not self._streams:
                self._close_audio_queue()

    def _observe_audio_frame(self, frame: PcmFrame) -> None:
        if self._audio_observer is None:
            return
        try:
            self._audio_observer(frame)
        except Exception as exc:
            log_error(logger, "livekit_audio_observer_failed", error=str(exc))

    async def _close_streams(self) -> None:
        for stream in list(self._streams.values()):
            with contextlib.suppress(Exception):
                await stream.aclose()
        for task in list(self._track_tasks):
            task.cancel()
        for task in list(self._track_tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._streams.clear()

    def _close_audio_queue(self) -> None:
        if self._queue_closed:
            return
        self._queue_closed = True
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            self._audio_queue.put_nowait(None)

    def _enqueue_audio_frame(self, frame: PcmFrame) -> None:
        try:
            self._audio_queue.put_nowait(frame)
            return
        except asyncio.QueueFull:
            self._dropped_frames += 1
            with contextlib.suppress(asyncio.QueueEmpty):
                self._audio_queue.get_nowait()
                self._audio_queue.task_done()
            self._audio_queue.put_nowait(frame)

        if self._dropped_frames == 1 or self._dropped_frames % 100 == 0:
            log_event(
                logger,
                "livekit_audio_queue_overflow",
                dropped_frames=self._dropped_frames,
                queue_maxsize=self._audio_queue.maxsize,
            )
