from __future__ import annotations

import io
import wave
from array import array

from voice_agent.audio import PcmAudio, decode_audio_bytes, iter_frames, prepare_for_livekit


def test_decode_wav_and_prepare_for_livekit_downmixes_and_resamples() -> None:
    left = array("h", [0, 1000, 2000, 3000])
    right = array("h", [0, -1000, -2000, -3000])
    interleaved = array("h")
    for left_sample, right_sample in zip(left, right):
        interleaved.extend([left_sample, right_sample])

    wav_bytes = io.BytesIO()
    with wave.open(wav_bytes, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(interleaved.tobytes())

    decoded = decode_audio_bytes(wav_bytes.getvalue(), fallback_sample_rate=16000)
    prepared = prepare_for_livekit(decoded, output_sample_rate=16000)

    assert decoded.channels == 2
    assert decoded.sample_rate == 8000
    assert prepared.channels == 1
    assert prepared.sample_rate == 16000
    assert len(prepared.data) == 16


def test_iter_frames_pads_final_frame() -> None:
    audio = PcmAudio(data=b"\x01\x00" * 1200, sample_rate=48000, channels=1)
    frames = list(iter_frames(audio, frame_ms=20))

    assert len(frames) == 2
    assert frames[0].samples_per_channel == 960
    assert len(frames[0].data) == 1920
    assert len(frames[1].data) == 1920
    assert frames[1].data.endswith(b"\x00" * 1440)
