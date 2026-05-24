from __future__ import annotations

_PRODUCTION_EXPORTS = {
    "LATENCY_TARGETS_MS",
    "REQUIRED_OBSERVABILITY_SIGNALS",
    "LatencyAssessment",
    "ObservabilityAssessment",
    "ProductionValidationReport",
    "RealtimeValidationRecorder",
    "RealWorldValidationChecklist",
    "build_real_world_validation_checklist",
    "load_jsonl_events",
    "run_local_validation_probe",
    "validate_events",
    "write_json_report",
    "write_markdown_checklist",
}

__all__ = [
    "RuntimeIntegrityValidator",
    *_PRODUCTION_EXPORTS,
]


def __getattr__(name: str):
    if name == "RuntimeIntegrityValidator":
        from voice_agent.validation.runtime_integrity import RuntimeIntegrityValidator

        return RuntimeIntegrityValidator
    if name in _PRODUCTION_EXPORTS:
        from voice_agent.validation import production_realtime

        return getattr(production_realtime, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
