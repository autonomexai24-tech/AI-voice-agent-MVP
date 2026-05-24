from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)

_BOOKING_FIELD_ORDER = (
    "customer_name",
    "phone_number",
    "service_type",
    "appointment_date",
    "appointment_time",
    "doctor_preference",
    "notes",
)
_QUESTION_KEYWORDS = {
    "customer_name": ("name", "naam"),
    "phone_number": ("phone", "number", "mobile"),
    "service_type": ("service", "treatment", "appointment for"),
    "appointment_date": ("date", "day", "when", "kab"),
    "appointment_time": ("time", "am", "pm", "baje"),
    "doctor_preference": ("doctor", "preference"),
    "notes": ("note", "anything else", "kuch aur"),
}


@dataclass
class CallerIdentityMemory:
    name: str | None = None
    phone_number: str | None = None
    last_updated_turn: int = 0


@dataclass
class BookingContinuityMemory:
    values: dict[str, str] = field(default_factory=dict)
    pending_fields: tuple[str, ...] = _BOOKING_FIELD_ORDER
    stage: str = "idle"
    awaiting_confirmation: bool = False
    confirmation_completed: bool = False
    active_correction: str | None = None
    corrected_fields: tuple[str, ...] = ()
    last_summary_fingerprint: str | None = None
    last_updated_turn: int = 0


@dataclass
class LanguageContinuityMemory:
    active_language: str = "english"
    dominant_language: str = "english"
    previous_language: str | None = None
    confidence: float = 1.0
    generation: int = 0
    switch_count: int = 0
    last_updated_turn: int = 0


@dataclass
class EscalationMemory:
    triggered: bool = False
    reason: str | None = None
    last_updated_turn: int = 0


@dataclass
class CorrectionMemory:
    active_fields: tuple[str, ...] = ()
    corrected_fields: dict[str, int] = field(default_factory=dict)
    last_correction_turn: int = 0


@dataclass
class UnresolvedQuestion:
    key: str
    field_name: str | None
    first_turn: int
    last_turn: int
    attempts: int = 1
    resolved: bool = False


@dataclass(frozen=True)
class RuntimeMemorySnapshot:
    session_id: str
    turn_index: int
    caller: CallerIdentityMemory
    booking: BookingContinuityMemory
    language: LanguageContinuityMemory
    escalation: EscalationMemory
    correction: CorrectionMemory
    unresolved_questions: tuple[UnresolvedQuestion, ...]
    compressed_turns: int
    injection_chars: int


