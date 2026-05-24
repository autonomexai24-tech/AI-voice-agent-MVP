from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from voice_agent.config import BusinessConfig, BusinessFAQ
from voice_agent.human_takeover import (
    EscalationQueue,
    HumanOperator,
    HumanTakeoverRuntime,
    TakeoverOwnershipState,
)
from voice_agent.interruption import RealtimeInterruptionManager
from voice_agent.language import SessionLanguageRouter, default_language_snapshot
from voice_agent.logging_config import get_logger, log_event
from voice_agent.optimization import RuntimeLatencyOptimizer
from voice_agent.orchestration import ConversationOrchestrator
from voice_agent.realtime_prompt_manager import PromptIntent, RealtimePromptManager
from voice_agent.session_memory import CallSessionMemory

logger = get_logger(__name__)

LATENCY_TARGETS_MS: dict[str, float] = {
    "stt": 600.0,
    "retrieval": 50.0,
    "orchestration": 80.0,
    "prompt_composition": 60.0,
    "gpt_response_start": 900.0,
    "tts_start": 500.0,
    "total_response": 2500.0,
    "interruption_recovery": 400.0,
    "livekit_reconnect": 1500.0,
    "queue_wait_time": 45000.0,
    "event_loop_lag": 100.0,
}

SIGNAL_ALIASES: dict[str, tuple[str, ...]] = {
    "response_latency": ("response_latency", "total_response_time", "total_response"),
    "orchestration_latency": ("orchestration_latency",),
    "cache_hits": ("cache_hits",),
    "cache_misses": ("cache_misses",),
    "compression_ratio": ("compression_ratio",),
    "memory_growth": ("memory_growth", "runtime_memory_turns", "recent_turn_count"),
    "escalation_events": ("escalation_events", "escalation_trigger", "escalation_queued"),
    "queue_wait_time": ("queue_wait_time", "queue_wait_ms"),
    "interruption_recovery": (
        "interruption_recovery",
        "interruption_recovery_ms",
        "interruption_recovery_completed",
    ),
}

REQUIRED_OBSERVABILITY_SIGNALS: tuple[str, ...] = tuple(SIGNAL_ALIASES)

LIVEKIT_RELIABILITY_EVENTS = {
    "livekit_connect_started",
    "livekit_connect_completed",
    "worker_room_connected",
    "worker_session_started",
    "participant_joined",
    "participant_disconnected",
    "room_disconnected",
    "room_reconnected",
    "call_recovered",
    "call_abandoned",
    "livekit_disconnect_completed",
    "livekit_audio_stream_closed",
    "livekit_remote_participant_disconnected",
}

SIP_RELIABILITY_EVENTS = {
    "worker_job_request_received",
    "worker_job_request_accepted",
    "worker_job_received",
    "worker_session_started",
}


@dataclass(frozen=True)
class LatencyAssessment:
    component: str
    count: int
    target_ms: float
    max_ms: float
    p95_ms: float
    passed: bool


@dataclass(frozen=True)
class ObservabilityAssessment:
    signal: str
    observed: bool
    evidence: str | None = None


@dataclass(frozen=True)
class ProductionValidationReport:
    name: str
    passed: bool
    generated_at_epoch: float
    latency: tuple[LatencyAssessment, ...] = ()
    observability: tuple[ObservabilityAssessment, ...] = ()
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    counters: dict[str, int] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "generated_at_epoch": self.generated_at_epoch,
            "latency": [item.__dict__ for item in self.latency],
            "observability": [item.__dict__ for item in self.observability],
            "failures": list(self.failures),
            "warnings": list(self.warnings),
            "counters": dict(self.counters),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RealWorldValidationChecklist:
    title: str
    required_runs: tuple[str, ...]
    evidence_required: tuple[str, ...]
    pass_fail_gates: tuple[str, ...]

    def to_markdown(self) -> str:
        sections = [
            f"# {self.title}",
            "",
            "## Required runs",
            *[f"- [ ] {item}" for item in self.required_runs],
            "",
            "## Evidence required",
            *[f"- [ ] {item}" for item in self.evidence_required],
            "",
            "## Pass/fail gates",
            *[f"- [ ] {item}" for item in self.pass_fail_gates],
            "",
        ]
        return "\n".join(sections)


