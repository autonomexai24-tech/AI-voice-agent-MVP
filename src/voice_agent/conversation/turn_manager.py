from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


@dataclass
class ResponseRepetitionGuard:
    max_recent_responses: int = 8
    _recent: deque[str] = field(default_factory=deque, init=False)
    _recent_confirmations: deque[str] = field(default_factory=deque, init=False)
    _recent_recovery: deque[str] = field(default_factory=deque, init=False)
    _recent_silence: deque[str] = field(default_factory=deque, init=False)

    def should_emit(
        self,
        text: str,
        *,
        request_id: str | None = None,
        response_id: str | None = None,
        category: str = "response",
    ) -> bool:
        normalized = _normalize(text)
        if not normalized:
            return False

        is_confirmation = _looks_like_confirmation(normalized)
        category_recent = self._category_recent(category)
        duplicate = normalized in self._recent or (
            is_confirmation and normalized in self._recent_confirmations
        ) or (
            category_recent is not None and normalized in category_recent
        )
        if duplicate:
            details = {
                "request_id": request_id,
                "response_id": response_id,
                "response_chars": len(text),
                "confirmation_like": is_confirmation,
                "category": category,
            }
            log_event(
                logger,
                "repetition_prevented",
                **details,
            )
            log_event(
                logger,
                "duplicate_response_blocked",
                **details,
            )
            return False

        self._recent.append(normalized)
        while len(self._recent) > self.max_recent_responses:
            self._recent.popleft()
        if is_confirmation:
            self._recent_confirmations.append(normalized)
            while len(self._recent_confirmations) > self.max_recent_responses:
                self._recent_confirmations.popleft()
        if category_recent is not None:
            category_recent.append(normalized)
            while len(category_recent) > self.max_recent_responses:
                category_recent.popleft()
        return True

    def _category_recent(self, category: str) -> deque[str] | None:
        if category == "recovery":
            return self._recent_recovery
        if category == "silence":
            return self._recent_silence
        return None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _looks_like_confirmation(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in (
            "confirmed",
            "details noted",
            "i have the details",
            "note kar li",
            "slot shortly",
        )
    )
