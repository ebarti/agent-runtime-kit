"""Run one task through explicitly selected providers (may incur charges)."""

import argparse
import asyncio

from agent_runtime_kit import AgentKit
from examples.provider_task import PROVIDERS, run_provider


async def main(providers: list[str], attempt_indeterminate: bool) -> int:
    async with AgentKit() as kit:
        outcomes = []
        for provider in providers:
            outcomes.append(
                await run_provider(kit, provider, attempt_indeterminate=attempt_indeterminate)
            )
    return 0 if all(outcomes) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("providers", nargs="+", choices=PROVIDERS)
    parser.add_argument(
        "--attempt-indeterminate",
        action="store_true",
        help="run even when setup readiness could not be determined",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.providers, arguments.attempt_indeterminate)))
