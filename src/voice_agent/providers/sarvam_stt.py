from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from websockets.exceptions import ConnectionClosed

try:
    from websockets.legacy.client import connect as _ws_connect  # type: ignore

    _WS_HEADER_KWARG = "extra_headers"
except ImportError:
    from websockets import connect as _ws_connect  # type: ignore

    _WS_HEADER_KWARG = "additional_headers"

from voice_agent.audio import PcmFrame
from voice_agent.config import SarvamSTTConfig
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)

AudioQueue = asyncio.Queue[PcmFrame | None]
TranscriptHandler = Callable[["Transcript"], Awaitable[None]]


@dataclass(frozen=True)
class Transcript:
    text: str
    request_id: str | None
    is_final: bool
    metrics: dict[str, Any]
    raw_type: str | None


class SarvamStreamingSTTClient:
    def __init__(self, config: SarvamSTTConfig) -> None:
        self._config = config

    async def stream_transcripts(
        self,
        audio_queue: AudioQueue,
        transcript_handler: TranscriptHandler,
    ) -> None:
        url = build_sarvam_stt_url(self._config)
        headers = {"api-subscription-key": self._config.api_key}

        log_event(
            logger,
            "sarvam_stt_connect_started",
            url=self._config.url,
            full_ws_url=_redact_key(url),
            model=self._config.model,
            mode=self._config.mode,
            language_code=self._config.language_code,
            sample_rate=self._config.sample_rate,
            input_audio_codec=self._config.input_audio_codec,
            payload_audio_encoding=_SARVAM_AUDIO_MIME_TYPE,
            config_audio_encoding=self._config.audio_encoding,
            header_keys=list(headers.keys()),
            ws_header_kwarg=_WS_HEADER_KWARG,
        )
        try:
            async with _ws_connect(url, **{_WS_HEADER_KWARG: headers}) as websocket:
                log_event(logger, "sarvam_stt_connect_completed")
                sender = asyncio.create_task(
                    self._send_audio(websocket, audio_queue),
                    name="sarvam-stt-audio-sender",
                )
                receiver = asyncio.create_task(
                    self._receive_transcripts(websocket, transcript_handler),
                    name="sarvam-stt-transcript-receiver",
                )
                try:
                    await asyncio.gather(sender, receiver)
                finally:
                    for task in (sender, receiver):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
        except ConnectionClosed as exc:
            log_event(
                logger,
                "sarvam_stt_ws_closed_during_connect",
                close_code=exc.rcvd.code if exc.rcvd else None,
                close_reason=exc.rcvd.reason if exc.rcvd else None,
            )
            raise
        except Exception as exc:
            log_event(
                logger,
                "sarvam_stt_connect_error",
                error_type=type(exc).__name__,
                error_message=str(exc),
                error_args=[str(a) for a in getattr(exc, 'args', ())],
            )
            raise

    async def _send_audio(self, websocket: Any, audio_queue: AudioQueue) -> None:
        chunk_count = 0
        total_bytes = 0
        while True:
            frame = await audio_queue.get()
            try:
                if frame is None:
                    await websocket.close()
                    log_event(
                        logger,
                        "sarvam_stt_audio_sender_closed",
                        chunk_count=chunk_count,
                        total_audio_bytes=total_bytes,
                    )
                    return

                json_msg = encode_audio_message(frame)
                if chunk_count < 3:
                    has_riff = frame.data[:4] == b"RIFF" if len(frame.data) >= 4 else False
                    first_bytes_hex = frame.data[:8].hex() if len(frame.data) >= 8 else frame.data.hex()
                    log_event(
                        logger,
                        "sarvam_stt_audio_frame_sent",
                        chunk_index=chunk_count,
                        raw_pcm_bytes=len(frame.data),
                        json_msg_len=len(json_msg),
                        frame_sample_rate=frame.sample_rate,
                        frame_channels=frame.channels,
                        frame_samples_per_channel=frame.samples_per_channel,
                        ws_payload_type="text_json_b64",
                        audio_encoding=_SARVAM_AUDIO_MIME_TYPE,
                        input_audio_codec=self._config.input_audio_codec,
                        has_riff_header=has_riff,
                        first_bytes_hex=first_bytes_hex,
                    )
                await websocket.send(json_msg)
                chunk_count += 1
                total_bytes += len(frame.data)
            finally:
                audio_queue.task_done()

    async def _receive_transcripts(
        self,
        websocket: Any,
        transcript_handler: TranscriptHandler,
    ) -> None:
        try:
            async for raw_message in websocket:
                message = _decode_message(raw_message)
                transcript = extract_transcript(message)
                if transcript is not None:
                    await transcript_handler(transcript)
                else:
                    log_event(
                        logger,
                        "sarvam_stt_control_message",
                        message_type=message.get("type"),
                        message_data=message.get("data"),
                        message_error=message.get("error"),
                        message_detail=message.get("detail"),
                        message_code=message.get("code"),
                        full_message=message,
                    )
        except ConnectionClosed as exc:
            log_event(
                logger,
                "sarvam_stt_connection_closed",
                close_code=exc.rcvd.code if exc.rcvd else None,
                close_reason=exc.rcvd.reason if exc.rcvd else None,
            )


