from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.language import (
    SessionLanguageSnapshot,
    build_openai_language_instruction,
    default_language_snapshot,
)
from voice_agent.logging_config import get_logger, log_event
from voice_agent.prompts.identity import SYSTEM_PROMPT
from voice_agent.retrieval import FAQRetrievalEngine
from voice_agent.optimization import (
    MemoryPruningPolicy,
    PromptCacheLayer,
    RetrievalCache,
    RollingMemoryCompressor,
    RuntimeLatencyProfiler,
)

if TYPE_CHECKING:
    from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FAQ_SYNONYMS = (
    frozenset({"hour", "hours", "open", "opening", "close", "closing", "time", "timing", "timings"}),
    frozenset({"address", "location", "located", "where"}),
    frozenset({"cost", "charge", "charges", "fee", "fees", "price", "pricing"}),
    frozenset({"phone", "call", "contact", "number"}),
    frozenset({"book", "booking", "appointment", "schedule", "visit"}),
)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "can",
    "do",
    "for",
    "have",
    "help",
    "i",
    "in",
    "is",
    "me",
    "my",
    "of",
    "on",
    "please",
    "sir",
    "the",
    "to",
    "what",
    "when",
    "where",
    "with",
    "you",
    "your",
}


@dataclass(frozen=True)
class PromptIntent:
    classification: str
    generation_source: str = "openai"
    matched_service: str | None = None
    requested_service: str | None = None
    matched_faq: str | None = None


@dataclass(frozen=True)
class RealtimePrompt:
    instructions: str
    input_text: str
    prompt_chars: int
    memory_chars: int
    faq_injection_count: int
    faq_injection_chars: int
    faq_retrieval_confidence: float
    faq_retrieval_latency_ms: float
    faq_retrieval_source: str
    selected_faq_questions: tuple[str, ...]
    composition_latency_ms: float
    recomposition_index: int
    compression_ratio: float = 1.0
    memory_pruned: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