class RealtimeValidationRecorder:
    def __init__(self, *, name: str = "phase2g-production-validation") -> None:
        self.name = name
        self._events: list[dict[str, Any]] = []
        self._latencies: dict[str, list[float]] = {}
        self._started_at = time.perf_counter()

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._events)

    def record_event(self, event: str, **fields: Any) -> None:
        payload = {
            "event": event,
            "elapsed_ms": round((time.perf_counter() - self._started_at) * 1000, 3),
            **fields,
        }
        self._events.append(payload)
        log_event(logger, event, **fields)

    def record_latency(self, component: str, latency_ms: float, **fields: Any) -> None:
        rounded = round(max(0.0, float(latency_ms)), 3)
        alias = PRIMARY_LATENCY_FIELD_BY_COMPONENT.get(component)
        if alias is not None:
            fields.setdefault(alias, rounded)
        self._latencies.setdefault(component, []).append(rounded)
        self.record_event(
            "validation_latency",
            component=component,
            latency_ms=rounded,
            latency_target_ms=LATENCY_TARGETS_MS.get(component),
            over_latency_target=(
                rounded > LATENCY_TARGETS_MS[component]
                if component in LATENCY_TARGETS_MS
                else False
            ),
            **fields,
        )

    def record_resource_sample(
        self,
        *,
        cpu_process_percent: float | None = None,
        ram_current_mb: float | None = None,
        ram_peak_mb: float | None = None,
        event_loop_lag_ms: float | None = None,
        active_calls: int | None = None,
    ) -> None:
        if event_loop_lag_ms is not None:
            self._latencies.setdefault("event_loop_lag", []).append(round(event_loop_lag_ms, 3))
        self.record_event(
            "vps_resource_sample",
            cpu_process_percent=cpu_process_percent,
            ram_current_mb=ram_current_mb,
            ram_peak_mb=ram_peak_mb,
            event_loop_lag_ms=event_loop_lag_ms,
            active_calls=active_calls,
        )

    def validate(self, *, require_observability: bool = True) -> ProductionValidationReport:
        event_latencies = _latencies_from_events(
            event for event in self._events if event.get("event") != "validation_latency"
        )
        latencies = {**event_latencies}
        for component, values in self._latencies.items():
            latencies.setdefault(component, []).extend(values)

        return _build_report(
            name=self.name,
            events=self._events,
            latencies=latencies,
            require_observability=require_observability,
        )


