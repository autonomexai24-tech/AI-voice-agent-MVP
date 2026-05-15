from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from voice_agent.conversation.states import ConversationState, PendingAction
from voice_agent.conversation.turn_manager import ResponseRepetitionGuard
from voice_agent.language import SessionLanguageSnapshot, default_language_snapshot
from voice_agent.logging_config import get_logger, log_event

logger = get_logger(__name__)


@dataclass
class SilenceHandler:
    silence_seconds: float = 8.0
    max_prompts: int = 2
    clock: Callable[[], float] = time.perf_counter

    def __post_init__(self) -> None:
        if self.silence_seconds <= 0:
            raise ValueError("silence_seconds must be positive")
        if self.max_prompts < 1:
            raise ValueError("max_prompts must be >= 1")
        self._last_activity_at = self.clock()
        self._last_prompt_at = 0.0
        self._prompt_count = 0

    def mark_activity(self) -> None:
        self._last_activity_at = self.clock()

    def maybe_prompt(
        self,
        *,
        state: ConversationState,
        language: SessionLanguageSnapshot | None = None,
        request_id: str | None = None,
        caller_speaking: bool = False,
        ai_speaking: bool = False,
        repetition_guard: ResponseRepetitionGuard | None = None,
    ) -> str | None:
        now = self.clock()
        if caller_speaking or ai_speaking:
            self._last_activity_at = now
            log_event(
                logger,
                "state_guard_triggered",
                request_id=request_id,
                guard_name="silence_prompt_blocked_active_speech",
                caller_speaking=caller_speaking,
                ai_speaking=ai_speaking,
                state=state.value,
            )
            return None
        if state != ConversationState.LISTENING:
            self._last_activity_at = now
            return None
        if self._prompt_count >= self.max_prompts:
            return None
        elapsed = now - self._last_activity_at
        if elapsed < self.silence_seconds:
            return None
        if self._last_prompt_at and now - self._last_prompt_at < self.silence_seconds:
            return None

        self._prompt_count += 1
        self._last_prompt_at = now
        self._last_activity_at = now
        language = language or default_language_snapshot()
        prompt = _silence_prompt(language, self._prompt_count)
        if repetition_guard and not repetition_guard.should_emit(
            prompt,
            request_id=request_id,
            category="silence",
        ):
            return None
        log_event(
            logger,
            "silence_detected",
            request_id=request_id,
            elapsed_ms=round(elapsed * 1000),
            prompt_count=self._prompt_count,
            pending_action=PendingAction.SILENCE_REPROMPT.value,
            active_language=language.active_language,
        )
        log_event(
            logger,
            "silence_recovery_started",
            request_id=request_id,
            prompt_count=self._prompt_count,
            active_language=language.active_language,
        )
        log_event(
            logger,
            "silence_recovery_completed",
            request_id=request_id,
            prompt_count=self._prompt_count,
            active_language=language.active_language,
        )
        return prompt


def _silence_prompt(language: SessionLanguageSnapshot, prompt_count: int) -> str:
    if language.active_language == "hindi":
        return "Main yahin hoon. Aap jab ready hon."
    if language.active_language in {"hinglish", "mixed"}:
        return "Main yahin hoon. Jab ready ho, bataiye."
    if prompt_count == 1:
        return "Take your time."
    return "I'm here. Whenever you're ready."
