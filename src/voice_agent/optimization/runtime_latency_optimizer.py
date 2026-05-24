from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterator

from voice_agent.logging_config import get_logger, log_event
from voice_agent.optimization.context_compression_engine import (
    PromptCacheLayer,
    RetrievalCache,
)

logger = get_logger(__name__)

LATENCY_TARGETS_MS = {
    "stt": 600.0,
    "retrieval": 50.0,
    "orchestration": 80.0,
    "memory_assembly": 100.0,
    "prompt_composition": 60.0,
    "gpt_response_start": 900.0,
    "gpt": 900.0,
    "tts_start": 500.0,
    "tts": 500.0,
    "total_response": 2500.0,
    "interruption_recovery": 120.0,
}


@dataclass(frozen=True)
class LatencyMeasurement:
    component: str
    latency_ms: float
    target_ms: float | None
    over_target: bool


@dataclass(frozen=True)
class LatencyProfileSnapshot:
    measurements: tuple[LatencyMeasurement, ...]
    total_response_time: float
    over_target_components: tuple[str, ...]
    component_latencies: dict[str, float] = field(default_factory=dict)


class RuntimeLatencyProfiler:
    """Small, failure-safe profiler for realtime response stages."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.perf_counter,
        max_measurements: int = 256,
    ) -> None:
        self._clock = clock
        self._max_measurements = max(16, max_measurements)
        self._measurements: list[LatencyMeasurement] = []

    @contextmanager
    def span(
        self,
        component: str,
        *,
        request_id: str | None = None,
        **fields: Any,
    ) -> Iterator[None]:
        started_at = self._clock()
        try:
            yield
        finally:
            self.record(
                component,
                (self._clock() - started_at) * 1000,
                request_id=request_id,
                **fields,
            )

    @asynccontextmanager
    async def async_span(
        self,
        component: str,
        *,
        request_id: str | None = None,
        **fields: Any,
    ) -> AsyncIterator[None]:
        started_at = self._clock()
        try:
            yield
        finally:
            self.record(
                component,
                (self._clock() - started_at) * 1000,
                request_id=request_id,
                **fields,
            )

    def record(
        self,
        component: str,
        latency_ms: float,
        *,
        request_id: str | None = None,
        **fields: Any,
    ) -> LatencyMeasurement:
        try:
            target = LATENCY_TARGETS_MS.get(component)
            rounded = round(max(0.0, latency_ms), 3)
            measurement = LatencyMeasurement(
                component=component,
                latency_ms=rounded,
                target_ms=target,
                over_target=bool(target is not None and rounded > target),
            )
            self._measurements.append(measurement)
            del self._measurements[: -self._max_measurements]
            log_event(
                logger,
                "runtime_latency_profile",
                request_id=request_id,
                component=component,
                latency_ms=rounded,
                latency_target_ms=target,
                over_latency_target=measurement.over_target,
                stt_latency=rounded if component == "stt" else None,
                retrieval_latency=rounded if component == "retrieval" else None,
                orchestration_latency=rounded if component == "orchestration" else None,
                memory_latency=rounded if component == "memory_assembly" else None,
                gpt_latency=rounded if component in {"gpt", "gpt_response_start"} else None,
                tts_latency=rounded if component in {"tts", "tts_start"} else None,
                total_response_time=rounded if component == "total_response" else None,
                response_latency=rounded if component == "total_response" else None,
                **fields,
            )
            return measurement
        except Exception:
            return LatencyMeasurement(
                component=component,
                latency_ms=max(0.0, latency_ms),
                target_ms=LATENCY_TARGETS_MS.get(component),
                over_target=False,
            )

    def snapshot(self) -> LatencyProfileSnapshot:
        component_latencies: dict[str, float] = {}
        for measurement in self._measurements:
            component_latencies[measurement.component] = measurement.latency_ms
        total = component_latencies.get("total_response", sum(component_latencies.values()))
        over = tuple(
            measurement.component
            for measurement in self._measurements
            if measurement.over_target
        )
        return LatencyProfileSnapshot(
            measurements=tuple(self._measurements),
            total_response_time=round(total, 3),
            over_target_components=tuple(dict.fromkeys(over)),
            component_latencies=component_latencies,
        )

    def log_summary(
        self,
        *,
        request_id: str | None = None,
        prompt_size: int | None = None,
        compression_ratio: float | None = None,
        memory_pruned: int | None = None,
        cache_hits: int | None = None,
        cache_misses: int | None = None,
    ) -> LatencyProfileSnapshot:
        snapshot = self.snapshot()
        log_event(
            logger,
            "runtime_latency_summary",
            request_id=request_id,
            prompt_size=prompt_size,
            compression_ratio=compression_ratio,
            memory_pruned=memory_pruned,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            retrieval_latency=snapshot.component_latencies.get("retrieval"),
            orchestration_latency=snapshot.component_latencies.get("orchestration"),
            memory_latency=snapshot.component_latencies.get("memory_assembly"),
            gpt_latency=snapshot.component_latencies.get("gpt")
            or snapshot.component_latencies.get("gpt_response_start"),
            tts_latency=snapshot.component_latencies.get("tts")
            or snapshot.component_latencies.get("tts_start"),
            total_response_time=snapshot.total_response_time,
            response_latency=snapshot.total_response_time,
            over_target_components=list(snapshot.over_target_components),
        )
        return snapshot


class RuntimeLatencyOptimizer:
    """Shared optimization foundation for prompt, retrieval, and latency state."""

    def __init__(
        self,
        *,
        prompt_cache: PromptCacheLayer | None = None,
        retrieval_cache: RetrievalCache | None = None,
        profiler: RuntimeLatencyProfiler | None = None,
    ) -> None:
        self.prompt_cache = prompt_cache or PromptCacheLayer()
        self.retrieval_cache = retrieval_cache or RetrievalCache()
        self.profiler = profiler or RuntimeLatencyProfiler()

    async def gather_pre_response(
        self,
        *aws: Any,
        request_id: str | None = None,
    ) -> tuple[Any, ...]:
        """Run independent lightweight preparation without blocking audio work."""
        try:
            return await asyncio.gather(*aws)
        except Exception as exc:
            log_event(
                logger,
                "runtime_parallel_preparation_failed",
                request_id=request_id,
                error_type=type(exc).__name__,
            )
            raise

    def should_start_streaming_early(self, *, route: str, prompt_size: int) -> bool:
        return route in {"booking", "faq", "escalation", "interruption"} or prompt_size <= 1200

    def log_response_summary(
        self,
        *,
        request_id: str | None = None,
        prompt_size: int | None = None,
        compression_ratio: float | None = None,
        memory_pruned: int | None = None,
    ) -> LatencyProfileSnapshot:
        prompt_stats = self.prompt_cache.stats()
        retrieval_stats = self.retrieval_cache.stats()
        return self.profiler.log_summary(
            request_id=request_id,
            prompt_size=prompt_size,
            compression_ratio=compression_ratio,
            memory_pruned=memory_pruned,
            cache_hits=prompt_stats.hits + retrieval_stats.hits,
            cache_misses=prompt_stats.misses + retrieval_stats.misses,
        )
