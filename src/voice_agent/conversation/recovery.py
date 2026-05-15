from __future__ import annotations

import re

from voice_agent.language import SessionLanguageSnapshot, default_language_snapshot


def build_interruption_recovery(
    caller_text: str,
    *,
    language: SessionLanguageSnapshot | None = None,
) -> str:
    language = language or default_language_snapshot()
    fragment = _clean_fragment(caller_text)
    if not fragment:
        return _localized(
            language,
            english="No problem. Go ahead.",
            hinglish="No problem. Boliye.",
            hindi="Koi baat nahi. Batayiye.",
        )
    return _localized(
        language,
        english=f"Got it. {fragment}.",
        hinglish=f"Got it. {fragment}.",
        hindi=f"Theek hai. {fragment}.",
    )


def _clean_fragment(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text.strip(" .?!,\t\r\n"))
    if len(cleaned) > 80:
        cleaned = cleaned[:80].rsplit(" ", 1)[0].strip()
    return cleaned


def _localized(
    language: SessionLanguageSnapshot,
    *,
    english: str,
    hinglish: str,
    hindi: str,
) -> str:
    if language.active_language == "hindi":
        return hindi
    if language.active_language in {"hinglish", "mixed"}:
        return hinglish
    return english
