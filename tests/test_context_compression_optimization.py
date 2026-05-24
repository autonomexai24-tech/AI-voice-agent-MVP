from __future__ import annotations

import asyncio

from dataclasses import replace

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.language import default_language_snapshot
from voice_agent.optimization import (
    MemoryPruningPolicy,
    PromptCacheLayer,
    RetrievalCache,
    RollingMemoryCompressor,
    RuntimeLatencyOptimizer,
    RuntimeLatencyProfiler,
)
from voice_agent.orchestration import ConversationOrchestrator
from voice_agent.realtime_prompt_manager import PromptIntent, RealtimePromptManager
from voice_agent.retrieval import FAQRetrievalEngine
from voice_agent.session_memory import CallSessionMemory


def test_rolling_compression_preserves_critical_runtime_state() -> None:
    memory = CallSessionMemory(session_id="compress-critical")
    memory.capture_booking_fields(
        customer_name="Ravi",
        phone_number="9876543210",
        service_type="braces treatment",
    )
    memory.mark_escalation(reason="caller requested human operator")
    memory.runtime_memory.mark_unresolved_question(field_name="appointment_time")
    for index in range(14):
        memory.record_turn(
            role="caller",
            text=f"Long greeting and small talk that should compress away {index}",
        )

    result = RollingMemoryCompressor(
        compress_after_turns=6,
        keep_recent_turns=3,
    ).compress(memory)

    injection = memory.runtime_memory.build_injection()
    assert result.pruned_turns > 0
    assert result.compression_ratio < 1
    assert "booking_fields" in result.preserved_runtime_fields
    assert "escalation_state" in result.preserved_runtime_fields
    assert "unresolved_issues" in result.preserved_runtime_fields
    assert "phone_number=9876543210" in injection
    assert "escalation: triggered:caller requested human operator" in injection
    assert len(memory.recent_turns) == 3


def test_memory_pruning_removes_stale_greetings_and_interruptions() -> None:
    memory = CallSessionMemory(session_id="prune-noise")
    memory.recent_turns = [
        {"role": "caller", "text": "hello"},
        {"role": "caller", "text": "um"},
        {"role": "caller", "text": "wait"},
        {"role": "caller", "text": "hold on"},
        {"role": "caller", "text": "stop"},
        {"role": "caller", "text": "I need braces charges"},
    ]

    pruned = MemoryPruningPolicy(
        max_recent_turns=4,
        max_interruption_turns=1,
    ).prune(memory)

    texts = [turn["text"] for turn in memory.recent_turns]
    assert pruned >= 3
    assert "hello" not in texts
    assert "um" not in texts
    assert sum(text in {"wait", "hold on", "stop"} for text in texts) == 1
    assert "I need braces charges" in texts


def test_prompt_composition_stays_in_low_token_budget_on_long_call() -> None:
    memory = CallSessionMemory(session_id="long-low-token")
    memory.capture_booking_fields(
        customer_name="Ravi",
        phone_number="9876543210",
        service_type="braces treatment",
        appointment_date="tomorrow",
    )
    for index in range(60):
        memory.record_turn(role="caller", text=f"Old conversational detail {index}")
        memory.record_turn(role="assistant", text="What time would you prefer?")

    prompt = RealtimePromptManager(max_prompt_chars=1200).compose(
        transcript="make it evening, actually Hindi mein batao",
        business=_business_config(),
        memory=memory,
        language=replace(
            default_language_snapshot(),
            active_language="hinglish",
            dominant_language="hindi",
            previous_language="english",
            openai_response_language="Hinglish",
            generation=3,
        ),
        intent=PromptIntent(classification="booking_correction"),
    )

    assert 500 <= prompt.prompt_chars <= 1200
    assert prompt.memory_chars <= 760
    assert prompt.memory_pruned > 0
    assert prompt.compression_ratio < 1
    assert "9876543210" in prompt.instructions
    assert "date=tomorrow" in prompt.instructions or "appointment_date=tomorrow" in prompt.instructions
    assert "Respond in natural Hinglish" in prompt.instructions
    assert "Old conversational detail 1" not in prompt.instructions


