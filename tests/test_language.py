from __future__ import annotations

import asyncio

from voice_agent.language import (
    SessionLanguageRouter,
    build_openai_language_instruction,
    detect_language,
)


def test_detect_language_identifies_supported_indic_scripts() -> None:
    cases = [
        ("\u0928\u092e\u0938\u094d\u0924\u0947", "hindi", "hi-IN"),
        ("\u0ca8\u0cae\u0cb8\u0ccd\u0c95\u0cbe\u0cb0", "kannada", "kn-IN"),
        ("\u0bb5\u0ba3\u0b95\u0bcd\u0b95\u0bae\u0bcd", "tamil", "ta-IN"),
        ("\u0c28\u0c2e\u0c38\u0c4d\u0c24\u0c47", "telugu", "te-IN"),
        ("\u0d28\u0d2e\u0d38\u0d4d\u0d15\u0d3e\u0d30\u0d02", "malayalam", "ml-IN"),
        ("\u09a8\u09ae\u09b8\u09cd\u0995\u09be\u09b0", "bengali", "bn-IN"),
    ]

    for text, language, sarvam_code in cases:
        detection = detect_language(text)
        assert detection.language == language
        assert detection.sarvam_language_code == sarvam_code
        assert detection.confidence >= 0.9


def test_detect_language_identifies_hinglish_and_mixed_language() -> None:
    hinglish = detect_language("haan ji mujhe appointment chahiye please")
    mixed = detect_language("appointment \u092e\u0926\u0926 \u091a\u093e\u0939\u093f\u090f")

    assert hinglish.language == "hinglish"
    assert hinglish.dominant_language == "hindi"
    assert hinglish.sarvam_language_code == "hi-IN"
    assert mixed.language == "mixed"
    assert mixed.dominant_language == "hindi"
    assert mixed.sarvam_language_code == "hi-IN"


def test_session_language_router_tracks_transition_state() -> None:
    asyncio.run(_run_router_transition_test())


async def _run_router_transition_test() -> None:
    router = SessionLanguageRouter(initial_language="english", default_speaker="meera")

    first = await router.route_text("Can you help me?", request_id="req-en", is_final=True)
    second = await router.route_text(
        "\u0ba8\u0bbe\u0ba9\u0bcd \u0b89\u0ba4\u0bb5\u0bbf \u0bb5\u0bc7\u0ba3\u0bc1\u0bae\u0bcd",
        request_id="req-ta",
        is_final=True,
    )

    assert first.switched is False
    assert second.switched is True
    assert second.snapshot.active_language == "tamil"
    assert second.snapshot.previous_language == "english"
    assert second.snapshot.sarvam_language_code == "ta-IN"
    assert second.snapshot.speaker == "meera"
    assert second.snapshot.generation == 1
    assert "Tamil" in build_openai_language_instruction(second.snapshot)