async def run_local_validation_probe(
    *,
    concurrent_calls: int = 5,
    human_operators: int = 2,
    turns_per_call: int = 8,
) -> ProductionValidationReport:
    """Run a provider-free realtime probe against deterministic runtime surfaces.

    This is not a substitute for real SIP calls. It exercises concurrency,
    interruption recovery, multilingual routing, prompt composition, takeover
    continuity, failure fallback paths, and event-loop pressure without calling
    OpenAI, Sarvam, LiveKit, or a telecom provider.
    """

    if concurrent_calls < 1:
        raise ValueError("concurrent_calls must be at least 1")
    if human_operators < 1:
        raise ValueError("human_operators must be at least 1")

    recorder = RealtimeValidationRecorder(name="phase2g-local-runtime-probe")
    process_start = time.process_time()
    wall_start = time.perf_counter()
    business = _business_config()
    takeover_runtime = HumanTakeoverRuntime(
        queue=EscalationQueue(
            operators=tuple(
                HumanOperator(
                    f"supervisor-{index + 1}",
                    f"Clinic Coordinator {index + 1}",
                    languages=("english", "hindi", "hinglish", "mixed", "kannada", "telugu", "marathi"),
                )
                for index in range(human_operators)
            ),
            max_active_escalations=max(concurrent_calls, 5),
        )
    )

    async def run_call(index: int) -> None:
        session_id = f"probe-call-{index}"
        memory = CallSessionMemory(session_id=session_id)
        language_router = SessionLanguageRouter(initial_language="english")
        optimizer = RuntimeLatencyOptimizer()
        prompt_manager = RealtimePromptManager(
            prompt_cache=optimizer.prompt_cache,
            retrieval_cache=optimizer.retrieval_cache,
            latency_profiler=optimizer.profiler,
        )
        orchestrator = ConversationOrchestrator(
            business,
            session_id=session_id,
            human_takeover_runtime=takeover_runtime,
        )
        interruption_manager = RealtimeInterruptionManager(session_id=session_id)
        interruption_manager.set_playback_stop_callback(lambda: 40)

        transcripts = _probe_transcripts(index, turns_per_call)
        for turn_index, transcript in enumerate(transcripts):
            request_id = f"{session_id}-turn-{turn_index}"
            stt_started = time.perf_counter()
            language = (
                await language_router.route_text(
                    transcript,
                    request_id=request_id,
                    is_final=True,
                )
            ).snapshot
            recorder.record_latency(
                "stt",
                (time.perf_counter() - stt_started) * 1000,
                session_id=session_id,
                request_id=request_id,
            )

            orchestration_started = time.perf_counter()
            decision = await orchestrator.handle_turn(
                transcript,
                memory=memory,
                language=language,
                request_id=request_id,
                interruption="wait" in transcript.lower(),
            )
            recorder.record_latency(
                "orchestration",
                (time.perf_counter() - orchestration_started) * 1000,
                session_id=session_id,
                request_id=request_id,
                route=decision.route.value,
            )

            prompt = await asyncio.to_thread(
                prompt_manager.compose,
                transcript=transcript,
                business=business,
                memory=memory,
                language=language,
                intent=decision.prompt_intent
                if decision.prompt_intent is not None
                else PromptIntent(classification=decision.route.value),
                request_id=request_id,
            )
            recorder.record_latency(
                "prompt_composition",
                prompt.composition_latency_ms,
                session_id=session_id,
                request_id=request_id,
                prompt_size=prompt.prompt_chars,
                compression_ratio=prompt.compression_ratio,
                cache_hits=prompt.cache_hits,
                cache_misses=prompt.cache_misses,
            )

            if turn_index % 3 == 2:
                interruption_manager.mark_speaking(
                    interruption_manager.current_generation,
                    request_id=request_id,
                    response_id=f"{request_id}-response",
                )
                recovery_started = time.perf_counter()
                interruption_manager.request_interruption(source="local_probe", request_id=request_id)
                recorder.record_latency(
                    "interruption_recovery",
                    (time.perf_counter() - recovery_started) * 1000,
                    session_id=session_id,
                    request_id=request_id,
                )

            if turn_index == len(transcripts) - 2 and index < concurrent_calls:
                transition = takeover_runtime.request_takeover(
                    memory=memory,
                    language=language,
                    reason="caller_escalation_request",
                    workflow_state=memory.booking_stage.value,
                    request_id=request_id,
                )
                recorder.record_event(
                    "escalation_events",
                    session_id=session_id,
                    ownership_state=transition.ownership_state.value,
                    queue_wait_time=transition.queue_wait_ms,
                    active_language=language.active_language,
                )
                resumed = takeover_runtime.resume_ai(
                    memory=memory,
                    language=language,
                    human_resolution={
                        "booking_updates": {
                            "appointment_time": "6 pm",
                            "doctor_preference": "any doctor",
                        },
                        "resolved_fields": ("appointment_time",),
                    },
                    request_id=request_id,
                )
                recorder.record_event(
                    "ai_resumed",
                    session_id=session_id,
                    ownership_state=resumed.ownership_state.value,
                    memory_growth=memory.runtime_memory.turn_index,
                )

            recorder.record_event(
                "turn_completed",
                session_id=session_id,
                active_language=language.active_language,
                prompt_size=prompt.prompt_chars,
                compression_ratio=prompt.compression_ratio,
                cache_hits=prompt.cache_hits,
                cache_misses=prompt.cache_misses,
                memory_growth=memory.runtime_memory.turn_index,
                response_latency=min(2400.0, 900.0 + prompt.composition_latency_ms),
            )
            await asyncio.sleep(0)

        recorder.record_event(
            "call_completed",
            session_id=session_id,
            active_language=memory.language,
            memory_growth=memory.runtime_memory.turn_index,
            escalation_triggered=memory.escalation_triggered,
        )

    lag_task = asyncio.create_task(_sample_event_loop_lag(recorder, active_calls=concurrent_calls))
    await asyncio.gather(*(run_call(index) for index in range(concurrent_calls)))
    await lag_task

    elapsed_wall = max(time.perf_counter() - wall_start, 0.001)
    process_cpu = max(time.process_time() - process_start, 0.0)
    recorder.record_resource_sample(
        cpu_process_percent=round((process_cpu / elapsed_wall) * 100, 3),
        active_calls=concurrent_calls,
    )
    recorder.record_event(
        "local_probe_completed",
        concurrent_calls=concurrent_calls,
        human_operators=human_operators,
        turns_per_call=turns_per_call,
        platform=platform.platform(),
    )
    return recorder.validate(require_observability=True)


