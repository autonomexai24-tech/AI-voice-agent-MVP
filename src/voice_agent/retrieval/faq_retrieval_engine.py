from __future__ import annotations

import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u0900-\u097f]+|[\u0c80-\u0cff]+|[\u0c00-\u0c7f]+")
_SPACE_RE = re.compile(r"\s+")

_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "at",
    "available",
    "can",
    "do",
    "does",
    "for",
    "from",
    "have",
    "help",
    "hi",
    "hello",
    "i",
    "in",
    "is",
    "it",
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
    "hai",
    "hain",
    "he",
    "ka",
    "ki",
    "ke",
    "ko",
    "main",
    "mein",
    "mujhe",
    "kya",
    "aap",
    "aur",
}

_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    frozenset(
        {
            "hour",
            "hours",
            "open",
            "opening",
            "close",
            "closing",
            "time",
            "timing",
            "timings",
            "samay",
            "waqt",
            "kab",
            "khula",
            "band",
            "समय",
            "टाइम",
            "कब",
            "वेळ",
            "कितीवेळ",
            "ಸಮಯ",
            "ಯಾವಾಗ",
            "టైమ్",
            "సమయం",
            "ఎప్పుడు",
        }
    ),
    frozenset(
        {
            "address",
            "location",
            "located",
            "where",
            "kaha",
            "kidhar",
            "pata",
            "पता",
            "कहाँ",
            "कहा",
            "कुठे",
            "पत्ता",
            "ವಿಳಾಸ",
            "ಎಲ್ಲಿ",
            "చిరునామా",
            "ఎక్కడ",
        }
    ),
    frozenset(
        {
            "cost",
            "charge",
            "charges",
            "fee",
            "fees",
            "price",
            "pricing",
            "kitna",
            "paisa",
            "daam",
            "kharcha",
            "कितना",
            "किती",
            "खर्च",
            "शुल्क",
            "फीस",
            "ದರ",
            "ಶುಲ್ಕ",
            "ఎంత",
            "ఖర్చు",
            "ఫీజు",
        }
    ),
    frozenset(
        {
            "appointment",
            "book",
            "booking",
            "schedule",
            "slot",
            "visit",
            "milna",
            "chahiye",
            "अपॉइंटमेंट",
            "बुक",
            "भेट",
            "ಅಪಾಯಿಂಟ್ಮೆಂಟ್",
            "ಬುಕ್",
            "అపాయింట్మెంట్",
            "బుక్",
        }
    ),
    frozenset(
        {
            "braces",
            "brace",
            "orthodontic",
            "orthodontics",
            "alignment",
            "aligners",
            "daant",
            "dant",
            "teeth",
            "tooth",
            "ब्रेस",
            "दांत",
            "दात",
            "हल्लು",
            "ಹಲ್ಲು",
            "ಬ್ರೇಸಸ್",
            "బ్రేసెస్",
            "పళ్ళు",
        }
    ),
    frozenset(
        {
            "cleaning",
            "clean",
            "safai",
            "scaling",
            "स्वच्छता",
            "सफाई",
            "स्वच्छ",
            "ಸ್ವಚ್ಛ",
            "క్లీనింగ్",
            "శుభ్రం",
        }
    ),
    frozenset({"root", "canal", "rct", "रूट", "कॅनल", "ರೂಟ್", "కాలువ"}),
    frozenset(
        {
            "doctor",
            "dentist",
            "dr",
            "डॉक्टर",
            "डाक्टर",
            "दंतचिकित्सक",
            "ವೈದ್ಯ",
            "డాక్టర్",
        }
    ),
    frozenset(
        {
            "parking",
            "park",
            "vehicle",
            "gaadi",
            "car",
            "पार्किंग",
            "गाडी",
            "पार्क",
            "ಪಾರ್ಕಿಂಗ್",
            "పార్కింగ్",
        }
    ),
    frozenset({"insurance", "insured", "cashless", "बीमा", "विमा", "ಇನ್ಶುರನ್ಸ್", "ఇన్సూరెన్స్"}),
)

_PHRASE_CONCEPTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hours", ("kitne baje", "kab khula", "kab band", "opening time", "closing time")),
    ("braces", ("teeth alignment", "daant seedha", "dant seedha", "दांत सीधे", "दात सरळ")),
    ("appointment", ("appointment chahiye", "book karna", "slot chahiye", "visit karna")),
    ("address", ("clinic kaha", "clinic kidhar", "where is clinic")),
    ("price", ("kitna lagega", "kya charge", "how much")),
)

_RETRIEVAL_SOURCE = "deterministic_keyword_phrase_fuzzy"
_INTENT_CONCEPTS = {"concept_0", "concept_1", "concept_2", "concept_3", "concept_8", "concept_9"}


@dataclass(frozen=True)
class FAQRetrievalMatch:
    faq: BusinessFAQ
    score: float
    confidence: float
    matched_terms: tuple[str, ...]
    source: str
    index: int


