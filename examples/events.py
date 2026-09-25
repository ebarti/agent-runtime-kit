"""Observe normalized events from an offline fake task."""

import asyncio
from collections.abc import Mapping
from typing import Any

from agent_runtime_kit import AgentKit, FakeAgentRuntime


async def main() -> None:
    async with AgentKit() as kit:

        @kit.on("agent.task.started")
        def on_start(event: Mapping[str, Any]) -> None:
            print(event["name"])

        @kit.on("agent.tool.completed")
        def on_tool(event: Mapping[str, Any]) -> None:
            print(event["name"])

        @kit.on("agent.task.completed")
        def on_complete(event: Mapping[str, Any]) -> None:
            print(event["name"])

        result = await kit.run(FakeAgentRuntime(output="done"), goal="Observe events")
        if not result.is_success:
            raise RuntimeError(result.error or result.finish_reason)


if __name__ == "__main__":
    asyncio.run(main())