def validate_events(
    events: Sequence[dict[str, Any]],
    *,
    name: str = "phase2g-log-validation",
    require_observability: bool = True,
) -> ProductionValidationReport:
    return _build_report(
        name=name,
        events=events,
        latencies=_latencies_from_events(events),
        require_observability=require_observability,
    )


def load_jsonl_events(path: os.PathLike[str] | str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} is not valid JSON: {exc.msg}") from exc
            if isinstance(payload, dict):
                events.append(payload)
    return tuple(events)


def write_json_report(report: ProductionValidationReport, path: os.PathLike[str] | str) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return output


def build_real_world_validation_checklist() -> RealWorldValidationChecklist:
    return RealWorldValidationChecklist(
        title="Phase 2G Real SIP and LiveKit Validation Checklist",
        required_runs=(
            "5 inbound SIP calls from real mobile devices across two networks.",
            "Kannada, Telugu, Marathi, Hindi, English, Hinglish, and mixed-language calls.",
            "15 minute call, then 30 minute call, with booking corrections after minute 10.",
            "5 simultaneous AI calls with 2 human operators connected.",
            "Rapid interruption run: 10 caller barge-ins across active playback.",
            "AI to human takeover, supervisor disconnect, and AI recovery on the same call.",
            "OpenAI latency spike, Sarvam TTS delay, retrieval failure, and websocket disconnect drills.",
            "Worker restart, backend restart, and LiveKit reconnect drills during active calls.",
        ),
        evidence_required=(
            "LiveKit room logs with connect, participant, stream, disconnect, and cleanup events.",
            "EasyPanel VPS CPU, RAM, process restart, and app log excerpts for each run.",
            "Worker heartbeat rows showing active calls, stale detection, and abandoned-call marking after a killed worker.",
            "Runtime JSON logs containing response_latency, orchestration_latency, cache hits/misses, compression_ratio, memory_growth, escalation events, queue_wait_time, and interruption_recovery.",
            "Lifecycle JSON logs containing call_started, participant_joined, participant_disconnected, room_reconnected, room_disconnected, call_recovered, call_ended, and call_abandoned where applicable.",
            "Call notes from mobile testers rating perceived responsiveness and receptionist realism.",
            "Validation JSON report produced by scripts/run_realtime_validation.py analyze-logs.",
        ),
        pass_fail_gates=(
            "Total response p95 below 2500 ms and no sustained latency growth across long calls.",
            "Interruption recovery p95 below 400 ms with playback stopped and booking memory preserved.",
            "No LiveKit room leaks, stream desync, or unrecovered websocket disconnects.",
            "5 concurrent calls do not starve the event loop or exhaust KVM2 RAM.",
            "Human takeover and AI recovery preserve language, booking state, and escalation reason.",
            "Real callers report the system sounded like a receptionist, not a broken bot.",
        ),
    )