def test_prompt_and_retrieval_caches_reuse_stable_fragments() -> None:
    prompt_cache = PromptCacheLayer()
    business = _business_config()
    assert prompt_cache.business_fragment(business, business.services) == prompt_cache.business_fragment(
        business,
        business.services,
    )
    assert prompt_cache.stats().hits >= 1

    retrieval_cache = RetrievalCache()
    engine = FAQRetrievalEngine(retrieval_cache=retrieval_cache)
    first = engine.retrieve(transcript="What are braces charges?", business=business)
    second = engine.retrieve(transcript="What are braces charges?", business=business)

    assert first.selected_questions == ("What are braces charges?",)
    assert second.selected_questions == first.selected_questions
    assert second.source.endswith(":cache_hit")
    assert second.latency_ms == 0.0
    assert retrieval_cache.stats().hits >= 1


def test_latency_profiler_records_targets_and_summary_fields() -> None:
    profiler = RuntimeLatencyProfiler()
    profiler.record("retrieval", 12.5)
    profiler.record("orchestration", 42.0)
    profiler.record("memory_assembly", 21.0)
    profiler.record("gpt_response_start", 700.0)
    profiler.record("tts_start", 220.0)
    profiler.record("total_response", 1300.0)

    snapshot = profiler.log_summary(
        prompt_size=880,
        compression_ratio=0.42,
        memory_pruned=12,
        cache_hits=3,
        cache_misses=1,
    )

    assert snapshot.total_response_time == 1300.0
    assert snapshot.component_latencies["retrieval"] == 12.5
    assert snapshot.over_target_components == ()


def test_interruption_path_stays_runtime_first_and_low_latency() -> None:
    asyncio.run(_run_interruption_optimization_test())


async def _run_interruption_optimization_test() -> None:
    memory = CallSessionMemory(session_id="interrupt-optimized")
    memory.capture_booking_fields(customer_name="Ravi", service_type="root canal")
    orchestrator = ConversationOrchestrator(_business_config(), session_id="interrupt-optimized")

    decision = await orchestrator.handle_turn(
        "wait one second",
        memory=memory,
        language=default_language_snapshot(),
        interruption=True,
    )

    assert decision.route.value == "interruption"
    assert decision.should_call_model is True
    assert decision.latency_ms < 80
    assert memory.booking_values()


def test_runtime_optimizer_is_concurrency_ready_for_five_calls() -> None:
    asyncio.run(_run_concurrency_test())


async def _run_concurrency_test() -> None:
    optimizer = RuntimeLatencyOptimizer()
    manager = RealtimePromptManager(
        prompt_cache=optimizer.prompt_cache,
        retrieval_cache=optimizer.retrieval_cache,
        latency_profiler=optimizer.profiler,
    )

    async def compose(index: int) -> int:
        memory = CallSessionMemory(session_id=f"call-{index}")
        memory.capture_booking_fields(customer_name=f"Caller {index}")
        prompt = await asyncio.to_thread(
            manager.compose,
            transcript="What are your hours and can I book braces?",
            business=_business_config(),
            memory=memory,
            language=default_language_snapshot(),
            intent=PromptIntent(classification="booking"),
        )
        return prompt.prompt_chars

    sizes = await asyncio.gather(*(compose(index) for index in range(5)))

    assert len(sizes) == 5
    assert all(500 <= size <= 1200 for size in sizes)
    assert optimizer.prompt_cache.stats().hits > 0


def test_streaming_first_hints_prioritize_runtime_routes() -> None:
    optimizer = RuntimeLatencyOptimizer()

    assert optimizer.should_start_streaming_early(route="interruption", prompt_size=1500)
    assert optimizer.should_start_streaming_early(route="faq", prompt_size=900)
    assert optimizer.should_start_streaming_early(route="service_discovery", prompt_size=900)
    assert not optimizer.should_start_streaming_early(route="service_discovery", prompt_size=1600)


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment", "root canal"),
        faqs=(
            BusinessFAQ(
                question="What are braces charges?",
                answer="Charges depend on the case and are confirmed after consultation.",
            ),
            BusinessFAQ(
                question="What are your hours?",
                answer="We are open from 10 AM to 7 PM, Monday to Saturday.",
            ),
        ),
        receptionist_tone="warm and concise",
        refusal_behavior="Sorry, clinic questions only.",
        receptionist_personality="calm and attentive",
        context_path=None,
    )
