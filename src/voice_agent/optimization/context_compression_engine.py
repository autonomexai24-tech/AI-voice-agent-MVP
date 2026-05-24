from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

from voice_agent.config import BusinessConfig
from voice_agent.logging_config import get_logger, log_event
from voice_agent.retrieval.faq_retrieval_engine import FAQRetrievalResult

logger = get_logger(__name__)

T = TypeVar("T")

_GREETING_RE = re.compile(r"^(hi|hello|hey|namaste|good morning|good evening)[!. ]*$", re.I)
_INTERRUPTION_RE = re.compile(r"\b(wait|hold on|one second|ruk|ruko|stop|no wait)\b", re.I)
_FAQ_COMPLETION_RE = re.compile(r"\b(thank you|thanks|ok|okay|got it|fine)\b", re.I)
_NOISE_RE = re.compile(r"\b(um+|uh+|hmm+|noise|inaudible)\b", re.I)


@dataclass(frozen=True)
class CompressionResult:
    summary: str
    original_chars: int
    compressed_chars: int
    pruned_turns: int
    compressed_turns: int
    preserved_runtime_fields: tuple[str, ...]
    failed: bool = False

    @property
    def compression_ratio(self) -> float:
        if self.original_chars <= 0:
            return 1.0
        return round(self.compressed_chars / self.original_chars, 3)


@dataclass(frozen=True)
class CacheStats:
    hits: int
    misses: int
    evictions: int
    size: int


class MemoryPruningPolicy:
    """Prune conversational noise while preserving active runtime continuity."""

    def __init__(
        self,
        *,
        max_recent_turns: int = 6,
        keep_active_workflow_turns: int = 8,
        max_interruption_turns: int = 2,
    ) -> None:
        self._max_recent_turns = max(2, max_recent_turns)
        self._keep_active_workflow_turns = max(
            self._max_recent_turns,
            keep_active_workflow_turns,
        )
        self._max_interruption_turns = max(1, max_interruption_turns)

    def prune(self, memory: Any, *, request_id: str | None = None) -> int:
        turns = list(getattr(memory, "recent_turns", []))
        if not turns:
            return 0
        active_workflow = _has_active_runtime_state(memory)
        target = self._keep_active_workflow_turns if active_workflow else self._max_recent_turns

        kept: list[dict[str, str]] = []
        interruption_kept = 0
        for turn in reversed(turns):
            text = _clean(turn.get("text", ""))
            if not text:
                continue
            if self._is_prunable_noise(text, memory=memory):
                continue
            if _INTERRUPTION_RE.search(text):
                interruption_kept += 1
                if interruption_kept > self._max_interruption_turns:
                    continue
            kept.append({"role": turn.get("role", ""), "text": text[:240]})
            if len(kept) >= target:
                break
        kept.reverse()

        pruned = max(0, len(turns) - len(kept))
        if pruned:
            memory.recent_turns = kept
            memory.pruned_turn_count = getattr(memory, "pruned_turn_count", 0) + pruned
            log_event(
                logger,
                "memory_pruned",
                request_id=request_id,
                session_id=getattr(memory, "session_id", None),
                memory_pruned=pruned,
                recent_turn_count=len(kept),
                active_workflow=active_workflow,
            )
        return pruned

    def _is_prunable_noise(self, text: str, *, memory: Any) -> bool:
        if _GREETING_RE.match(text):
            return True
        if _NOISE_RE.search(text) and len(text) <= 32:
            return True
        if _FAQ_COMPLETION_RE.search(text) and not _has_active_runtime_state(memory):
            return True
        return False


