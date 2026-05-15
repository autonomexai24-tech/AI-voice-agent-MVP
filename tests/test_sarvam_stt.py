from __future__ import annotations

from voice_agent.audio import PcmFrame
from voice_agent.config import SarvamSTTConfig
from voice_agent.providers.sarvam_stt import (
    build_sarvam_stt_url,
    encode_audio_message,
    extract_transcript,
)


def _config() -> SarvamSTTConfig:
    return SarvamSTTConfig(
        api_key="secret",
        url="wss://api.sarvam.ai/speech-to-text/ws",
        model="saaras:v3",
        mode="transcribe",
        language_code="en-IN",
        sample_rate=16000,
        input_audio_codec="pcm_s16le",
        audio_encoding="audio/wav",
        high_vad_sensitivity=True,
        queue_max_chunks=250,
    )


def test_build_sarvam_stt_url_uses_streaming_query_params() -> None:
    url = build_sarvam_stt_url(_config())

    assert url.startswith("wss://api.sarvam.ai/speech-to-text/ws?")
    assert "language-code=en-IN" in url
    assert "model=saaras%3Av3" in url
    assert "mode=transcribe" in url
    assert "sample_rate=16000" in url
    assert "input_audio_codec=pcm_s16le" in url
    assert "high_vad_sensitivity=true" in url


def test_encode_audio_message_returns_json_b64() -> None:
    frame = PcmFrame(
        data=b"\x01\x00\x02\x00",
        sample_rate=16000,
        channels=1,
        samples_per_channel=2,
    )
    result = encode_audio_message(frame)

    import json
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert "audio" in parsed
    assert parsed["audio"]["sample_rate"] == 16000
    assert parsed["audio"]["encoding"] == "audio/wav"
    import base64
    assert base64.b64decode(parsed["audio"]["data"]) == b"\x01\x00\x02\x00"


def test_extract_transcript_accepts_reference_data_shape() -> None:
    transcript = extract_transcript(
        {
            "type": "data",
            "data": {
                "request_id": "req-1",
                "transcript": "hello",
                "metrics": {"processing_latency": 0.1},
            },
        }
    )

    assert transcript is not None
    assert transcript.text == "hello"
    assert transcript.request_id == "req-1"
    assert transcript.metrics == {"processing_latency": 0.1}