class RealtimePromptManager:
    """Compose compact per-turn prompts from live runtime state.

    The manager intentionally avoids transcript history and heavyweight retrieval.
    FAQ selection is delegated to FAQRetrievalEngine so each GPT call gets
    current memory, language, booking, escalation, and only relevant FAQ context
    without the old full static business prompt.
    """

    def __init__(
        self,
        *,
        max_prompt_chars: int = 1500,
        top_faqs: int = 2,
        max_services: int = 6,
        faq_retrieval_engine: FAQRetrievalEngine | None = None,
        prompt_cache: PromptCacheLayer | None = None,
        retrieval_cache: RetrievalCache | None = None,
        memory_pruning_policy: MemoryPruningPolicy | None = None,
        rolling_memory_compressor: RollingMemoryCompressor | None = None,
        latency_profiler: RuntimeLatencyProfiler | None = None,
        clock=time.perf_counter,
    ) -> None:
        self._max_prompt_chars = max(500, min(max_prompt_chars, 1200))
        self._top_faqs = top_faqs
        self._max_services = max_services
        self._prompt_cache = prompt_cache or PromptCacheLayer()
        self._retrieval_cache = retrieval_cache or RetrievalCache()
        self._faq_retrieval_engine = faq_retrieval_engine or FAQRetrievalEngine(
            top_k=top_faqs,
            retrieval_cache=self._retrieval_cache,
        )
        self._memory_pruning_policy = memory_pruning_policy or MemoryPruningPolicy()
        self._rolling_memory_compressor = rolling_memory_compressor or RollingMemoryCompressor()
        self._latency_profiler = latency_profiler or RuntimeLatencyProfiler()
        self._clock = clock
        self._recomposition_count = 0

    def compose(
        self,
        *,
        transcript: str,
        business: BusinessConfig,
        memory: "CallSessionMemory",
        language: SessionLanguageSnapshot | None = None,
        intent: PromptIntent | None = None,
        request_id: str | None = None,
    ) -> RealtimePrompt:
        started_at = self._clock()
        self._recomposition_count += 1
        language = language or default_language_snapshot()
        intent = intent or PromptIntent(classification="business_context")

        with self._latency_profiler.span("memory_assembly", request_id=request_id):
            compression = self._rolling_memory_compressor.compress(
                memory,
                request_id=request_id,
            )
            pruned = self._memory_pruning_policy.prune(memory, request_id=request_id)

        faq_result = self._faq_retrieval_engine.retrieve(
            transcript=transcript,
            business=business,
            intent_terms=_intent_terms(intent),
            request_id=request_id,
        )
        self._latency_profiler.record(
            "retrieval",
            faq_result.latency_ms,
            request_id=request_id,
        )
        memory.record_faq_retrieval(
            matched_count=len(faq_result.matches),
            confidence=faq_result.confidence,
            injected_chars=faq_result.injected_chars,
            latency_ms=faq_result.latency_ms,
            source=faq_result.source,
            selected_questions=faq_result.selected_questions,
            request_id=request_id,
            failure_reason=faq_result.failure_reason,
        )
        services = self._select_relevant_services(
            transcript=transcript,
            business=business,
            intent=intent,
        )
        memory_block = self._memory_block(memory=memory, language=language, intent=intent)
        instructions = self._instructions(
            business=business,
            services=services,
            faq_block=faq_result.injection_text,
            memory_block=memory_block,
            language=language,
            intent=intent,
        )
        if len(instructions) > self._max_prompt_chars:
            instructions = self._compressed_instructions(
                business=business,
                services=services,
                faq_block=faq_result.injection_text,
                memory_block=memory_block,
                language=language,
                intent=intent,
            )
        if len(instructions) > self._max_prompt_chars:
            instructions = self._minimal_instructions(
                business=business,
                services=services,
                faq_block=faq_result.injection_text,
                memory_block=memory_block,
                language=language,
                intent=intent,
            )

        input_text = self._input_text(
            transcript=transcript,
            language=language,
            intent=intent,
        )
        latency_ms = round((self._clock() - started_at) * 1000, 3)
        self._latency_profiler.record(
            "prompt_composition",
            latency_ms,
            request_id=request_id,
        )
        prompt_cache_stats = self._prompt_cache.stats()
        retrieval_cache_stats = self._retrieval_cache.stats()
        prompt = RealtimePrompt(
            instructions=instructions,
            input_text=input_text,
            prompt_chars=len(instructions),
            memory_chars=len(memory_block),
            faq_injection_count=len(faq_result.matches),
            faq_injection_chars=faq_result.injected_chars,
            faq_retrieval_confidence=faq_result.confidence,
            faq_retrieval_latency_ms=faq_result.latency_ms,
            faq_retrieval_source=faq_result.source,
            selected_faq_questions=faq_result.selected_questions,
            composition_latency_ms=latency_ms,
            recomposition_index=self._recomposition_count,
            compression_ratio=compression.compression_ratio,
            memory_pruned=pruned + compression.pruned_turns,
            cache_hits=prompt_cache_stats.hits + retrieval_cache_stats.hits,
            cache_misses=prompt_cache_stats.misses + retrieval_cache_stats.misses,
        )
        log_event(
            logger,
            "realtime_prompt_recomposed",
            request_id=request_id,
            recomposition_index=prompt.recomposition_index,
            prompt_chars=prompt.prompt_chars,
            prompt_size=prompt.prompt_chars,
            compression_ratio=prompt.compression_ratio,
            memory_pruned=prompt.memory_pruned,
            memory_growth=memory.runtime_memory.turn_index,
            runtime_memory_turns=memory.runtime_memory.turn_index,
            cache_hits=prompt.cache_hits,
            cache_misses=prompt.cache_misses,
            memory_injection_chars=prompt.memory_chars,
            faq_injection_count=prompt.faq_injection_count,
            faq_injection_chars=prompt.faq_injection_chars,
            faq_retrieval_confidence=prompt.faq_retrieval_confidence,
            faq_retrieval_latency_ms=prompt.faq_retrieval_latency_ms,
            retrieval_latency=prompt.faq_retrieval_latency_ms,
            faq_retrieval_source=prompt.faq_retrieval_source,
            composition_latency_ms=prompt.composition_latency_ms,
            memory_latency=None,
            orchestration_latency=None,
            gpt_latency=None,
            tts_latency=None,
            total_response_time=None,
            response_latency=None,
            current_intent=intent.classification,
            generation_source=intent.generation_source,
            active_language=language.active_language,
            language_generation=language.generation,
            booking_stage=memory.booking_stage.value,
            pending_booking_fields=list(memory.pending_booking_fields),
            escalation_triggered=memory.escalation_triggered,
        )
        return prompt

    def select_relevant_faqs(
        self,
        *,
        transcript: str,
        business: BusinessConfig,
        intent: PromptIntent | None = None,
    ) -> tuple[BusinessFAQ, ...]:
        return self._faq_retrieval_engine.retrieve(
            transcript=transcript,
            business=business,
            intent_terms=_intent_terms(intent),
        ).faqs

    def _instructions(
        self,
        *,
        business: BusinessConfig,
        services: tuple[str, ...],
        faq_block: str,
        memory_block: str,
        language: SessionLanguageSnapshot,
        intent: PromptIntent,
    ) -> str:
        return "\n".join(
            section
            for section in (
                _compact_system_prompt(),
                "Realtime context only; no hidden transcript history.",
                self._prompt_cache.business_fragment(business, services),
                self._prompt_cache.orchestration_fragment(intent.classification),
                self._prompt_cache.language_fragment(language),
                memory_block,
                faq_block,
            )
            if section.strip()
        )

    def _compressed_instructions(
        self,
        *,
        business: BusinessConfig,
        services: tuple[str, ...],
        faq_block: str,
        memory_block: str,
        language: SessionLanguageSnapshot,
        intent: PromptIntent,
    ) -> str:
        compact_identity = (
            "You are a calm human receptionist on a live phone call. "
            "Answer only from injected business/runtime context. "
            "Never answer unrelated general knowledge or invent booking confirmations."
        )
        return "\n".join(
            section
            for section in (
                compact_identity,
                self._prompt_cache.business_fragment(business, services),
                self._prompt_cache.orchestration_fragment(intent.classification),
                self._prompt_cache.language_fragment(language),
                memory_block,
                faq_block,
                "Reply in 1-2 short sentences. Ask one missing booking field at a time. Refuse outside scope.",
            )
            if section.strip()
        )

    def _minimal_instructions(
        self,
        *,
        business: BusinessConfig,
        services: tuple[str, ...],
        faq_block: str,
        memory_block: str,
        language: SessionLanguageSnapshot,
        intent: PromptIntent,
    ) -> str:
        compact = "\n".join(
            section
            for section in (
                "You are a calm live-call receptionist. Use only injected context; never invent.",
                self._prompt_cache.business_fragment(business, services),
                self._prompt_cache.orchestration_fragment(intent.classification),
                self._prompt_cache.language_fragment(language),
                _truncate(memory_block, 520),
                _truncate(faq_block, 220),
                "ask_policy: reply in 1-2 short sentences; ask one missing field; refuse outside scope.",
            )
            if section.strip()
        )
        if len(compact) <= self._max_prompt_chars:
            return compact
        return _truncate(compact, self._max_prompt_chars)

    def _input_text(
        self,
        *,
        transcript: str,
        language: SessionLanguageSnapshot,
        intent: PromptIntent,
    ) -> str:
        return (
            f"Caller language: {language.openai_response_language} "
            f"(confidence {language.confidence:.2f}).\n"
            f"Current intent: {intent.classification}.\n"
            f"Caller said: {transcript.strip()}\n"
            "Receptionist response:"
        )

    def _memory_block(
        self,
        *,
        memory: "CallSessionMemory",
        language: SessionLanguageSnapshot,
        intent: PromptIntent,
    ) -> str:
        runtime_memory = getattr(memory, "runtime_memory", None)
        if runtime_memory is not None:
            try:
                runtime_memory.update_from_session(
                    memory,
                    language=language,
                )
                summary = getattr(memory, "rolling_context_summary", "")
                block = runtime_memory.build_injection(
                    intent_classification=intent.classification,
                    max_chars=620 if summary else None,
                )
                extra_lines = []
                if intent.matched_service:
                    extra_lines.append(f"- matched_service: {intent.matched_service}")
                if intent.requested_service:
                    extra_lines.append(f"- requested_service: {intent.requested_service}")
                if extra_lines:
                    block = block + "\n" + "\n".join(extra_lines)
                if summary:
                    remaining = max(80, 760 - len(block) - len("\n- compressed_context: "))
                    block = block + f"\n- compressed_context: {_truncate(summary, remaining)}"
                return block
            except Exception as exc:
                log_event(
                    logger,
                    "runtime_memory_injection_fallback",
                    error_type=type(exc).__name__,
                    session_id=getattr(memory, "session_id", None),
                )

        captured = _booking_captured_fields(memory)
        pending = ", ".join(memory.pending_booking_fields) or "none"
        correction = memory.booking.active_correction or "none"
        escalation = (
            f"triggered ({memory.escalation_reason})"
            if memory.escalation_triggered
            else "not_triggered"
        )
        lines = [
            "Runtime memory:",
            f"- caller_name: {memory.caller_name or 'unknown'}",
            f"- booking_stage: {memory.booking_stage.value}; pending: {pending}",
            f"- booking_captured: {captured or 'none'}",
            f"- awaiting_confirmation: {memory.booking.awaiting_confirmation}; confirmation_completed: {memory.booking.confirmation_completed}",
            f"- booking_truth: calcom_uid={memory.booking.calcom_uid or 'none'}; external_status={memory.booking.external_status or 'none'}; validation={memory.booking.booking_validation_state or 'none'}",
            f"- active_correction: {correction}",
            f"- language_state: active={language.active_language}; previous={language.previous_language or 'none'}; generation={language.generation}",
            f"- escalation_state: {escalation}",
        ]
        if intent.matched_service:
            lines.append(f"- matched_service: {intent.matched_service}")
        if intent.requested_service:
            lines.append(f"- requested_service: {intent.requested_service}")
        summary = getattr(memory, "rolling_context_summary", "")
        if summary:
            lines.append(f"- compressed_context: {summary}")
        return "\n".join(lines)

    def _select_relevant_services(
        self,
        *,
        transcript: str,
        business: BusinessConfig,
        intent: PromptIntent,
    ) -> tuple[str, ...]:
        query_tokens = _expanded_tokens(_tokens(transcript))
        query_tokens |= _tokens(intent.matched_service or "")
        query_tokens |= _tokens(intent.requested_service or "")
        scored: list[tuple[float, int, str]] = []
        for index, service in enumerate(business.services):
            service_tokens = _significant_tokens(_tokens(service))
            if not service_tokens:
                continue
            overlap = len(service_tokens & query_tokens)
            if service == intent.matched_service:
                overlap += len(service_tokens)
            if overlap:
                scored.append((overlap / len(service_tokens), index, service))
        if not scored:
            return tuple(business.services[: self._max_services])
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored[: self._max_services])


