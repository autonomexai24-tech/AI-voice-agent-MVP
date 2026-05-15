"""Entrypoint for ``python -m voice_agent``.

Delegates to the worker module which uses the official LiveKit AgentSession
+ Sarvam plugin architecture.  Supports ``dev`` and ``start`` subcommands
provided by the LiveKit agents CLI.

Usage:
    python -m voice_agent dev       # development mode (single job)
    python -m voice_agent start     # production mode (accepts jobs)
"""
from __future__ import annotations

from voice_agent.worker import main

if __name__ == "__main__":
    main()
