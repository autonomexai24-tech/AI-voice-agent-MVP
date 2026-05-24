from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Callable

from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)

SUPPORTED_LANGUAGE_KEYS = {
    "english",
    "hindi",
    "kannada",
    "tamil",
    "telugu",
    "marathi",
    "malayalam",
    "bengali",
    "hinglish",
    "mixed",
}

LANGUAGE_LABELS = {
    "english": "English",
    "hindi": "Hindi",
    "kannada": "Kannada",
    "tamil": "Tamil",
    "telugu": "Telugu",
    "marathi": "Marathi",
    "malayalam": "Malayalam",
    "bengali": "Bengali",
    "hinglish": "Hinglish",
    "mixed": "mixed language",
}

SARVAM_LANGUAGE_CODES = {
    "english": "en-IN",
    "hindi": "hi-IN",
    "kannada": "kn-IN",
    "tamil": "ta-IN",
    "telugu": "te-IN",
    "marathi": "mr-IN",
    "malayalam": "ml-IN",
    "bengali": "bn-IN",
    "hinglish": "hi-IN",
    "mixed": "en-IN",
}

_SCRIPT_RANGES = {
    "hindi": ((0x0900, 0x097F),),
    "bengali": ((0x0980, 0x09FF),),
    "tamil": ((0x0B80, 0x0BFF),),
    "telugu": ((0x0C00, 0x0C7F),),
    "kannada": ((0x0C80, 0x0CFF),),
    "malayalam": ((0x0D00, 0x0D7F),),
}

_ROMAN_LANGUAGE_MARKERS = {
    "hindi": {
        "achha",
        "acha",
        "aap",
        "abhi",
        "batao",
        "chahiye",
        "haan",
        "hai",
        "hain",
        "hindi",
        "hoon",
        "kaise",
        "karna",
        "karo",
        "kya",
        "mera",
        "meri",
        "mujhe",
        "nahin",
        "nahi",
        "namaste",
        "theek",
        "thik",
    },
    "kannada": {
        "beku",
        "beda",
        "elli",
        "enu",
        "hegide",
        "hogbeku",
        "illa",
        "kannada",
        "nanage",
        "nanu",
        "nimma",
        "yavaga",
    },
    "tamil": {
        "enna",
        "eppo",
        "illa",
        "irukku",
        "naan",
        "nalam",
        "seri",
        "sollunga",
        "tamil",
        "unga",
        "venum",
    },
    "telugu": {
        "avunu",
        "cheppandi",
        "ekkada",
        "ela",
        "kaavali",
        "ledu",
        "meeru",
        "naku",
        "telugu",
        "undi",
    },
    "marathi": {
        "aahe",
        "ahe",
        "bolaycha",
        "hava",
        "kaay",
        "mala",
        "marathi",
        "nahi",
        "pahije",
        "sanga",
        "tumhi",
    },
    "malayalam": {
        "aanu",
        "alla",
        "ente",
        "evide",
        "malayalam",
        "njan",
        "parayu",
        "sugham",
        "undo",
        "venam",
    },
    "bengali": {
        "ami",
        "apni",
        "bhalo",
        "bangla",
        "bengali",
        "chai",
        "kemon",
        "namaskar",
        "tumi",
    },
}

_ENGLISH_MARKERS = {
    "a",
    "about",
    "appointment",
    "are",
    "book",
    "call",
    "can",
    "check",
    "do",
    "for",
    "hello",
    "help",
    "hi",
    "hours",
    "i",
    "is",
    "need",
    "please",
    "reception",
    "speak",
    "the",
    "to",
    "what",
    "when",
    "you",
}

_TOKEN_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class DetectedLanguage:
    language: str
    dominant_language: str
    confidence: float
    reason: str
    sarvam_language_code: str

    @property
    def label(self) -> str:
        return LANGUAGE_LABELS[self.language]


@dataclass(frozen=True)
class SessionLanguageSnapshot:
    active_language: str
    dominant_language: str
    previous_language: str | None
    confidence: float
    generation: int
    transition_started_at: float
    last_detected_at: float
    sarvam_language_code: str
    speaker: str
    openai_response_language: str

    @property
    def active_label(self) -> str:
        return LANGUAGE_LABELS[self.active_language]

    @property
    def dominant_label(self) -> str:
        return LANGUAGE_LABELS[self.dominant_language]


@dataclass(frozen=True)
class LanguageRoutingResult:
    detection: DetectedLanguage
    snapshot: SessionLanguageSnapshot
    switched: bool
    switching_latency_ms: float