def _booking_captured_fields(memory: Any) -> str:
    fields = {
        "customer_name": memory.caller_name,
        "phone_number": memory.phone_number,
        "service_type": memory.selected_service,
        "appointment_date": memory.preferred_date,
        "appointment_time": memory.preferred_time,
        "doctor_preference": memory.doctor_preference,
        "notes": memory.optional_notes,
    }
    return "; ".join(f"{name}={value}" for name, value in fields.items() if value not in (None, ""))


def _intent_terms(intent: PromptIntent | None) -> tuple[str, ...]:
    if intent is None:
        return ()
    return tuple(
        term
        for term in (
            intent.matched_faq,
            intent.requested_service,
            intent.matched_service,
            intent.classification,
        )
        if term
    )


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _expanded_tokens(tokens: Iterable[str]) -> set[str]:
    expanded = set(tokens)
    for group in _FAQ_SYNONYMS:
        if expanded & group:
            expanded.update(group)
    return expanded


def _significant_tokens(tokens: Iterable[str]) -> set[str]:
    return {token for token in tokens if token not in _STOPWORDS and len(token) >= 2}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _join_or_none(values: tuple[str, ...]) -> str:
    return "; ".join(values) if values else "none configured"


def _compact_system_prompt() -> str:
    if len(SYSTEM_PROMPT) <= 220:
        return SYSTEM_PROMPT
    return (
        "You are a calm human receptionist on a live phone call. "
        "Use only injected business/runtime context; do not hallucinate; "
        "keep replies warm, concise, and operational."
    )
