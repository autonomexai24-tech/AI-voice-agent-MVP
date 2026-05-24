from __future__ import annotations

import json
import subprocess
import sys

from voice_agent.deployment_diagnostics import DeploymentDiagnosticsService
from database.session import DatabaseSettings


def test_deployment_diagnostics_reports_environment_consistency() -> None:
    service = DeploymentDiagnosticsService(
        database_settings=DatabaseSettings(url=None, enabled=False),
        session_factory=None,
        env={
            "LIVEKIT_URL": "wss://example.livekit.cloud",
            "LIVEKIT_API_KEY": "key",
            "LIVEKIT_API_SECRET": "secret",
            "SARVAM_API_KEY": "sarvam",
            "OPENAI_API_KEY": "openai",
            "DATABASE_URL": "postgresql://postgres:postgres@postgres:5432/app",
            "WORKER_ID": "voice-worker-1",
            "PORT": "8000",
            "WORKER_HEALTH_PORT": "8081",
        },
    )

    audit = service.environment_audit()

    assert audit["status"] == "consistent"
    assert audit["missing_required"] == []
    assert audit["database_host"] == "postgres"
    assert audit["livekit_scheme"] == "wss"
    assert audit["worker_id"] == "voice-worker-1"


def test_deployment_topology_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/validate_deployment_infrastructure.py"],
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["failures"] == []

