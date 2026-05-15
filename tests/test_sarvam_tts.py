from __future__ import annotations

import asyncio
import base64

import httpx

from voice_agent.config import SarvamConfig
from voice_agent.providers.sarvam_tts import SarvamTTSClient, extract_audio_bytes


def _config() -> SarvamConfig:
    return SarvamConfig(
        api_key="sarvam-key",
        tts_url="https://api.sarvam.ai/text-to-speech",
        model="bulbul:v1",
        speaker="meera",
        language_code="en-IN",
        sample_rate=16000,
        tts_timeout_seconds=20.0,
    )


def test_extract_audio_bytes_accepts_json_audios_shape() -> None:
    audio = b"\x01\x00\x02\x00"
    response = httpx.Response(
        200,
        json={"audios": [base64.b64encode(audio).decode("ascii")]},
        headers={"content-type": "application/json"},
    )

    assert extract_audio_bytes(response) == audio


def test_synthesize_pcm_sends_cleaned_phone_response_text() -> None:
    asyncio.run(_run_synthesize_pcm_test())


async def _run_synthesize_pcm_test() -> None:
    fake_http = _FakeHttpClient()
    client = SarvamTTSClient(_config())
    client._client = fake_http

    audio = await client.synthesize_pcm("  Sure,   I can help.  ")

    assert fake_http.payload is not None
    assert fake_http.payload["inputs"] == ["Sure, I can help."]
    assert fake_http.payload["target_language_code"] == "en-IN"
    assert fake_http.payload["speaker"] == "meera"
    assert audio.data == b"\x00\x00" * 160
    assert audio.sample_rate == 16000
    assert audio.channels == 1


def test_synthesize_pcm_accepts_dynamic_language_route() -> None:
    asyncio.run(_run_dynamic_language_route_test())


async def _run_dynamic_language_route_test() -> None:
    fake_http = _FakeHttpClient()
    client = SarvamTTSClient(_config())
    client._client = fake_http

    await client.synthesize_pcm(
        "\u0920\u0940\u0915 \u0939\u0948, \u092e\u0948\u0902 \u092e\u0926\u0926 \u0915\u0930\u0924\u093e \u0939\u0942\u0902.",
        language_code="hi-IN",
        speaker="meera",
        route_language="hindi",
    )

    assert fake_http.payload is not None
    assert fake_http.payload["target_language_code"] == "hi-IN"
    assert fake_http.payload["speaker"] == "meera"


class _FakeHttpClient:
    def __init__(self) -> None:
        self.payload = None

    async def post(self, _url: str, *, headers: dict[str, str], json: dict[str, object]) -> httpx.Response:
        self.payload = json
        assert headers["api-subscription-key"] == "sarvam-key"
        return httpx.Response(
            200,
            content=b"\x00\x00" * 160,
            headers={"content-type": "audio/pcm"},
            request=httpx.Request("POST", "https://api.sarvam.ai/text-to-speech"),
        )
