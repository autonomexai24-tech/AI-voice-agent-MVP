from __future__ import annotations

import base64
from typing import Any

import httpx

from voice_agent.audio import PcmAudio, decode_audio_bytes
from voice_agent.config import SarvamConfig
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


class SarvamTTSClient:
    def __init__(self, config: SarvamConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(timeout=config.tts_timeout_seconds)

    async def __aenter__(self) -> "SarvamTTSClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def synthesize_pcm(
        self,
        text: str,
        *,
        language_code: str | None = None,
        speaker: str | None = None,
        route_language: str | None = None,
    ) -> PcmAudio:
        cleaned_text = _clean_text(text)
        if not cleaned_text:
            raise ValueError("text must not be empty")

        target_language_code = language_code or self._config.language_code
        target_speaker = speaker or self._config.speaker
        payload = {
            "inputs": [cleaned_text],
            "target_language_code": target_language_code,
            "speaker": target_speaker,
            "model": self._config.model,
            "speech_sample_rate": self._config.sample_rate,
            "enable_preprocessing": True,
        }
        headers = {
            "api-subscription-key": self._config.api_key,
            "content-type": "application/json",
        }

        log_event(
            logger,
            "sarvam_tts_request_started",
            text_chars=len(cleaned_text),
            sample_rate=self._config.sample_rate,
            target_language_code=target_language_code,
            speaker=target_speaker,
            route_language=route_language,
            model=self._config.model,
        )
        response = await self._client.post(self._config.tts_url, headers=headers, json=payload)

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:500]
            raise RuntimeError(f"Sarvam TTS failed with HTTP {response.status_code}: {body}") from exc

        audio_bytes = extract_audio_bytes(response)
        audio = decode_audio_bytes(
            audio_bytes,
            fallback_sample_rate=self._config.sample_rate,
            fallback_channels=1,
        )
        log_event(
            logger,
            "sarvam_tts_request_completed",
            response_bytes=len(audio_bytes),
            decoded_sample_rate=audio.sample_rate,
            decoded_channels=audio.channels,
            decoded_duration_ms=round(audio.duration_seconds * 1000),
            target_language_code=target_language_code,
            speaker=target_speaker,
            route_language=route_language,
        )
        return audio


def extract_audio_bytes(response: httpx.Response) -> bytes:
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return response.content

    payload = response.json()
    encoded_audio = _first_audio_value(payload)
    try:
        return base64.b64decode(encoded_audio)
    except ValueError as exc:
        raise RuntimeError("Sarvam TTS response contained invalid base64 audio") from exc


def _first_audio_value(payload: dict[str, Any]) -> str:
    audios = payload.get("audios")
    if isinstance(audios, list) and audios and isinstance(audios[0], str):
        return audios[0]

    audio = payload.get("audio")
    if isinstance(audio, str):
        return audio

    raise RuntimeError("Sarvam TTS response did not include audio data")


def _clean_text(text: str) -> str:
    return " ".join(text.strip().split())
