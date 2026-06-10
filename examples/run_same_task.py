"""Run the same task through every configured runtime."""

from __future__ import annotations

import asyncio

from agent_runtime_kit import AgentTask, create_default_registry
from agent_runtime_kit.adapters import register_adapters


async def main() -> None:
    registry = create_default_registry()
    register_adapters(registry)

    task = AgentTask(goal="Summarize this repository in one paragraph.")
    for kind in registry.kinds():
        if kind.value == "fake":
            continue
        runtime = registry.resolve(kind)
        diagnostic = runtime.availability()
        if not diagnostic.available:
            print(f"{kind.value}: unavailable - {diagnostic.message}")
            continue
        result = await runtime.run(task)
        print(f"{kind.value}: {result.output}")


if __name__ == "__main__":
    asyncio.run(main())
