"""Run one deterministic task with the vendor-SDK-free core package."""

import asyncio

from agent_runtime_kit import AgentKit, FakeAgentRuntime


async def main() -> None:
    async with AgentKit() as kit:
        result = await kit.run(
            FakeAgentRuntime(output="offline example complete"),
            goal="Demonstrate a task result",
        )
        if not result.is_success:
            raise RuntimeError(result.error or result.finish_reason)
        print(result.output)


if __name__ == "__main__":
    asyncio.run(main())