class RollingMemoryCompressor:
    """Deterministically compress old conversational turns, never critical state."""

    def __init__(
        self,
        *,
        compress_after_turns: int = 8,
        keep_recent_turns: int = 4,
        max_summary_chars: int = 360,
    ) -> None:
        self._compress_after_turns = max(4, compress_after_turns)
        self._keep_recent_turns = max(2, keep_recent_turns)
        self._max_summary_chars = max(160, max_summary_chars)
        self._lock = threading.RLock()

    def compress(self, memory: Any, *, request_id: str | None = None) -> CompressionResult:
        with self._lock:
            try:
                turns = list(getattr(memory, "recent_turns", []))
                original_chars = sum(len(turn.get("text", "")) for turn in turns)
                if len(turns) <= self._compress_after_turns:
                    summary = getattr(memory, "rolling_context_summary", "")
                    return CompressionResult(
                        summary=summary,
                        original_chars=original_chars,
                        compressed_chars=len(summary),
                        pruned_turns=0,
                        compressed_turns=getattr(memory, "compressed_context_turns", 0),
                        preserved_runtime_fields=_critical_runtime_fields(memory),
                    )

                old_turns = turns[: -self._keep_recent_turns]
                recent_turns = turns[-self._keep_recent_turns :]
                summary = self._build_summary(memory, old_turns)
                previous_summary = _clean(getattr(memory, "rolling_context_summary", ""))
                if previous_summary and previous_summary not in summary:
                    summary = _truncate(f"{previous_summary}; {summary}", self._max_summary_chars)
                memory.rolling_context_summary = summary
                memory.compressed_context_turns = getattr(memory, "compressed_context_turns", 0) + len(old_turns)
                memory.recent_turns = [
                    {"role": turn.get("role", ""), "text": _clean(turn.get("text", ""))[:240]}
                    for turn in recent_turns
                    if _clean(turn.get("text", ""))
                ]
                result = CompressionResult(
                    summary=summary,
                    original_chars=original_chars,
                    compressed_chars=len(summary),
                    pruned_turns=len(old_turns),
                    compressed_turns=getattr(memory, "compressed_context_turns", 0),
                    preserved_runtime_fields=_critical_runtime_fields(memory),
                )
                log_event(
                    logger,
                    "rolling_context_compressed",
                    request_id=request_id,
                    session_id=getattr(memory, "session_id", None),
                    prompt_size=None,
                    compression_ratio=result.compression_ratio,
                    memory_pruned=result.pruned_turns,
                    original_chars=result.original_chars,
                    compressed_chars=result.compressed_chars,
                    compressed_turns=result.compressed_turns,
                    preserved_runtime_fields=list(result.preserved_runtime_fields),
                )
                return result
            except Exception as exc:
                log_event(
                    logger,
                    "rolling_context_compression_failed",
                    request_id=request_id,
                    session_id=getattr(memory, "session_id", None),
                    error_type=type(exc).__name__,
                )
                return CompressionResult(
                    summary=getattr(memory, "rolling_context_summary", ""),
                    original_chars=0,
                    compressed_chars=0,
                    pruned_turns=0,
                    compressed_turns=getattr(memory, "compressed_context_turns", 0),
                    preserved_runtime_fields=_critical_runtime_fields(memory),
                    failed=True,
                )

    def _build_summary(self, memory: Any, turns: list[dict[str, str]]) -> str:
        caller_turns = sum(1 for turn in turns if turn.get("role") == "caller")
        assistant_turns = sum(1 for turn in turns if turn.get("role") == "assistant")
        topics = _topic_labels(turn.get("text", "") for turn in turns)
        if not topics:
            topics = ("general clinic conversation",)
        critical = []
        if getattr(memory, "escalation_triggered", False):
            critical.append(f"escalation active: {getattr(memory, 'escalation_reason', None) or 'unspecified'}")
        if getattr(memory, "booking", None) is not None and getattr(memory.booking, "active_correction", None):
            critical.append(f"correction: {memory.booking.active_correction}")
        body = (
            f"Compressed prior call: {caller_turns} caller turns/{assistant_turns} receptionist turns; "
            f"topics={', '.join(topics[:5])}"
        )
        if critical:
            body = f"{body}; {'; '.join(critical)}"
        return _truncate(body, self._max_summary_chars)


class _BoundedCache(Generic[T]):
    def __init__(
        self,
        *,
        max_entries: int = 128,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._ttl_seconds = max(0.0, ttl_seconds)
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> T | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                self._misses += 1
                return None
            created_at, value = item
            if self._ttl_seconds and self._clock() - created_at > self._ttl_seconds:
                self._items.pop(key, None)
                self._misses += 1
                return None
            self._items.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: T) -> None:
        with self._lock:
            self._items[key] = (self._clock(), value)
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)
                self._evictions += 1

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                size=len(self._items),
            )


class PromptCacheLayer:
    """Bounded prompt-fragment cache for stable runtime instructions."""

    def __init__(self, *, max_entries: int = 128, ttl_seconds: float = 900.0) -> None:
        self._cache: _BoundedCache[str] = _BoundedCache(
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )

    def business_fragment(self, business: BusinessConfig, services: tuple[str, ...]) -> str:
        key = "business:" + _business_fingerprint(business, services)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fragment = (
            f"Biz: {business.name} ({business.business_type}); "
            f"services: {_join_or_none(services)}; "
            f"tone: {business.receptionist_tone}; persona: {business.receptionist_personality}."
        )
        self._cache.set(key, fragment)
        return fragment

    def language_fragment(self, language: Any) -> str:
        active = getattr(language, "active_language", "english")
        response_language = getattr(language, "openai_response_language", active)
        confidence = float(getattr(language, "confidence", 1.0) or 0.0)
        generation = int(getattr(language, "generation", 0) or 0)
        key = f"language:{active}:{response_language}:{round(confidence, 2)}:{generation}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fragment = (
            f"Lang: Respond in natural {response_language}; "
            f"active={active}; confidence={confidence:.2f}; generation={generation}."
        )
        self._cache.set(key, fragment)
        return fragment

    def orchestration_fragment(self, classification: str) -> str:
        key = f"orchestration:{classification}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        fragment = (
            f"Intent: {classification}. Rules: answer from context only; "
            "one or two phone-call sentences; ask one missing booking field; refuse outside scope."
        )
        self._cache.set(key, fragment)
        return fragment

    def stats(self) -> CacheStats:
        return self._cache.stats()