@dataclass(frozen=True)
class FAQRetrievalResult:
    matches: tuple[FAQRetrievalMatch, ...]
    confidence: float
    latency_ms: float
    injected_chars: int
    injection_text: str
    source: str
    failure_reason: str | None = None

    @property
    def faqs(self) -> tuple[BusinessFAQ, ...]:
        return tuple(match.faq for match in self.matches)

    @property
    def selected_questions(self) -> tuple[str, ...]:
        return tuple(match.faq.question for match in self.matches)


class FAQRetrievalEngine:
    """Low-latency deterministic FAQ retriever for per-turn grounding."""

    def __init__(
        self,
        *,
        top_k: int = 3,
        min_score: float = 1.15,
        max_injection_chars: int = 400,
        max_answer_chars: int = 150,
        clock: Callable[[], float] = time.perf_counter,
        retrieval_cache: Any | None = None,
    ) -> None:
        self._top_k = max(1, top_k)
        self._min_score = min_score
        self._max_injection_chars = max(120, max_injection_chars)
        self._max_answer_chars = max(60, max_answer_chars)
        self._clock = clock
        self._retrieval_cache = retrieval_cache

    def retrieve(
        self,
        *,
        transcript: str,
        business: BusinessConfig,
        intent_terms: Iterable[str] = (),
        request_id: str | None = None,
    ) -> FAQRetrievalResult:
        started_at = self._clock()
        try:
            normalized_intent_terms = tuple(intent_terms)
            if self._retrieval_cache is not None:
                result = self._retrieval_cache.get_or_compute(
                    transcript=transcript,
                    business=business,
                    intent_terms=normalized_intent_terms,
                    request_id=request_id,
                    compute=lambda: self._retrieve(
                        transcript=transcript,
                        business=business,
                        intent_terms=normalized_intent_terms,
                        started_at=started_at,
                    ),
                )
            else:
                result = self._retrieve(
                    transcript=transcript,
                    business=business,
                    intent_terms=normalized_intent_terms,
                    started_at=started_at,
                )
            log_event(
                logger,
                "faq_retrieval_completed",
                request_id=request_id,
                source=result.source,
                matched_faq_count=len(result.matches),
                confidence=result.confidence,
                injected_faq_chars=result.injected_chars,
                retrieval_latency_ms=result.latency_ms,
                retrieval_latency=result.latency_ms,
                selected_faq_questions=list(result.selected_questions),
            )
            return result
        except Exception as exc:
            latency_ms = round((self._clock() - started_at) * 1000, 3)
            fallback = _empty_result(
                latency_ms=latency_ms,
                failure_reason=type(exc).__name__,
            )
            log_event(
                logger,
                "faq_retrieval_failed",
                request_id=request_id,
                source=_RETRIEVAL_SOURCE,
                error_type=type(exc).__name__,
                matched_faq_count=0,
                confidence=0.0,
                injected_faq_chars=fallback.injected_chars,
                retrieval_latency_ms=latency_ms,
            )
            return fallback

    def _retrieve(
        self,
        *,
        transcript: str,
        business: BusinessConfig,
        intent_terms: Iterable[str],
        started_at: float,
    ) -> FAQRetrievalResult:
        query = _build_query_terms((transcript, *intent_terms))
        if not query.significant_terms or not business.faqs:
            latency_ms = round((self._clock() - started_at) * 1000, 3)
            return _empty_result(latency_ms=latency_ms)

        scored: list[FAQRetrievalMatch] = []
        for index, faq in enumerate(business.faqs):
            question = _build_query_terms((faq.question,))
            answer = _build_query_terms((faq.answer,))
            question_overlap = query.significant_terms & question.significant_terms
            answer_overlap = query.significant_terms & answer.significant_terms
            fuzzy_terms = _fuzzy_overlap(query.significant_terms, question.significant_terms)
            phrase_bonus = _phrase_bonus(query.normalized_text, question.normalized_text)
            query_concepts = _concept_terms(query.significant_terms)
            question_concepts = _concept_terms(question.significant_terms)
            missing_intent_concepts = (question_concepts & _INTENT_CONCEPTS) - query_concepts
            if missing_intent_concepts:
                continue
            exact = bool(
                question.normalized_text
                and (
                    question.normalized_text in query.normalized_text
                    or query.normalized_text in question.normalized_text
                )
            )

            score = (
                len(question_overlap) * 1.8
                + len(answer_overlap) * 0.55
                + len(fuzzy_terms) * 0.8
                + phrase_bonus
            )
            if question.normalized_text and question.normalized_text in query.normalized_text:
                score += 2.0
            if query.normalized_text and query.normalized_text in question.normalized_text:
                score += 1.5

            matched_terms = tuple(sorted(question_overlap | answer_overlap | fuzzy_terms))
            question_side_matches = question_overlap | fuzzy_terms
            if not exact and not phrase_bonus:
                if len(question_side_matches) < 2:
                    continue
            if score < self._min_score or not matched_terms:
                continue

            confidence = _confidence(score, len(question.significant_terms), phrase_bonus)
            scored.append(
                FAQRetrievalMatch(
                    faq=faq,
                    score=round(score, 3),
                    confidence=confidence,
                    matched_terms=matched_terms[:8],
                    source=_RETRIEVAL_SOURCE,
                    index=index,
                )
            )

        scored.sort(key=lambda match: (-match.score, -match.confidence, match.index))
        matches = tuple(scored[: self._top_k])
        injection_text = self._build_injection(matches)
        latency_ms = round((self._clock() - started_at) * 1000, 3)
        confidence = matches[0].confidence if matches else 0.0
        return FAQRetrievalResult(
            matches=matches,
            confidence=confidence,
            latency_ms=latency_ms,
            injected_chars=len(injection_text),
            injection_text=injection_text,
            source=_RETRIEVAL_SOURCE,
        )

    def _build_injection(self, matches: tuple[FAQRetrievalMatch, ...]) -> str:
        if not matches:
            return "Relevant FAQs: none matched; do not invent FAQ answers."

        lines = ["Relevant FAQs:"]
        for match in matches:
            prefix = "- Q: "
            question = _clean(match.faq.question)
            answer = _truncate(_clean(match.faq.answer), self._max_answer_chars)
            line = f"{prefix}{question} A: {answer}"
            candidate = "\n".join((*lines, line))
            if len(candidate) <= self._max_injection_chars:
                lines.append(line)
                continue

            remaining = self._max_injection_chars - len("\n".join(lines)) - len(prefix) - len(question) - 8
            if remaining >= 45:
                lines.append(f"{prefix}{question} A: {_truncate(_clean(match.faq.answer), remaining)}")
            break
        return "\n".join(lines)