class CallMemoryEngine:
    """Deterministic runtime-owned memory for live voice calls.

    This engine stores compact structured state only. It intentionally does not
    retain raw transcripts, embeddings, vector indexes, or full conversation
    history, so prompt injection remains low-latency and bounded on long calls.
    """

    def __init__(
        self,
        session_id: str,
        *,
        max_unresolved_questions: int = 8,
        compression_turn_threshold: int = 16,
        max_injection_chars: int = 760,
        clock=time.perf_counter,
    ) -> None:
        self.session_id = session_id
        self._max_unresolved_questions = max_unresolved_questions
        self._compression_turn_threshold = compression_turn_threshold
        self._max_injection_chars = max_injection_chars
        self._clock = clock
        self._turn_index = 0
        self._last_compressed_turn = 0
        self._compressed_turns = 0
        self.caller = CallerIdentityMemory()
        self.booking = BookingContinuityMemory()
        self.language = LanguageContinuityMemory()
        self.escalation = EscalationMemory()
        self.correction = CorrectionMemory()
        self._unresolved: dict[str, UnresolvedQuestion] = {}
        self._recent_question_keys: list[str] = []

    @property
    def turn_index(self) -> int:
        return self._turn_index

    def record_turn(
        self,
        *,
        role: str,
        text: str,
        request_id: str | None = None,
    ) -> None:
        cleaned = _clean(text)
        if not cleaned:
            return
        self._turn_index += 1
        if role == "assistant":
            self._record_assistant_question(cleaned, request_id=request_id)
        self._maybe_compress(request_id=request_id)

    def update_from_session(
        self,
        session: Any,
        *,
        language: Any | None = None,
        request_id: str | None = None,
    ) -> None:
        started_at = self._clock()
        try:
            self.caller.name = _value_or_none(getattr(session, "caller_name", None))
            self.caller.phone_number = _value_or_none(getattr(session, "phone_number", None))
            self.caller.last_updated_turn = self._turn_index

            values = {
                "customer_name": getattr(session, "caller_name", None),
                "phone_number": getattr(session, "phone_number", None),
                "service_type": getattr(session, "selected_service", None),
                "appointment_date": getattr(session, "preferred_date", None),
                "appointment_time": getattr(session, "preferred_time", None),
                "doctor_preference": getattr(session, "doctor_preference", None),
                "notes": getattr(session, "optional_notes", None),
            }
            self.booking.values = {
                key: _clean(str(value))
                for key, value in values.items()
                if value not in (None, "")
            }
            self.booking.pending_fields = tuple(
                str(field) for field in getattr(session, "pending_booking_fields", ())
            )
            booking_stage = getattr(session, "booking_stage", None)
            self.booking.stage = getattr(booking_stage, "value", str(booking_stage or "idle"))
            booking = getattr(session, "booking", None)
            if booking is not None:
                self.booking.awaiting_confirmation = bool(
                    getattr(booking, "awaiting_confirmation", False)
                )
                self.booking.confirmation_completed = bool(
                    getattr(booking, "confirmation_completed", False)
                )
                self.booking.active_correction = _value_or_none(
                    getattr(booking, "active_correction", None)
                )
                self.booking.last_summary_fingerprint = _value_or_none(
                    getattr(booking, "last_summary_fingerprint", None)
                )
            self.booking.last_updated_turn = self._turn_index

            self.escalation.triggered = bool(getattr(session, "escalation_triggered", False))
            self.escalation.reason = _value_or_none(getattr(session, "escalation_reason", None))
            self.escalation.last_updated_turn = self._turn_index

            self._resolve_known_booking_questions()
            if language is not None:
                self.update_language(language, request_id=request_id)
            self._maybe_compress(request_id=request_id)

            log_event(
                logger,
                "runtime_memory_synchronized",
                request_id=request_id,
                session_id=self.session_id,
                known_booking_fields=list(self.booking.values),
                pending_booking_fields=list(self.booking.pending_fields),
                unresolved_question_count=len(self.unresolved_questions()),
                latency_ms=round((self._clock() - started_at) * 1000, 3),
            )
        except Exception as exc:
            log_event(
                logger,
                "runtime_memory_sync_failed",
                request_id=request_id,
                session_id=self.session_id,
                error_type=type(exc).__name__,
            )

    def update_language(self, language: Any, *, request_id: str | None = None) -> None:
        active_language = _value_or_none(getattr(language, "active_language", None))
        if active_language is None and isinstance(language, str):
            active_language = language
        if active_language is None:
            return
        previous_active = self.language.active_language
        generation = int(getattr(language, "generation", self.language.generation) or 0)
        self.language.active_language = active_language
        self.language.dominant_language = _value_or_none(
            getattr(language, "dominant_language", None)
        ) or active_language
        self.language.previous_language = _value_or_none(
            getattr(language, "previous_language", None)
        ) or (previous_active if previous_active != active_language else self.language.previous_language)
        self.language.confidence = float(getattr(language, "confidence", self.language.confidence) or 0)
        if active_language != previous_active:
            self.language.switch_count += 1
        self.language.generation = generation
        self.language.last_updated_turn = self._turn_index
        log_event(
            logger,
            "runtime_memory_language_updated",
            request_id=request_id,
            session_id=self.session_id,
            active_language=self.language.active_language,
            previous_language=self.language.previous_language,
            language_generation=self.language.generation,
            switch_count=self.language.switch_count,
        )

    def mark_unresolved_question(
        self,
        *,
        field_name: str | None,
        question_key: str | None = None,
        request_id: str | None = None,
    ) -> None:
        key = question_key or field_name or "general"
        key = _stable_key(key)
        existing = self._unresolved.get(key)
        if existing is None or existing.resolved:
            self._unresolved[key] = UnresolvedQuestion(
                key=key,
                field_name=field_name,
                first_turn=self._turn_index,
                last_turn=self._turn_index,
            )
        else:
            existing.attempts += 1
            existing.last_turn = self._turn_index
        self._recent_question_keys.append(key)
        del self._recent_question_keys[: -self._max_unresolved_questions]
        self._age_unresolved()
        log_event(
            logger,
            "runtime_memory_unresolved_question_tracked",
            request_id=request_id,
            session_id=self.session_id,
            question_key=key,
            field_name=field_name,
            attempts=self._unresolved[key].attempts,
            unresolved_question_count=len(self.unresolved_questions()),
        )

    def resolve_question(
        self,
        *,
        field_name: str,
        request_id: str | None = None,
    ) -> None:
        changed = False
        for question in self._unresolved.values():
            if question.field_name == field_name and not question.resolved:
                question.resolved = True
                question.last_turn = self._turn_index
                changed = True
        if changed:
            log_event(
                logger,
                "runtime_memory_unresolved_question_resolved",
                request_id=request_id,
                session_id=self.session_id,
                field_name=field_name,
            )

    def note_corrections(
        self,
        fields: tuple[str, ...],
        *,
        request_id: str | None = None,
    ) -> None:
        if not fields:
            self.correction.active_fields = ()
            return
        normalized = tuple(_stable_key(field) for field in fields)
        self.correction.active_fields = normalized
        self.booking.corrected_fields = normalized
        self.correction.last_correction_turn = self._turn_index
        for field_name in normalized:
            self.correction.corrected_fields[field_name] = (
                self.correction.corrected_fields.get(field_name, 0) + 1
            )
            self.resolve_question(field_name=field_name, request_id=request_id)
        log_event(
            logger,
            "runtime_memory_correction_recorded",
            request_id=request_id,
            session_id=self.session_id,
            corrected_fields=list(normalized),
        )

    def note_escalation(
        self,
        *,
        reason: str,
        request_id: str | None = None,
    ) -> None:
        self.escalation.triggered = True
        self.escalation.reason = _clean(reason) or "unspecified"
        self.escalation.last_updated_turn = self._turn_index
        log_event(
            logger,
            "runtime_memory_escalation_recorded",
            request_id=request_id,
            session_id=self.session_id,
            escalation_reason=self.escalation.reason,
        )

    def unresolved_questions(self) -> tuple[UnresolvedQuestion, ...]:
        questions = [question for question in self._unresolved.values() if not question.resolved]
        questions.sort(key=lambda question: (question.last_turn, question.key), reverse=True)
        return tuple(questions[: self._max_unresolved_questions])

    def snapshot(self) -> RuntimeMemorySnapshot:
        injection = self.build_injection()
        return RuntimeMemorySnapshot(
            session_id=self.session_id,
            turn_index=self._turn_index,
            caller=self.caller,
            booking=self.booking,
            language=self.language,
            escalation=self.escalation,
            correction=self.correction,
            unresolved_questions=self.unresolved_questions(),
            compressed_turns=self._compressed_turns,
            injection_chars=len(injection),
        )

    def build_injection(
        self,
        *,
        intent_classification: str | None = None,
        max_chars: int | None = None,
    ) -> str:
        max_chars = max_chars or self._max_injection_chars
        known = _join_pairs(self.booking.values)
        pending = ", ".join(self.booking.pending_fields) or "none"
        unresolved = _unresolved_block(self.unresolved_questions())
        corrections = ", ".join(self.correction.active_fields) or "none"
        escalation = (
            f"triggered:{self.escalation.reason}"
            if self.escalation.triggered
            else "not_triggered"
        )
        lines = [
            "Runtime memory:",
            f"- caller_name: {self.caller.name or 'unknown'}; phone_number: {self.caller.phone_number or 'unknown'}",
            f"- booking_stage: {self.booking.stage}; booking_captured: {known or 'none'}; pending: {pending}",
            f"- confirmation: awaiting={self.booking.awaiting_confirmation}; completed={self.booking.confirmation_completed}",
            f"- language: active={self.language.active_language}; previous={self.language.previous_language or 'none'}; dominant={self.language.dominant_language}; generation={self.language.generation}",
            f"- corrections: active={corrections}; total={sum(self.correction.corrected_fields.values())}",
            f"- unresolved: {unresolved}",
            f"- escalation: {escalation}",
            "- ask_policy: do not ask for known fields; ask only the first pending booking field; acknowledge corrections before continuing.",
        ]
        if intent_classification:
            lines.insert(2, f"- intent: {intent_classification}")
        injection = "\n".join(lines)
        if len(injection) <= max_chars:
            return injection
        compact_lines = [
            "Runtime memory:",
            f"- caller={self.caller.name or 'unknown'}/{self.caller.phone_number or 'unknown'}",
            f"- booking={self.booking.stage}; known={_compact_known(self.booking.values)}; pending={pending}",
            f"- confirm={int(self.booking.awaiting_confirmation)}/{int(self.booking.confirmation_completed)}; lang={self.language.active_language}; corr={corrections}",
            f"- unresolved={unresolved}; escalation={escalation}",
            "- rule: ask one unknown field; never re-ask known fields.",
        ]
        compact = "\n".join(compact_lines)
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 3].rstrip() + "..."

    def _record_assistant_question(self, text: str, *, request_id: str | None) -> None:
        if "?" not in text and not re.search(r"\b(?:tell|share|bata|confirm)\b", text, re.I):
            return
        lowered = text.lower()
        for field_name, markers in _QUESTION_KEYWORDS.items():
            if any(marker in lowered for marker in markers):
                self.mark_unresolved_question(
                    field_name=field_name,
                    request_id=request_id,
                )
                return
        digest = hashlib.sha1(lowered.encode("utf-8")).hexdigest()[:10]
        self.mark_unresolved_question(
            field_name=None,
            question_key=f"general:{digest}",
            request_id=request_id,
        )

    def _resolve_known_booking_questions(self) -> None:
        for field_name in self.booking.values:
            self.resolve_question(field_name=field_name)

    def _maybe_compress(self, *, request_id: str | None) -> None:
        if self._turn_index - self._last_compressed_turn < self._compression_turn_threshold:
            self._age_unresolved()
            return
        self._last_compressed_turn = self._turn_index
        before = len(self._unresolved)
        self._age_unresolved(force=True)
        self._compressed_turns = self._turn_index
        log_event(
            logger,
            "runtime_memory_compressed",
            request_id=request_id,
            session_id=self.session_id,
            turn_index=self._turn_index,
            unresolved_before=before,
            unresolved_after=len(self.unresolved_questions()),
            known_booking_fields=list(self.booking.values),
        )

    def _age_unresolved(self, *, force: bool = False) -> None:
        max_age = self._compression_turn_threshold * (1 if force else 2)
        stale_keys = [
            key
            for key, question in self._unresolved.items()
            if question.resolved or self._turn_index - question.last_turn > max_age
        ]
        for key in stale_keys:
            self._unresolved.pop(key, None)
        if len(self._unresolved) <= self._max_unresolved_questions:
            return
        active = sorted(
            self._unresolved.values(),
            key=lambda question: (question.last_turn, question.attempts),
            reverse=True,
        )
        keep = {question.key for question in active[: self._max_unresolved_questions]}
        for key in list(self._unresolved):
            if key not in keep:
                self._unresolved.pop(key, None)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _stable_key(value: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", value.strip().lower()).strip("_") or "unknown"


def _value_or_none(value: object | None) -> str | None:
    if value is None:
        return None
    text = _clean(str(value))
    return text or None


def _join_pairs(values: dict[str, str]) -> str:
    return "; ".join(
        f"{field_name}={values[field_name]}"
        for field_name in _BOOKING_FIELD_ORDER
        if field_name in values
    )


def _compact_known(values: dict[str, str]) -> str:
    labels = {
        "customer_name": "name",
        "phone_number": "phone",
        "service_type": "service",
        "appointment_date": "date",
        "appointment_time": "time",
        "doctor_preference": "doctor",
        "notes": "notes",
    }
    return ",".join(
        f"{labels.get(field_name, field_name)}={values[field_name][:40]}"
        for field_name in _BOOKING_FIELD_ORDER
        if field_name in values
    ) or "none"


def _unresolved_block(questions: tuple[UnresolvedQuestion, ...]) -> str:
    if not questions:
        return "none"
    entries = []
    for question in questions[:4]:
        label = question.field_name or question.key
        entries.append(f"{label}(attempts={question.attempts})")
    return ", ".join(entries)
