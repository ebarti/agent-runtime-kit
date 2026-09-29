"""Validate a typed result without credentials or a provider call."""

import asyncio
from dataclasses import dataclass

from agent_runtime_kit import AgentKit
from agent_runtime_kit.testing import FakeSDKHarness, FakeSDKRuntime, FakeSDKScenario


@dataclass
class Summary:
    name: str
    languages: list[str]


async def main() -> None:
    # The SDK harness returns this fixture; it does not infer facts from files.
    scenario = FakeSDKScenario(
        output='{"name":"example","languages":["Python"]}',
        structured_output={"name": "example", "languages": ["Python"]},
    )
    runtime = FakeSDKRuntime(FakeSDKHarness(scenario))
    async with AgentKit() as kit:
        result = await kit.run(runtime, goal="Demonstrate typed output", output_type=Summary)
        if not result.is_success or not result.parsed_output_available:
            raise RuntimeError(result.error or result.finish_reason)
        summary = result.parsed
        if summary is None:
            raise RuntimeError("expected a non-null Summary")
        print(f"{summary.name}: {', '.join(summary.languages)}")


if __name__ == "__main__":
    asyncio.run(main())