@dataclass(frozen=True)
class _QueryTerms:
    normalized_text: str
    significant_terms: frozenset[str]


def _build_query_terms(parts: Iterable[str]) -> _QueryTerms:
    normalized_parts = [_normalize(part) for part in parts if part and part.strip()]
    normalized = " ".join(part for part in normalized_parts if part)
    raw_tokens = set(_TOKEN_RE.findall(normalized))
    raw_tokens.update(_phrase_concepts(normalized))
    expanded = _expand_tokens(raw_tokens)
    significant = frozenset(token for token in expanded if _is_significant(token))
    return _QueryTerms(normalized_text=normalized, significant_terms=significant)


def _expand_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for index, group in enumerate(_SYNONYM_GROUPS):
        if expanded & group:
            expanded.add(f"concept_{index}")
            expanded.update(group)
    return expanded


def _concept_terms(tokens: frozenset[str]) -> set[str]:
    return {token for token in tokens if token.startswith("concept_")}


def _phrase_concepts(normalized: str) -> set[str]:
    concepts: set[str] = set()
    for concept, phrases in _PHRASE_CONCEPTS:
        if any(phrase in normalized for phrase in phrases):
            concepts.add(concept)
    return concepts


def _fuzzy_overlap(query_terms: frozenset[str], faq_terms: frozenset[str]) -> set[str]:
    fuzzy: set[str] = set()
    faq_candidates = [term for term in faq_terms if len(term) >= 5 and term.isascii()]
    if not faq_candidates:
        return fuzzy
    for query_term in query_terms:
        if len(query_term) < 5 or not query_term.isascii() or query_term in faq_terms:
            continue
        for faq_term in faq_candidates:
            if abs(len(query_term) - len(faq_term)) > 2:
                continue
            if _near_match(query_term, faq_term):
                fuzzy.add(faq_term)
                break
    return fuzzy


def _near_match(left: str, right: str) -> bool:
    if left == right:
        return True
    if _bounded_edit_distance(left, right, max_distance=1 if max(len(left), len(right)) <= 7 else 2):
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.86


def _bounded_edit_distance(left: str, right: str, *, max_distance: int) -> bool:
    if abs(len(left) - len(right)) > max_distance:
        return False
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        row_min = current[0]
        for j, right_char in enumerate(right, start=1):
            cost = 0 if left_char == right_char else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > max_distance:
            return False
        previous = current
    return previous[-1] <= max_distance


def _phrase_bonus(query_text: str, faq_question_text: str) -> float:
    if not query_text or not faq_question_text:
        return 0.0
    if faq_question_text in query_text or query_text in faq_question_text:
        return 2.0
    return 0.0


def _confidence(score: float, question_term_count: int, phrase_bonus: float) -> float:
    denominator = max(2.5, min(5.0, question_term_count * 0.7))
    value = min(1.0, score / denominator)
    if phrase_bonus:
        value = min(1.0, value + 0.12)
    return round(value, 3)


def _empty_result(*, latency_ms: float, failure_reason: str | None = None) -> FAQRetrievalResult:
    injection = "Relevant FAQs: none matched; do not invent FAQ answers."
    return FAQRetrievalResult(
        matches=(),
        confidence=0.0,
        latency_ms=latency_ms,
        injected_chars=len(injection),
        injection_text=injection,
        source=_RETRIEVAL_SOURCE,
        failure_reason=failure_reason,
    )


def _normalize(text: str) -> str:
    return _SPACE_RE.sub(" ", text.strip().lower())


def _clean(text: str) -> str:
    return _SPACE_RE.sub(" ", text.strip())


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _is_significant(token: str) -> bool:
    return len(token) >= 2 and token not in _STOPWORDS
