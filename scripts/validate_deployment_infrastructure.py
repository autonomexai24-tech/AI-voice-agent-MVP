from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.yml"
ENV_EXAMPLE_PATH = ROOT / ".env.docker.example"

REQUIRED_SERVICES = ("postgres", "backend", "worker", "frontend")
REQUIRED_HEALTHCHECK_SERVICES = ("postgres", "backend", "worker")
SHARED_RUNTIME_ENV = (
    "DATABASE_URL",
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "SARVAM_API_KEY",
    "OPENAI_API_KEY",
)
WORKER_RESILIENCE_ENV = (
    "WORKER_ID",
    "WORKER_HEALTH_PORT",
    "WORKER_HEARTBEAT_INTERVAL_SECONDS",
    "WORKER_STALE_AFTER_SECONDS",
    "WORKER_DRAIN_TIMEOUT_SECONDS",
    "MAX_CALL_DURATION_SECONDS",
    "CALL_TIMEOUT_WARNING_SECONDS",
)


def main() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")
    env_text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    sections = _service_sections(compose_text)
    checks = {
        "services_present": {
            service: service in sections for service in REQUIRED_SERVICES
        },
        "healthchecks_present": {
            service: "healthcheck:" in sections.get(service, "")
            for service in REQUIRED_HEALTHCHECK_SERVICES
        },
        "dependencies_present": {
            "backend_depends_on_postgres": "postgres:" in sections.get("backend", "")
            and "condition: service_healthy" in sections.get("backend", ""),
            "worker_depends_on_postgres_and_backend": all(
                token in sections.get("worker", "")
                for token in (
                    "postgres:",
                    "backend:",
                    "condition: service_healthy",
                )
            ),
            "frontend_depends_on_backend": "backend:" in sections.get("frontend", "")
            and "condition: service_healthy" in sections.get("frontend", ""),
        },
        "shared_env_present": {
            name: name in sections.get("backend", "") and name in sections.get("worker", "")
            for name in SHARED_RUNTIME_ENV
        },
        "worker_resilience_env_present": {
            name: name in sections.get("worker", "") for name in WORKER_RESILIENCE_ENV
        },
        "resource_limits_present": {
            service: ("mem_limit:" in sections.get(service, "") and "cpus:" in sections.get(service, ""))
            for service in REQUIRED_SERVICES
        },
        "env_example_present": {
            name: name in env_text
            for name in (
                "DEPLOYMENT_VERSION",
                "BACKEND_MEMORY_LIMIT",
                "WORKER_MEMORY_LIMIT",
                "FRONTEND_INTERNAL_API_BASE_URL",
            )
        },
    }
    failures = [
        f"{group}.{name}"
        for group, values in checks.items()
        for name, passed in values.items()
        if not passed
    ]
    result: dict[str, Any] = {
        "status": "pass" if not failures else "fail",
        "compose_path": str(COMPOSE_PATH),
        "env_example_path": str(ENV_EXAMPLE_PATH),
        "checks": checks,
        "failures": failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if not failures else 1)


def _service_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^  ([a-zA-Z0-9_-]+):\n", text, flags=re.MULTILINE))
    for index, match in enumerate(matches):
        service = match.group(1)
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[service] = text[start:end]
    return sections


if __name__ == "__main__":
    main()

