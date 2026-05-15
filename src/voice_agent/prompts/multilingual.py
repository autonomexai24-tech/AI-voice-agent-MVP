from __future__ import annotations

from voice_agent.language import SessionLanguageSnapshot, build_openai_language_instruction


def multilingual_prompt(language: SessionLanguageSnapshot | None = None) -> str:
    if language is None:
        language_line = (
            "Respond in the same language the caller uses. If unsure, use natural Indian English."
        )
    else:
        language_line = build_openai_language_instruction(language)

    return (
        "Language behavior:\n"
        f"- {language_line}\n"
        "- If the caller speaks Hindi, respond in Hindi.\n"
        "- If the caller uses Hinglish, respond in natural Hinglish.\n"
        "- Preserve code-mixed style naturally; do not force a formal translation.\n"
        "- Keep words simple for Indian telecom audio."
    )
