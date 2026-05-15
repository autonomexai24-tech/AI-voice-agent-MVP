from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.language import SessionLanguageSnapshot
from voice_agent.logging_config import get_logger, log_error, log_event
from voice_agent.prompts.business import (
    business_memory_from_config,
    business_prompt as render_business_prompt,
)
from voice_agent.prompts.composer import PromptContext, compose_prompt
from voice_agent.prompts.identity import SYSTEM_PROMPT

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SERVICE_REQUEST_PATTERNS = (
    re.compile(
        r"\b(?:do|can)\s+you\s+(?:provide|offer|do|have)\s+(?P<service>.+?)(?:\?|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:is|are)\s+(?P<service>.+?)\s+(?:available|provided|offered)(?:\?|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i\s+need|i\s+want|looking\s+for)\s+(?P<service>.+?)(?:\?|$)",
        re.IGNORECASE,
    ),
)
_SERVICE_STOPWORDS = {
    "a",
    "an",
    "and",
    "appointment",
    "available",
    "book",
    "booking",
    "care",
    "check",
    "consultation",
    "do",
    "for",
    "get",
    "have",
    "help",
    "i",
    "is",
    "need",
    "please",
    "provide",
    "service",
    "services",
    "sir",
    "the",
    "treatment",
    "want",
    "you",
}
_BUSINESS_KEYWORDS = {
    "address",
    "appointment",
    "available",
    "book",
    "booking",
    "call",
    "charges",
    "clinic",
    "close",
    "consult",
    "consultation",
    "cost",
    "doctor",
    "fee",
    "fees",
    "help",
    "hours",
    "location",
    "open",
    "price",
    "reception",
    "service",
    "services",
    "timing",
    "timings",
    "treat",
    "treatment",
    "visit",
}
_GREETING_TOKENS = {"hello", "hi", "hey", "namaste", "good", "morning", "evening"}
_UNRELATED_TOPIC_PHRASES = (
    "prime minister",
    "president",
    "capital of",
    "weather",
    "stock price",
    "cricket score",
    "latest news",
    "movie",
    "recipe",
    "write code",
    "python code",
)
_FAQ_SYNONYMS = (
    frozenset({"hour", "hours", "open", "opening", "close", "closing", "time", "timing", "timings"}),
    frozenset({"address", "location", "located", "where"}),
    frozenset({"cost", "charge", "charges", "fee", "fees", "price", "pricing"}),
    frozenset({"phone", "call", "contact", "number"}),
)


@dataclass(frozen=True)
class BusinessPromptDecision:
    classification: str
    generation_source: str
    instructions: str | None
    input_text: str | None
    response_text: str | None
    matched_service: str | None = None
    matched_faq: str | None = None
    requested_service: str | None = None

    @property
    def uses_model(self) -> bool:
        return self.response_text is None


@dataclass(frozen=True)
class _Intent:
    classification: str
    matched_service: str | None = None
    matched_faq: BusinessFAQ | None = None
    requested_service: str | None = None
    reason: str | None = None