class RetrievalCache:
    """Bounded FAQ retrieval cache for repeated caller intents."""

    def __init__(self, *, max_entries: int = 128, ttl_seconds: float = 180.0) -> None:
        self._cache: _BoundedCache[FAQRetrievalResult] = _BoundedCache(
            max_entries=max_entries,
            ttl_seconds=ttl_seconds,
        )

    def get_or_compute(
        self,
        *,
        transcript: str,
        business: BusinessConfig,
        intent_terms: tuple[str, ...],
        compute: Callable[[], FAQRetrievalResult],
        request_id: str | None = None,
    ) -> FAQRetrievalResult:
        key = self.key_for(
            transcript=transcript,
            business=business,
            intent_terms=intent_terms,
        )
        cached = self._cache.get(key)
        if cached is not None:
            result = FAQRetrievalResult(
                matches=cached.matches,
                confidence=cached.confidence,
                latency_ms=0.0,
                injected_chars=cached.injected_chars,
                injection_text=cached.injection_text,
                source=f"{cached.source}:cache_hit",
                failure_reason=cached.failure_reason,
            )
            log_event(
                logger,
                "faq_retrieval_cache_hit",
                request_id=request_id,
                cache_hits=self.stats().hits,
                cache_misses=self.stats().misses,
                retrieval_latency=0.0,
                selected_faq_questions=list(result.selected_questions),
            )
            return result
        result = compute()
        self._cache.set(key, result)
        stats = self.stats()
        log_event(
            logger,
            "faq_retrieval_cache_miss",
            request_id=request_id,
            cache_hits=stats.hits,
            cache_misses=stats.misses,
            retrieval_latency=result.latency_ms,
            selected_faq_questions=list(result.selected_questions),
        )
        return result

    def key_for(
        self,
        *,
        transcript: str,
        business: BusinessConfig,
        intent_terms: tuple[str, ...],
    ) -> str:
        payload = "|".join(
            (
                _normalize(transcript),
                _business_fingerprint(business, business.services),
                _normalize(" ".join(sorted(term for term in intent_terms if term))),
            )
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()

    def stats(self) -> CacheStats:
        return self._cache.stats()


def _has_active_runtime_state(memory: Any) -> bool:
    booking = getattr(memory, "booking", None)
    return bool(
        getattr(memory, "escalation_triggered", False)
        or getattr(memory, "pending_booking_fields", ())
        or getattr(memory, "booking_stage", None)
        and getattr(getattr(memory, "booking_stage", None), "value", "idle") != "idle"
        or booking
        and (
            getattr(booking, "awaiting_confirmation", False)
            or getattr(booking, "confirmation_completed", False)
            or getattr(booking, "active_correction", None)
        )
    )


def _critical_runtime_fields(memory: Any) -> tuple[str, ...]:
    fields = ["active_language", "workflow_stage"]
    if getattr(memory, "pending_booking_fields", ()):
        fields.append("booking_fields")
    if getattr(memory, "escalation_triggered", False):
        fields.append("escalation_state")
    booking = getattr(memory, "booking", None)
    if booking is not None and getattr(booking, "active_correction", None):
        fields.append("correction_history")
    runtime_memory = getattr(memory, "runtime_memory", None)
    if runtime_memory is not None and runtime_memory.unresolved_questions():
        fields.append("unresolved_issues")
    return tuple(dict.fromkeys(fields))


def _topic_labels(texts: Any) -> tuple[str, ...]:
    joined = " ".join(_normalize(str(text)) for text in texts)
    labels = []
    checks = (
        ("booking", ("book", "appointment", "slot", "visit")),
        ("faq", ("hours", "price", "parking", "address", "location")),
        ("correction", ("actually", "change", "instead", "wrong")),
        ("interruption", ("wait", "hold", "stop", "ruko")),
        ("escalation", ("human", "operator", "manager", "supervisor")),
        ("multilingual", ("hinglish", "hindi", "kannada", "telugu", "marathi")),
    )
    for label, markers in checks:
        if any(marker in joined for marker in markers):
            labels.append(label)
    return tuple(labels)


def _business_fingerprint(business: BusinessConfig, services: tuple[str, ...]) -> str:
    faq_bits = ";".join(f"{faq.question}:{faq.answer}" for faq in business.faqs)
    payload = "|".join(
        (
            business.name,
            business.business_type,
            ";".join(services),
            business.receptionist_tone,
            business.receptionist_personality,
            faq_bits,
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _join_or_none(values: tuple[str, ...]) -> str:
    return "; ".join(values) if values else "none configured"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."
