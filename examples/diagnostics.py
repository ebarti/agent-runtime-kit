"""Inspect package availability and, optionally, bounded setup readiness."""

import argparse
import asyncio

from agent_runtime_kit import AgentKit


async def main(provider: str, probe_readiness: bool) -> None:
    async with AgentKit() as kit:
        availability = kit.availability_for(provider)
        print(f"{provider} package: {'available' if availability.available else 'unavailable'}")
        if not availability.available:
            print(availability.message)
        if probe_readiness:
            readiness = await kit.readiness_for(provider)
            print(f"{provider} readiness: {readiness.status.value}")
            print(readiness.message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("fake", "claude", "codex", "antigravity"))
    parser.add_argument(
        "--probe-readiness",
        action="store_true",
        help="probe provider setup without running an agent task",
    )
    arguments = parser.parse_args()
    asyncio.run(main(arguments.provider, arguments.probe_readiness))
