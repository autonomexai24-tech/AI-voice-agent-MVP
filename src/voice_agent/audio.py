from __future__ import annotations

import io
import math
import sys
import wave
from array import array
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PcmAudio:
    data: bytes
    sample_rate: int
    channels: int

    @property
    def duration_seconds(self) -> float:
        if self.sample_rate <= 0 or self.channels <= 0:
            return 0.0
        return len(self.data) / (self.sample_rate * self.channels * 2)


@dataclass(frozen=True)
class PcmFrame:
    data: bytes
    sample_rate: int
    channels: int
    samples_per_channel: int


def decode_audio_bytes(
    payload: bytes,
    *,
    fallback_sample_rate: int,
    fallback_channels: int = 1,
) -> PcmAudio:
    if not payload:
        raise ValueError("audio payload is empty")
    if payload[:4] == b"RIFF":
        return _decode_wav(payload)
    if len(payload) % 2 != 0:
        raise ValueError("raw PCM16 payload must contain an even number of bytes")
    return PcmAudio(
        data=payload,
        sample_rate=fallback_sample_rate,
        channels=fallback_channels,
    )


def prepare_for_livekit(
    audio: PcmAudio,
    *,
    output_sample_rate: int,
    output_channels: int = 1,
) -> PcmAudio:
    if audio.channels < 1:
        raise ValueError("audio must have at least one channel")
    if output_channels != 1:
        raise ValueError("Phase 1A publishes mono audio only")

    mono = audio.data if audio.channels == 1 else _downmix_to_mono(audio.data, audio.channels)
    resampled = (
        mono
        if audio.sample_rate == output_sample_rate
        else _resample_pcm16_mono(mono, audio.sample_rate, output_sample_rate)
    )
    return PcmAudio(data=resampled, sample_rate=output_sample_rate, channels=output_channels)


def iter_frames(audio: PcmAudio, *, frame_ms: int) -> Iterable[PcmFrame]:
    if frame_ms <= 0:
        raise ValueError("frame_ms must be positive")
    samples_per_channel = audio.sample_rate * frame_ms // 1000
    if samples_per_channel <= 0 or (audio.sample_rate * frame_ms) % 1000 != 0:
        raise ValueError("frame_ms must divide evenly into sample frames")

    bytes_per_frame = samples_per_channel * audio.channels * 2
    for offset in range(0, len(audio.data), bytes_per_frame):
        chunk = audio.data[offset : offset + bytes_per_frame]
        if len(chunk) < bytes_per_frame:
            chunk += b"\x00" * (bytes_per_frame - len(chunk))
        yield PcmFrame(
            data=chunk,
            sample_rate=audio.sample_rate,
            channels=audio.channels,
            samples_per_channel=samples_per_channel,
        )


def _decode_wav(payload: bytes) -> PcmAudio:
    with wave.open(io.BytesIO(payload), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    return PcmAudio(
        data=_convert_to_pcm16(frames, sample_width),
        sample_rate=sample_rate,
        channels=channels,
    )


def _convert_to_pcm16(data: bytes, sample_width: int) -> bytes:
    if sample_width == 2:
        return data
    if sample_width == 1:
        converted = array("h", ((sample - 128) << 8 for sample in data))
        return _array_to_le_bytes(converted)
    if sample_width == 3:
        samples = array("h")
        for index in range(0, len(data), 3):
            sample = int.from_bytes(data[index : index + 3], "little", signed=True)
            samples.append(_clip_int16(sample >> 8))
        return _array_to_le_bytes(samples)
    if sample_width == 4:
        samples = array("h")
        for index in range(0, len(data), 4):
            sample = int.from_bytes(data[index : index + 4], "little", signed=True)
            samples.append(_clip_int16(sample >> 16))
        return _array_to_le_bytes(samples)
    raise ValueError(f"unsupported WAV sample width: {sample_width}")


def _downmix_to_mono(data: bytes, channels: int) -> bytes:
    samples = _le_bytes_to_array(data)
    if len(samples) % channels != 0:
        raise ValueError("interleaved PCM data does not align with channel count")

    mono = array("h")
    for index in range(0, len(samples), channels):
        total = sum(samples[index : index + channels])
        mono.append(_clip_int16(round(total / channels)))
    return _array_to_le_bytes(mono)


def _resample_pcm16_mono(data: bytes, input_rate: int, output_rate: int) -> bytes:
    if input_rate <= 0 or output_rate <= 0:
        raise ValueError("sample rates must be positive")

    samples = _le_bytes_to_array(data)
    if not samples or input_rate == output_rate:
        return data

    output_count = max(1, math.ceil(len(samples) * output_rate / input_rate))
    ratio = input_rate / output_rate
    resampled = array("h")

    for output_index in range(output_count):
        source_pos = output_index * ratio
        lower = int(source_pos)
        upper = min(lower + 1, len(samples) - 1)
        fraction = source_pos - lower
        value = samples[lower] * (1.0 - fraction) + samples[upper] * fraction
        resampled.append(_clip_int16(round(value)))

    return _array_to_le_bytes(resampled)


def _le_bytes_to_array(data: bytes) -> array:
    samples = array("h")
    samples.frombytes(data)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _array_to_le_bytes(samples: array) -> bytes:
    output = array("h", samples)
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


def _clip_int16(value: int) -> int:
    return max(-32768, min(32767, value))
