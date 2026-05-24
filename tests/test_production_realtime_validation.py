from __future__ import annotations

import asyncio
import json

from voice_agent.language import detect_language
from voice_agent.validation import (
    REQUIRED_OBSERVABILITY_SIGNALS,
    RealtimeValidationRecorder,
    build_real_world_validation_checklist,
    load_jsonl_events,
    run_local_validation_probe,
    validate_events,
)


def test_log_validation_requires_latency_targets_and_observability_signals() -> None:
    events = (
        {
            "event": "runtime_latency_summary",
            "response_latency": 1800.0,
            "orchestration_latency": 42.0,
            "cache_hits": 3,
            "cache_misses": 1,
            "compression_ratio": 0.42,
            "memory_growth": 12,
        },
        {
            "event": "interruption_recovery_completed",
            "interruption_recovery_ms": 110.0,
        },
        {
            "event": "escalation_trigger",
            "escalation_events": 1,
        },
        {
            "event": "queue_wait_time",
            "queue_wait_time": 250.0,
        },
    )

    report = validate_events(events)

    assert report.passed is True
    assert {item.signal for item in report.observability if item.observed} == set(
        REQUIRED_OBSERVABILITY_SIGNALS
    )
    assert report.counters["interruption_recovery_events"] == 1
    assert report.counters["escalation_events"] == 1


def test_log_validation_fails_when_real_latency_exceeds_target() -> None:
    events = (
        {
            "event": "runtime_latency_summary",
            "response_latency": 3100.0,
            "orchestration_latency": 42.0,
            "cache_hits": 1,
            "cache_misses": 1,
            "compression_ratio": 0.8,
            "memory_growth": 4,
        },
        {"event": "interruption_recovery_completed", "interruption_recovery_ms": 120.0},
        {"event": "escalation_trigger", "escalation_events": 1},
        {"event": "queue_wait_time", "queue_wait_time": 0.0},
    )

    report = validate_events(events)

    assert report.passed is False
    assert any("total_response" in failure for failure in report.failures)


def test_recorder_produces_phase2g_report_with_resource_samples() -> None:
    recorder = RealtimeValidationRecorder(name="unit-recorder")
    recorder.record_latency("total_response", 1200.0, response_latency=1200.0)
    recorder.record_latency("orchestration", 30.0, orchestration_latency=30.0)
    recorder.record_event(
        "runtime_metrics",
        cache_hits=1,
        cache_misses=0,
        compression_ratio=0.5,
        memory_growth=3,
    )
    recorder.record_event("interruption_recovery_completed", interruption_recovery_ms=90.0)
    recorder.record_event("escalation_trigger", escalation_events=1)
    recorder.record_event("queue_wait_time", queue_wait_time=0.0)
    recorder.record_resource_sample(
        cpu_process_percent=14.0,
        ram_current_mb=64.0,
        ram_peak_mb=80.0,
        event_loop_lag_ms=4.0,
        active_calls=5,
    )

    report = recorder.validate()

    assert report.passed is True
    assert report.counters["events"] >= 6
    assert any(item.component == "event_loop_lag" for item in report.latency)


def test_jsonl_log_loader_parses_runtime_events(tmp_path) -> None:
    log_file = tmp_path / "runtime.jsonl"
    log_file.write_text(
        "\n".join(
            json.dumps(payload)
            for payload in (
                {"event": "runtime_latency_summary", "response_latency": 1200.0},
                {"event": "livekit_audio_stream_closed", "frame_count": 120},
            )
        ),
        encoding="utf-8",
    )

    events = load_jsonl_events(log_file)

    assert len(events) == 2
    assert events[1]["event"] == "livekit_audio_stream_closed"


def test_local_probe_exercises_five_calls_two_humans_and_continuity() -> None:
    report = asyncio.run(
        run_local_validation_probe(
            concurrent_calls=5,
            human_operators=2,
            turns_per_call=4,
        )
    )

    assert report.observability
    assert report.counters["events"] > 0
    assert report.counters["escalation_events"] >= 1
    assert report.counters["interruption_recovery_events"] >= 1


def test_phase2g_checklist_names_real_sip_livekit_and_vps_evidence() -> None:
    checklist = build_real_world_validation_checklist()
    markdown = checklist.to_markdown()

    assert "inbound SIP calls" in markdown
    assert "LiveKit room logs" in markdown
    assert "EasyPanel VPS CPU" in markdown
    assert "5 simultaneous AI calls" in markdown


def test_marathi_is_available_for_multilingual_validation() -> None:
    detected = detect_language("mala appointment pahije marathi madhe sanga")

    assert detected.language == "marathi"
    assert detected.sarvam_language_code == "mr-IN"