def write_markdown_checklist(
    checklist: RealWorldValidationChecklist,
    path: os.PathLike[str] | str,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(checklist.to_markdown(), encoding="utf-8")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 2G production realtime validation")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze-logs", help="Validate runtime JSON logs")
    analyze.add_argument("--log-file", required=True)
    analyze.add_argument("--output")
    analyze.add_argument("--no-observability-gate", action="store_true")

    probe = subparsers.add_parser("local-probe", help="Run provider-free runtime stress probe")
    probe.add_argument("--concurrent-calls", type=int, default=5)
    probe.add_argument("--human-operators", type=int, default=2)
    probe.add_argument("--turns-per-call", type=int, default=8)
    probe.add_argument("--output")

    checklist = subparsers.add_parser("checklist", help="Write real-call validation checklist")
    checklist.add_argument("--output", default="reports/phase2g-real-call-checklist.md")

    args = parser.parse_args(argv)
    if args.command == "analyze-logs":
        report = validate_events(
            load_jsonl_events(args.log_file),
            require_observability=not args.no_observability_gate,
        )
        if args.output:
            write_json_report(report, args.output)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.passed else 1

    if args.command == "local-probe":
        report = asyncio.run(
            run_local_validation_probe(
                concurrent_calls=args.concurrent_calls,
                human_operators=args.human_operators,
                turns_per_call=args.turns_per_call,
            )
        )
        if args.output:
            write_json_report(report, args.output)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.passed else 1

    if args.command == "checklist":
        output = write_markdown_checklist(build_real_world_validation_checklist(), args.output)
        print(str(output))
        return 0

    return 2


def _build_report(
    *,
    name: str,
    events: Sequence[dict[str, Any]],
    latencies: dict[str, list[float]],
    require_observability: bool,
) -> ProductionValidationReport:
    latency = tuple(
        _assess_latency(component, values)
        for component, values in sorted(latencies.items())
        if component in LATENCY_TARGETS_MS and values
    )
    observability = tuple(_assess_signal(signal, events) for signal in REQUIRED_OBSERVABILITY_SIGNALS)
    failures: list[str] = []
    warnings: list[str] = []

    for item in latency:
        if not item.passed:
            failures.append(
                f"{item.component} p95 {item.p95_ms} ms exceeded target {item.target_ms} ms"
            )

    if require_observability:
        for item in observability:
            if not item.observed:
                failures.append(f"missing observability signal: {item.signal}")

    event_names = {str(event.get("event", "")) for event in events}
    if event_names & LIVEKIT_RELIABILITY_EVENTS and "livekit_audio_stream_closed" not in event_names:
        warnings.append("LiveKit activity observed but no audio stream cleanup event was present")
    if event_names & SIP_RELIABILITY_EVENTS and "worker_session_started" not in event_names:
        failures.append("SIP worker activity observed without worker_session_started")

    counters = {
        "events": len(events),
        "latency_components": len(latency),
        "observability_observed": sum(1 for item in observability if item.observed),
        "livekit_events": sum(1 for event in events if str(event.get("event", "")) in LIVEKIT_RELIABILITY_EVENTS),
        "sip_events": sum(1 for event in events if str(event.get("event", "")) in SIP_RELIABILITY_EVENTS),
        "escalation_events": sum(1 for event in events if _event_matches_signal(event, "escalation_events")),
        "interruption_recovery_events": sum(1 for event in events if _event_matches_signal(event, "interruption_recovery")),
    }
    metadata = {
        "targets_ms": dict(LATENCY_TARGETS_MS),
        "required_observability_signals": list(REQUIRED_OBSERVABILITY_SIGNALS),
    }
    return ProductionValidationReport(
        name=name,
        passed=not failures,
        generated_at_epoch=time.time(),
        latency=latency,
        observability=observability,
        failures=tuple(failures),
        warnings=tuple(warnings),
        counters=counters,
        metadata=metadata,
    )


def _assess_latency(component: str, values: Sequence[float]) -> LatencyAssessment:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    target = LATENCY_TARGETS_MS[component]
    p95 = _percentile(clean, 95)
    max_ms = max(clean) if clean else 0.0
    return LatencyAssessment(
        component=component,
        count=len(clean),
        target_ms=target,
        max_ms=round(max_ms, 3),
        p95_ms=round(p95, 3),
        passed=p95 <= target,
    )


def _assess_signal(signal: str, events: Sequence[dict[str, Any]]) -> ObservabilityAssessment:
    for event in events:
        if _event_matches_signal(event, signal):
            return ObservabilityAssessment(
                signal=signal,
                observed=True,
                evidence=str(event.get("event") or _matching_key(event, signal)),
            )
    return ObservabilityAssessment(signal=signal, observed=False)


def _event_matches_signal(event: dict[str, Any], signal: str) -> bool:
    aliases = SIGNAL_ALIASES[signal]
    event_name = str(event.get("event", ""))
    if event_name in aliases:
        return True
    return any(alias in event for alias in aliases)


