"""Command entry point for the SDK evolution agent."""

from __future__ import annotations

import asyncio

from examples.sdk_evolution_agent.cli import main

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