def default_language_snapshot() -> SessionLanguageSnapshot:
    return SessionLanguageSnapshot(
        active_language="english",
        dominant_language="english",
        previous_language=None,
        confidence=1.0,
        generation=0,
        transition_started_at=0.0,
        last_detected_at=0.0,
        sarvam_language_code=SARVAM_LANGUAGE_CODES["english"],
        speaker="kavya",
        openai_response_language="English",
    )


def language_from_sarvam_code(language_code: str) -> str:
    normalized = language_code.strip().lower()
    for language, code in SARVAM_LANGUAGE_CODES.items():
        if normalized == code.lower() and language != "mixed":
            return language
    return "english"


def detect_language(text: str) -> DetectedLanguage:
    cleaned = text.strip()
    if not cleaned:
        return _detected("english", "english", 0.0, "empty")

    script_counts = _script_counts(cleaned)
    script_total = sum(script_counts.values())
    latin_tokens = _latin_tokens(cleaned)
    latin_count = sum(1 for character in cleaned if character.isascii() and character.isalpha())

    if script_total:
        dominant_language, dominant_count = max(script_counts.items(), key=lambda item: item[1])
        active_scripts = [language for language, count in script_counts.items() if count > 0]
        script_share = dominant_count / max(script_total, 1)
        confidence = min(0.98, 0.78 + (0.2 * script_share))
        if len(active_scripts) > 1 or latin_count >= max(3, dominant_count // 3):
            return _detected(
                "mixed",
                dominant_language,
                confidence,
                "indic_script_with_mixed_content",
            )
        return _detected(dominant_language, dominant_language, confidence, "indic_script")

    marker_counts = _roman_marker_counts(latin_tokens)
    total_markers = sum(marker_counts.values())
    english_markers = sum(1 for token in latin_tokens if token in _ENGLISH_MARKERS)
    if total_markers:
        dominant_language, dominant_count = max(marker_counts.items(), key=lambda item: item[1])
        active_marker_languages = [
            language for language, count in marker_counts.items() if count > 0
        ]
        marker_ratio = dominant_count / max(len(latin_tokens), 1)
        confidence = min(0.92, 0.7 + marker_ratio)
        if len(active_marker_languages) > 1:
            return _detected(
                "mixed",
                dominant_language,
                max(confidence, 0.78),
                "roman_markers_multiple_languages",
            )
        if dominant_language == "hindi":
            language = "hinglish" if english_markers or latin_tokens else "hindi"
            return _detected(language, "hindi", max(confidence, 0.76), "roman_hindi_markers")
        if english_markers and dominant_count <= english_markers:
            return _detected(
                "mixed",
                dominant_language,
                max(confidence, 0.76),
                "roman_indic_with_english",
            )
        return _detected(dominant_language, dominant_language, confidence, "roman_indic_markers")

    if latin_tokens:
        confidence = 0.74 if english_markers else 0.62
        return _detected("english", "english", confidence, "latin_default")

    return _detected("english", "english", 0.4, "unknown_default")


def build_openai_language_instruction(language: SessionLanguageSnapshot) -> str:
    active = language.active_language
    if active == "english":
        return "Respond in natural Indian English."
    if active == "hinglish":
        return (
            "Respond in natural Hinglish, using simple Latin-script Hindi mixed with English "
            "where it sounds normal on an Indian phone call."
        )
    if active == "mixed":
        return (
            f"Respond in the caller's mixed-language style. Keep {language.dominant_label} "
            "as the main language and use English only where the caller naturally mixed it."
        )
    return f"Respond in natural {language.active_label}."


class SessionLanguageRouter:
    def __init__(
        self,
        *,
        initial_language: str = "english",
        default_speaker: str = "meera",
        min_switch_confidence: float = 0.68,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        if initial_language not in SUPPORTED_LANGUAGE_KEYS or initial_language == "mixed":
            initial_language = "english"
        if min_switch_confidence < 0 or min_switch_confidence > 1:
            raise ValueError("min_switch_confidence must be between 0 and 1")

        now = clock()
        self._active_language = initial_language
        self._dominant_language = "hindi" if initial_language == "hinglish" else initial_language
        self._previous_language: str | None = None
        self._confidence = 1.0
        self._generation = 0
        self._transition_started_at = now
        self._last_detected_at = now
        self._default_speaker = default_speaker
        self._min_switch_confidence = min_switch_confidence
        self._clock = clock
        self._lock = asyncio.Lock()

    @property
    def current_generation(self) -> int:
        return self._generation

    async def route_text(
        self,
        text: str,
        *,
        request_id: str | None,
        is_final: bool,
    ) -> LanguageRoutingResult:
        started_at = self._clock()
        detection = detect_language(text)

        async with self._lock:
            now = self._clock()
            previous_active = self._active_language
            previous_dominant = self._dominant_language
            accepted = detection.confidence >= self._min_switch_confidence
            next_active = detection.language if accepted else self._active_language
            next_dominant = (
                detection.dominant_language if accepted else self._dominant_language
            )
            switched = (
                accepted
                and (next_active, next_dominant) != (previous_active, previous_dominant)
            )

            if switched:
                self._previous_language = previous_active
                self._active_language = next_active
                self._dominant_language = next_dominant
                self._confidence = detection.confidence
                self._generation += 1
                self._transition_started_at = now
            elif accepted:
                self._confidence = detection.confidence
                self._active_language = next_active
                self._dominant_language = next_dominant

            self._last_detected_at = now
            snapshot = self._snapshot()

        latency_ms = round((self._clock() - started_at) * 1000, 3)
        log_event(
            logger,
            "language_detected",
            request_id=request_id,
            is_final=is_final,
            detected_language=detection.language,
            dominant_language=detection.dominant_language,
            active_language=snapshot.active_language,
            previous_language=snapshot.previous_language,
            confidence=detection.confidence,
            state_confidence=snapshot.confidence,
            accepted=accepted,
            language_generation=snapshot.generation,
            reason=detection.reason,
        )
        if switched:
            log_event(
                logger,
                "language_transition",
                request_id=request_id,
                is_final=is_final,
                previous_language=previous_active,
                active_language=snapshot.active_language,
                dominant_language=snapshot.dominant_language,
                confidence=snapshot.confidence,
                language_generation=snapshot.generation,
                sarvam_language_code=snapshot.sarvam_language_code,
                speaker=snapshot.speaker,
            )
        log_event(
            logger,
            "language_switching_latency_timing",
            request_id=request_id,
            is_final=is_final,
            detected_language=detection.language,
            active_language=snapshot.active_language,
            switched=switched,
            latency_ms=latency_ms,
        )
        return LanguageRoutingResult(
            detection=detection,
            snapshot=snapshot,
            switched=switched,
            switching_latency_ms=latency_ms,
        )

    def snapshot(self) -> SessionLanguageSnapshot:
        return self._snapshot()

    def is_current(self, generation: int) -> bool:
        return generation == self._generation

    def should_stop_playback(self, generation: int) -> bool:
        return not self.is_current(generation)

    def _snapshot(self) -> SessionLanguageSnapshot:
        tts_language = _tts_language_for(self._active_language, self._dominant_language)
        return SessionLanguageSnapshot(
            active_language=self._active_language,
            dominant_language=self._dominant_language,
            previous_language=self._previous_language,
            confidence=self._confidence,
            generation=self._generation,
            transition_started_at=self._transition_started_at,
            last_detected_at=self._last_detected_at,
            sarvam_language_code=SARVAM_LANGUAGE_CODES[tts_language],
            speaker=self._default_speaker,
            openai_response_language=LANGUAGE_LABELS[self._active_language],
        )


def _detected(
    language: str,
    dominant_language: str,
    confidence: float,
    reason: str,
) -> DetectedLanguage:
    tts_language = _tts_language_for(language, dominant_language)
    return DetectedLanguage(
        language=language,
        dominant_language=dominant_language,
        confidence=round(confidence, 3),
        reason=reason,
        sarvam_language_code=SARVAM_LANGUAGE_CODES[tts_language],
    )


def _tts_language_for(language: str, dominant_language: str) -> str:
    if language in {"mixed"}:
        return dominant_language if dominant_language in SARVAM_LANGUAGE_CODES else "english"
    return language if language in SARVAM_LANGUAGE_CODES else "english"


def _script_counts(text: str) -> dict[str, int]:
    counts = {language: 0 for language in _SCRIPT_RANGES}
    for character in text:
        codepoint = ord(character)
        for language, ranges in _SCRIPT_RANGES.items():
            if any(start <= codepoint <= end for start, end in ranges):
                counts[language] += 1
                break
    return counts


def _latin_tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _roman_marker_counts(tokens: list[str]) -> dict[str, int]:
    counts = {language: 0 for language in _ROMAN_LANGUAGE_MARKERS}
    for token in tokens:
        for language, markers in _ROMAN_LANGUAGE_MARKERS.items():
            if token in markers:
                counts[language] += 1
    return counts