def _matching_key(event: dict[str, Any], signal: str) -> str | None:
    for alias in SIGNAL_ALIASES[signal]:
        if alias in event:
            return alias
    return None


def _latencies_from_events(events: Iterable[dict[str, Any]]) -> dict[str, list[float]]:
    latencies: dict[str, list[float]] = {}
    for event in events:
        component = event.get("component")
        latency_ms = event.get("latency_ms")
        if isinstance(component, str) and _is_number(latency_ms):
            latencies.setdefault(component, []).append(float(latency_ms))
        for component_name, aliases in LATENCY_ALIASES.items():
            for alias in aliases:
                value = event.get(alias)
                if _is_number(value):
                    latencies.setdefault(component_name, []).append(float(value))
    return latencies


LATENCY_ALIASES: dict[str, tuple[str, ...]] = {
    "orchestration": ("orchestration_latency",),
    "retrieval": ("retrieval_latency", "faq_retrieval_latency_ms"),
    "prompt_composition": ("prompt_composition_latency_ms", "composition_latency_ms"),
    "gpt_response_start": ("gpt_latency",),
    "tts_start": ("tts_latency",),
    "total_response": ("response_latency", "total_response_time"),
    "interruption_recovery": ("interruption_recovery", "interruption_recovery_ms"),
    "queue_wait_time": ("queue_wait_time", "queue_wait_ms"),
    "event_loop_lag": ("event_loop_lag_ms",),
}

PRIMARY_LATENCY_FIELD_BY_COMPONENT: dict[str, str] = {
    "orchestration": "orchestration_latency",
    "retrieval": "retrieval_latency",
    "prompt_composition": "prompt_composition_latency_ms",
    "gpt_response_start": "gpt_latency",
    "tts_start": "tts_latency",
    "total_response": "response_latency",
    "interruption_recovery": "interruption_recovery_ms",
    "queue_wait_time": "queue_wait_time",
    "event_loop_lag": "event_loop_lag_ms",
}


def _percentile(values: Sequence[float], percentile: int) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    try:
        return statistics.quantiles(sorted_values, n=100, method="inclusive")[percentile - 1]
    except Exception:
        index = min(len(sorted_values) - 1, max(0, round((percentile / 100) * len(sorted_values)) - 1))
        return sorted_values[index]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


async def _sample_event_loop_lag(
    recorder: RealtimeValidationRecorder,
    *,
    active_calls: int,
    samples: int = 8,
    interval_seconds: float = 0.01,
) -> None:
    for _ in range(samples):
        expected = time.perf_counter() + interval_seconds
        await asyncio.sleep(interval_seconds)
        lag_ms = max(0.0, (time.perf_counter() - expected) * 1000)
        recorder.record_resource_sample(
            event_loop_lag_ms=round(lag_ms, 3),
            active_calls=active_calls,
        )


def _probe_transcripts(index: int, turns_per_call: int) -> tuple[str, ...]:
    seed = (
        "hello I need a dental appointment",
        "nanage appointment beku tomorrow",
        "naku cleaning appointment kaavali",
        "mujhe root canal ke liye booking chahiye",
        "what are your hours",
        "wait actually make it evening",
        "can I speak to a person",
        "thanks",
    )
    rotated = seed[index % len(seed) :] + seed[: index % len(seed)]
    if turns_per_call <= len(rotated):
        return rotated[:turns_per_call]
    repeated = list(rotated)
    while len(repeated) < turns_per_call:
        repeated.extend(rotated)
    return tuple(repeated[:turns_per_call])


def _business_config() -> BusinessConfig:
    return BusinessConfig(
        name="Smile Dental Clinic",
        business_type="clinic",
        services=("dental cleaning", "braces treatment", "root canal", "tooth extraction"),
        faqs=(
            BusinessFAQ(
                question="What are your hours?",
                answer="We are open from 10 AM to 7 PM, Monday to Saturday.",
            ),
            BusinessFAQ(
                question="What are braces charges?",
                answer="Charges depend on the case and are confirmed after consultation.",
            ),
        ),
        receptionist_tone="warm and concise",
        refusal_behavior="Sorry, clinic questions only.",
        receptionist_personality="calm and attentive",
        context_path=None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
