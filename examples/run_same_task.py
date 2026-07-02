"""Run the same task through every configured runtime."""

from __future__ import annotations

import asyncio

from agent_runtime_kit import AgentTask, create_default_registry, runtime_kind_value
from agent_runtime_kit.adapters import register_adapters


async def main() -> None:
    registry = create_default_registry()
    register_adapters(registry)

    task = AgentTask(goal="Summarize this repository in one paragraph.")
    for kind in registry.kinds():
        # runtime_kind_value: kinds may be enum members or namespaced strings.
        label = runtime_kind_value(kind)
        if label == "fake":
            continue
        runtime = registry.resolve(kind)
        diagnostic = runtime.availability()
        if not diagnostic.available:
            print(f"{label}: unavailable - {diagnostic.message}")
            continue
        result = await runtime.run(task)
        print(f"{label}: {result.output}")


if __name__ == "__main__":
    asyncio.run(main())