def build_sarvam_stt_url(config: SarvamSTTConfig) -> str:
    query = {
        "language-code": config.language_code,
        "model": config.model,
        "mode": config.mode,
        "sample_rate": str(config.sample_rate),
        "input_audio_codec": config.input_audio_codec,
        "high_vad_sensitivity": _bool_query(config.high_vad_sensitivity),
    }
    separator = "&" if "?" in config.url else "?"
    return f"{config.url}{separator}{urlencode(query)}"


# Sarvam protocol constant: audio.encoding is always the MIME type,
# NOT the PCM codec.  The codec is communicated separately via the
# ``input_audio_codec`` URL query parameter.
_SARVAM_AUDIO_MIME_TYPE = "audio/wav"


def encode_audio_message(frame: PcmFrame, _audio_encoding: str | None = None) -> str:
    """Return a JSON text message wrapping base64-encoded raw PCM audio.

    Sarvam's ``speech-to-text/ws`` endpoint expects JSON frames matching
    the official sarvamai SDK ``AudioMessage`` format::

        {"audio": {"data": "<base64>", "sample_rate": <int>, "encoding": "audio/wav"}}

    ``audio.encoding`` is a **MIME type** (always ``"audio/wav"``).
    The actual PCM codec (e.g. ``pcm_s16le``) is communicated via the
    ``input_audio_codec`` URL query parameter at connection time.

    Raw PCM bytes are sent directly — no RIFF/WAV header is added.
    """
    audio_b64 = base64.b64encode(frame.data).decode("ascii")
    return json.dumps({
        "audio": {
            "data": audio_b64,
            "sample_rate": frame.sample_rate,
            "encoding": _SARVAM_AUDIO_MIME_TYPE,
        }
    })


def extract_transcript(message: dict[str, Any]) -> Transcript | None:
    message_type = _as_optional_str(message.get("type"))
    data = message.get("data")
    if isinstance(data, dict):
        text = _as_optional_str(data.get("transcript") or data.get("text"))
        if text:
            return Transcript(
                text=text,
                request_id=_as_optional_str(data.get("request_id")),
                is_final=True,
                metrics=data.get("metrics") if isinstance(data.get("metrics"), dict) else {},
                raw_type=message_type,
            )

    text = _as_optional_str(message.get("text") or message.get("transcript"))
    if text:
        return Transcript(
            text=text,
            request_id=_as_optional_str(message.get("request_id")),
            is_final=bool(message.get("is_final", True)),
            metrics=message.get("metrics") if isinstance(message.get("metrics"), dict) else {},
            raw_type=message_type,
        )

    return None


def _decode_message(raw_message: str | bytes) -> dict[str, Any]:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8")
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError:
        return {"type": "unparseable", "raw_size": len(raw_message)}
    return message if isinstance(message, dict) else {"type": "unexpected", "value": message}


def _redact_key(url: str) -> str:
    """Return *url* with any api-key query values masked."""
    if "api" not in url.lower():
        return url
    from urllib.parse import parse_qs, urlparse, urlencode as _ue, urlunparse

    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    for key in list(qs):
        if "key" in key.lower() or "secret" in key.lower():
            qs[key] = ["***"]
    return urlunparse(parsed._replace(query=_ue(qs, doseq=True)))


def _bool_query(value: bool) -> str:
    return "true" if value else "false"


def _as_optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None

