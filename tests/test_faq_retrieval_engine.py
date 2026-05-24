from __future__ import annotations

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.retrieval import FAQRetrievalEngine


def test_retrieves_only_relevant_braces_faq() -> None:
    result = FAQRetrievalEngine().retrieve(
        transcript="Do you do braces treatment?",
        business=_business_config(),
    )

    assert result.selected_questions == ("Do you do braces treatment?",)
    assert result.confidence > 0.5
    assert "braces" in result.injection_text.lower()
    assert "parking" not in result.injection_text.lower()
    assert "insurance" not in result.injection_text.lower()


def test_hinglish_braces_query_matches_english_faq() -> None:
    result = FAQRetrievalEngine().retrieve(
        transcript="Daant seedha karne ke liye braces lagate ho?",
        business=_business_config(),
    )

    assert result.selected_questions == ("Do you do braces treatment?",)
    assert result.matches[0].source == "deterministic_keyword_phrase_fuzzy"


def test_multilingual_queries_match_without_embeddings() -> None:
    engine = FAQRetrievalEngine()

    hindi = engine.retrieve(transcript="ब्रेस treatment available hai?", business=_business_config())
    kannada = engine.retrieve(transcript="ಬ್ರೇಸಸ್ ಚಿಕಿತ್ಸೆ ಇದೆಯಾ?", business=_business_config())
    telugu = engine.retrieve(transcript="బ్రేసెస్ treatment ఉందా?", business=_business_config())
    marathi = engine.retrieve(transcript="दात सरळ करण्यासाठी ब्रेस आहेत का?", business=_business_config())

    assert hindi.selected_questions == ("Do you do braces treatment?",)
    assert kannada.selected_questions == ("Do you do braces treatment?",)
    assert telugu.selected_questions == ("Do you do braces treatment?",)
    assert marathi.selected_questions == ("Do you do braces treatment?",)


def test_typo_tolerance_matches_close_business_terms() -> None:
    result = FAQRetrievalEngine().retrieve(
        transcript="Do you do bracess treatmnt?",
        business=_business_config(),
    )

    assert result.selected_questions == ("Do you do braces treatment?",)
    assert result.matches[0].confidence > 0.3


def test_ranking_prefers_exact_intent_over_generic_faq() -> None:
    result = FAQRetrievalEngine(top_k=2).retrieve(
        transcript="What are the braces charges?",
        business=_business_config(),
    )

    assert result.selected_questions[0] == "What are braces charges?"
    assert "Do you do braces treatment?" in result.selected_questions


def test_injection_stays_under_low_token_budget() -> None:
    result = FAQRetrievalEngine(top_k=3, max_injection_chars=400).retrieve(
        transcript="Tell me braces price and appointment timing",
        business=_business_config(),
    )

    assert 0 < result.injected_chars <= 400
    assert len(result.injection_text) == result.injected_chars


def test_no_irrelevant_faq_injection_for_unmatched_question() -> None:
    result = FAQRetrievalEngine().retrieve(
        transcript="Do you sell laptops?",
        business=_business_config(),
    )

    assert result.matches == ()
    assert result.confidence == 0.0
    assert "none matched" in result.injection_text
    assert "braces" not in result.injection_text.lower()


def test_retrieval_latency_target_for_small_faq_set() -> None:
    result = FAQRetrievalEngine().retrieve(
        transcript="What are your opening timings?",
        business=_business_config(),
    )

    assert result.latency_ms < 50
    assert result.selected_questions == ("What are your hours?",)


def test_failure_fallback_keeps_prompt_composable() -> None:
    broken_business = BusinessConfig(
        name="Broken Clinic",
        business_type="clinic",
        services=(),
        faqs=(object(),),  # type: ignore[arg-type]
        receptionist_tone="warm",
        refusal_behavior="Sorry, clinic-only questions.",
        receptionist_personality="calm",
        context_path=None,
    )

    result = FAQRetrievalEngine().retrieve(
        transcript="What are your hours?",
        business=broken_business,
    )

    assert result.matches == ()
    assert result.failure_reason == "AttributeError"
    assert "none matched" in result.injection_text


def _business_config() -> BusinessConfig:
    long_answer = (
        "Braces treatment is available after an orthodontic consultation. "
        "The dentist checks alignment, explains options, and shares the final plan in clinic."
    )
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment", "root canal"),
        faqs=(
            BusinessFAQ(
                question="Do you do braces treatment?",
                answer=long_answer,
            ),
            BusinessFAQ(
                question="What are braces charges?",
                answer="Charges depend on the case and are confirmed after consultation.",
            ),
            BusinessFAQ(
                question="What are your hours?",
                answer="We are open from 10 AM to 7 PM, Monday to Saturday.",
            ),
            BusinessFAQ(
                question="Is parking available?",
                answer="Parking is available behind the clinic building.",
            ),
            BusinessFAQ(
                question="Do you accept insurance?",
                answer="Insurance support depends on the provider and treatment type.",
            ),
        ),
        receptionist_tone="warm and concise",
        refusal_behavior="Sorry sir, I can help only with {business_type}-related questions.",
        receptionist_personality="calm and attentive",
        context_path=None,
    )