class BusinessPromptOrchestrator:
    def __init__(
        self,
        config: BusinessConfig,
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._base_config = config
        self._clock = clock
        self._load_lock = asyncio.Lock()
        self._loaded_context: BusinessConfig | None = None
        self._context_source = "env"

    async def load(self) -> BusinessConfig:
        if self._loaded_context is not None:
            return self._loaded_context

        async with self._load_lock:
            if self._loaded_context is not None:
                return self._loaded_context

            started_at = self._clock()
            try:
                if self._base_config.context_path:
                    context = await asyncio.to_thread(
                        _load_context_file,
                        self._base_config.context_path,
                        self._base_config,
                    )
                    self._context_source = "file"
                else:
                    context = self._base_config
                    self._context_source = "env"
            except Exception as exc:
                log_error(
                    logger,
                    "business_prompt_load_failed",
                    context_path=self._base_config.context_path,
                    error=str(exc),
                )
                raise

            self._loaded_context = context
            full_prompt = _build_business_prompt(context)
            prompt_chars = len(full_prompt)
            log_event(
                logger,
                "business_prompt_loaded",
                context_source=self._context_source,
                business_name=context.name,
                business_type=context.business_type,
                services_count=len(context.services),
                services=list(context.services),
                faqs_count=len(context.faqs),
                faq_questions=[faq.question for faq in context.faqs],
                receptionist_tone=context.receptionist_tone,
                prompt_chars=prompt_chars,
                latency_ms=round((self._clock() - started_at) * 1000, 3),
            )
            if not context.services:
                log_event(
                    logger,
                    "business_prompt_warning_no_services",
                    message="No BUSINESS_SERVICES configured; service queries will use fallback responses",
                )
            if not context.faqs:
                log_event(
                    logger,
                    "business_prompt_warning_no_faqs",
                    message="No BUSINESS_FAQS configured; FAQ queries will fall through to OpenAI",
                )
            return context

    async def prepare_response(
        self,
        transcript: str,
        *,
        language: SessionLanguageSnapshot,
        request_id: str | None = None,
    ) -> BusinessPromptDecision:
        context = await self.load()
        intent = _classify(transcript, context)

        log_event(
            logger,
            "business_context_applied",
            request_id=request_id,
            business_name=context.name,
            business_type=context.business_type,
            services_count=len(context.services),
            faqs_count=len(context.faqs),
            classification=intent.classification,
            generation_source=(
                "openai" if intent.classification == "business_context" else "local_guardrail"
            ),
            response_language=language.active_language,
            language_generation=language.generation,
        )

        if intent.classification == "unrelated":
            log_event(
                logger,
                "business_refusal",
                request_id=request_id,
                reason=intent.reason or "outside_business_scope",
                transcript_chars=len(transcript),
                business_type=context.business_type,
            )
            log_event(
                logger,
                "refusal_triggered",
                request_id=request_id,
                reason=intent.reason or "outside_business_scope",
                business_name=context.name,
                business_type=context.business_type,
                active_language=language.active_language,
            )
            return BusinessPromptDecision(
                classification="unrelated",
                generation_source="local_guardrail",
                instructions=None,
                input_text=None,
                response_text=_render_refusal(context, language),
            )

        if intent.classification == "unsupported_service":
            log_event(
                logger,
                "unsupported_service_request",
                request_id=request_id,
                requested_service=intent.requested_service,
                services_count=len(context.services),
            )
            return BusinessPromptDecision(
                classification="unsupported_service",
                generation_source="local_guardrail",
                instructions=None,
                input_text=None,
                response_text=_render_unsupported_service(
                    context,
                    intent.requested_service,
                    language,
                ),
                requested_service=intent.requested_service,
            )

        if intent.classification == "service_list":
            return BusinessPromptDecision(
                classification="service_list",
                generation_source="local_guardrail",
                instructions=None,
                input_text=None,
                response_text=_render_service_list(context, language),
            )

        if intent.classification == "service_supported":
            return BusinessPromptDecision(
                classification="service_supported",
                generation_source="local_guardrail",
                instructions=None,
                input_text=None,
                response_text=_render_supported_service(
                    context,
                    intent.matched_service or "",
                    language,
                ),
                matched_service=intent.matched_service,
                requested_service=intent.requested_service,
            )

        if intent.classification == "faq" and intent.matched_faq is not None:
            return BusinessPromptDecision(
                classification="faq",
                generation_source="local_guardrail",
                instructions=None,
                input_text=None,
                response_text=_phone_friendly_text(intent.matched_faq.answer),
                matched_faq=intent.matched_faq.question,
            )

        return BusinessPromptDecision(
            classification="business_context",
            generation_source="openai",
            instructions=_build_instructions(context, language),
            input_text=_build_input(transcript, language),
            response_text=None,
        )


def _load_context_file(path: str, fallback: BusinessConfig) -> BusinessConfig:
    file_path = Path(path)
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("business context file must contain a JSON object")
    return _business_config_from_mapping(payload, fallback)


def _business_config_from_mapping(payload: dict[str, Any], fallback: BusinessConfig) -> BusinessConfig:
    return BusinessConfig(
        name=_string_value(payload, "business_name", fallback.name),
        business_type=_string_value(payload, "business_type", fallback.business_type),
        services=_services_value(payload.get("services"), fallback.services),
        faqs=_faqs_value(payload.get("faqs"), fallback.faqs),
        receptionist_tone=_string_value(
            payload,
            "receptionist_tone",
            fallback.receptionist_tone,
        ),
        refusal_behavior=_string_value(
            payload,
            "refusal_behavior",
            fallback.refusal_behavior,
        ),
        receptionist_personality=_string_value(
            payload,
            "receptionist_personality",
            fallback.receptionist_personality,
        ),
        context_path=fallback.context_path,
    )


def _string_value(payload: dict[str, Any], key: str, default: str) -> str:
    value = payload.get(key, default)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _services_value(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        raw_values = re.split(r"[\n;,|]", value)
    elif isinstance(value, list):
        raw_values = [str(item) for item in value]
    else:
        raise ValueError("services must be a list or separated string")
    return tuple(_dedupe(text.strip() for text in raw_values))


def _faqs_value(value: Any, default: tuple[BusinessFAQ, ...]) -> tuple[BusinessFAQ, ...]:
    if value is None:
        return default
    if not isinstance(value, list):
        raise ValueError("faqs must be a list")

    faqs: list[BusinessFAQ] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"faqs item {index} must be an object")
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if not question or not answer:
            raise ValueError(f"faqs item {index} must include question and answer")
        faqs.append(BusinessFAQ(question=question, answer=answer))
    return tuple(faqs)


def _classify(transcript: str, context: BusinessConfig) -> _Intent:
    normalized = _normalize(transcript)
    tokens = _expanded_tokens(_tokens(normalized))
    requested_service = _extract_requested_service(transcript)

    if _is_service_list_question(normalized, tokens):
        return _Intent("service_list")

    matched_faq = _match_faq(normalized, tokens, context.faqs)
    if matched_faq is not None:
        return _Intent("faq", matched_faq=matched_faq)

    matched_service = _match_service(normalized, tokens, context.services)
    if requested_service is not None:
        if matched_service is not None:
            return _Intent(
                "service_supported",
                matched_service=matched_service,
                requested_service=requested_service,
            )
        return _Intent("unsupported_service", requested_service=requested_service)

    if matched_service is not None and _has_service_intent(tokens):
        return _Intent("service_supported", matched_service=matched_service)

    if _contains_unrelated_topic(normalized):
        return _Intent("unrelated", reason="unrelated_topic")

    if _has_business_signal(tokens, context):
        return _Intent("business_context")

    return _Intent("unrelated", reason="no_business_signal")


def _build_instructions(context: BusinessConfig, language: SessionLanguageSnapshot) -> str:
    composed = compose_prompt(PromptContext(business=context, language=language))
    instructions = composed.instructions
    log_event(
        logger,
        "business_instructions_built",
        instruction_chars=len(instructions),
        business_name=context.name,
        services_count=len(context.services),
        faqs_count=len(context.faqs),
    )
    return instructions


def format_business_context(config: BusinessConfig) -> str:
    """Format business configuration into a prompt context block.

    Public interface for agent_v2.py to build instructions from business config.
    """
    return _build_business_prompt(config)


def _build_business_prompt(context: BusinessConfig) -> str:
    memory = business_memory_from_config(context, emit_log=False)
    return render_business_prompt(memory)


def _build_input(transcript: str, language: SessionLanguageSnapshot) -> str:
    return (
        f"Caller language: {language.openai_response_language} "
        f"(confidence {language.confidence:.2f}).\n"
        f"Caller said: {transcript}\n"
        "Receptionist response:"
    )


def _format_services(services: tuple[str, ...]) -> str:
    if not services:
        return "No services configured. Do not claim service availability."
    return "; ".join(services)


def _format_faqs(faqs: tuple[BusinessFAQ, ...]) -> str:
    if not faqs:
        return "No FAQs configured. Do not invent FAQ answers."
    return " ".join(f"Q: {faq.question} A: {faq.answer}" for faq in faqs)


def _render_refusal(
    context: BusinessConfig,
    language: SessionLanguageSnapshot | None = None,
) -> str:
    if language is not None and language.active_language == "hindi":
        return _phone_friendly_text(
            f"Maaf kijiye, main sirf {context.business_type}-related madad kar sakta hoon."
        )
    if language is not None and language.active_language in {"hinglish", "mixed"}:
        return _phone_friendly_text(
            f"Sorry, main sirf {context.business_type}-related help kar sakta hoon."
        )
    behavior = context.refusal_behavior.strip()
    if "{" in behavior and "}" in behavior:
        try:
            return _phone_friendly_text(
                behavior.format(
                    business_name=context.name,
                    business_type=context.business_type,
                    services=_join_naturally(context.services),
                )
            )
        except (KeyError, ValueError):
            pass
    if behavior:
        return _phone_friendly_text(behavior)
    return _phone_friendly_text(
        f"Sorry sir, I can help only with {context.business_type}-related questions."
    )


def _render_unsupported_service(
    context: BusinessConfig,
    requested_service: str | None,
    language: SessionLanguageSnapshot | None = None,
) -> str:
    if not context.services:
        if language is not None and language.active_language in {"hindi", "hinglish", "mixed"}:
            return _phone_friendly_text(
                "Sorry, current service list mere paas abhi nahi hai."
            )
        return _phone_friendly_text(
            "Sorry sir, I do not have the current service list right now."
        )
    service_text = requested_service or "that service"
    if language is not None and language.active_language in {"hindi", "hinglish", "mixed"}:
        return _phone_friendly_text(
            f"Sorry, {service_text} {_place_label(context)} mein available nahi hai."
        )
    return _phone_friendly_text(
        f"Sorry sir, we do not provide {service_text} at {_place_label(context)}."
    )


def _render_supported_service(
    context: BusinessConfig,
    service: str,
    language: SessionLanguageSnapshot | None = None,
) -> str:
    if language is not None and language.active_language in {"hindi", "hinglish", "mixed"}:
        return _phone_friendly_text(
            f"Yes, {_place_label(context)} mein {service} available hai."
        )
    return _phone_friendly_text(
        f"Yes sir, we provide {service} at {_place_label(context)}."
    )


def _render_service_list(
    context: BusinessConfig,
    language: SessionLanguageSnapshot | None = None,
) -> str:
    if not context.services:
        if language is not None and language.active_language in {"hindi", "hinglish", "mixed"}:
            return _phone_friendly_text(
                "Sorry, current service list mere paas abhi nahi hai."
            )
        return _phone_friendly_text(
            "Sorry sir, I do not have the current service list right now."
        )
    if language is not None and language.active_language in {"hindi", "hinglish", "mixed"}:
        return _phone_friendly_text(
            f"Yes, hum {_join_naturally(context.services)} provide karte hain."
        )
    return _phone_friendly_text(f"Yes sir, we provide {_join_naturally(context.services)}.")


def _place_label(context: BusinessConfig) -> str:
    generic_names = {"our clinic", "the clinic", "the business", "your business"}
    if context.name.strip().lower() in generic_names:
        return f"our {context.business_type}"
    return context.name


def _phone_friendly_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _expanded_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for group in _FAQ_SYNONYMS:
        if tokens & group:
            expanded.update(group)
    return expanded


def _extract_requested_service(transcript: str) -> str | None:
    cleaned = _phone_friendly_text(transcript)
    for pattern in _SERVICE_REQUEST_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        service = match.group("service").strip(" .?!,")
        service = re.sub(r"\b(?:at|in|from)\s+(?:your|the|our)\s+\w+$", "", service).strip()
        service = _phone_friendly_text(service)
        if _is_meaningful_service_phrase(service):
            return service.lower()
    return None


def _is_meaningful_service_phrase(service: str) -> bool:
    service_tokens = [token for token in _TOKEN_RE.findall(service.lower()) if token]
    significant = [token for token in service_tokens if token not in _SERVICE_STOPWORDS]
    return bool(significant)


def _is_service_list_question(normalized: str, tokens: set[str]) -> bool:
    if "services" not in tokens and "service" not in tokens:
        return False
    return bool(tokens & {"what", "which", "list", "provide", "offer", "available"})


def _match_faq(
    normalized: str,
    query_tokens: set[str],
    faqs: tuple[BusinessFAQ, ...],
) -> BusinessFAQ | None:
    best_match: BusinessFAQ | None = None
    best_score = 0.0
    for faq in faqs:
        question_norm = _normalize(faq.question)
        question_tokens = _expanded_tokens(_tokens(question_norm))
        significant_question_tokens = _significant_tokens(question_tokens)
        if question_norm and (question_norm in normalized or normalized in question_norm):
            return faq
        if not significant_question_tokens:
            continue
        overlap = len(significant_question_tokens & query_tokens)
        score = overlap / len(significant_question_tokens)
        if overlap >= 2 and score > best_score:
            best_match = faq
            best_score = score
    if best_score >= 0.45:
        return best_match
    return None


def _match_service(
    normalized: str,
    query_tokens: set[str],
    services: tuple[str, ...],
) -> str | None:
    best_service: str | None = None
    best_score = 0.0
    for service in services:
        service_norm = _normalize(service)
        service_tokens = _significant_service_tokens(service)
        if service_norm and service_norm in normalized:
            return service
        if not service_tokens:
            continue
        overlap = len(service_tokens & query_tokens)
        has_distinctive_hit = any(token in query_tokens for token in service_tokens if len(token) >= 5)
        if not has_distinctive_hit and overlap < max(2, len(service_tokens)):
            continue
        score = overlap / len(service_tokens)
        if score > best_score:
            best_score = score
            best_service = service
    if best_score > 0:
        return best_service
    return None


def _significant_service_tokens(service: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(service.lower())
        if token not in _SERVICE_STOPWORDS and len(token) >= 3
    }


def _significant_tokens(tokens: set[str]) -> set[str]:
    return {token for token in tokens if token not in _SERVICE_STOPWORDS and len(token) >= 2}


def _has_service_intent(tokens: set[str]) -> bool:
    return bool(tokens & {"available", "do", "provide", "offer", "need", "want", "treat"})


def _contains_unrelated_topic(normalized: str) -> bool:
    return any(phrase in normalized for phrase in _UNRELATED_TOPIC_PHRASES)


def _has_business_signal(tokens: set[str], context: BusinessConfig) -> bool:
    if tokens & _GREETING_TOKENS:
        return True
    if tokens & _BUSINESS_KEYWORDS:
        return True
    if tokens & _tokens(context.business_type):
        return True
    if tokens & _tokens(context.name):
        return True
    service_tokens = set()
    for service in context.services:
        service_tokens.update(_significant_service_tokens(service))
    if tokens & service_tokens:
        return True
    faq_tokens = set()
    for faq in context.faqs:
        faq_tokens.update(_significant_tokens(_expanded_tokens(_tokens(faq.question))))
    return bool(tokens & faq_tokens)


def _join_naturally(values: tuple[str, ...]) -> str:
    cleaned = [value.strip() for value in values if value.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _dedupe(values: object) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        text = str(value).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped
