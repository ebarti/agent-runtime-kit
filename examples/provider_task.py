"""Run one explicitly selected provider after package and setup checks."""

import argparse
import asyncio
from pathlib import Path

from agent_runtime_kit import AgentKit, ReadinessStatus

PROVIDERS = ("claude", "codex", "antigravity")
GOAL = "Summarize the purpose of this repository in one paragraph."


async def run_provider(
    kit: AgentKit, provider: str, *, attempt_indeterminate: bool = False
) -> bool:
    availability = kit.availability_for(provider)
    if not availability.available:
        print(f"{provider}: unavailable - {availability.message}")
        return False

    readiness = await kit.readiness_for(provider)
    if readiness.status is ReadinessStatus.NOT_READY:
        print(f"{provider}: not ready - {readiness.message}")
        return False
    if readiness.status is ReadinessStatus.INDETERMINATE and not attempt_indeterminate:
        print(f"{provider}: readiness indeterminate - {readiness.message}")
        print("Pass --attempt-indeterminate to choose to attempt this provider.")
        return False

    try:
        result = await kit.run(
            provider,
            goal=GOAL,
            working_directory=Path.cwd(),
            permissions="strict",
            filesystem="read-only",
        )
    except Exception as exc:
        print(f"{provider}: {type(exc).__name__}: {exc}")
        return False
    if not result.is_success:
        print(f"{provider}: {result.finish_reason} - {result.error or result.output}")
        return False
    print(f"{provider}: {result.output}")
    return True


async def main(provider: str, attempt_indeterminate: bool) -> int:
    async with AgentKit() as kit:
        succeeded = await run_provider(
            kit, provider, attempt_indeterminate=attempt_indeterminate
        )
        return 0 if succeeded else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=PROVIDERS)
    parser.add_argument(
        "--attempt-indeterminate",
        action="store_true",
        help="run even when setup readiness could not be determined",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(arguments.provider, arguments.attempt_indeterminate)))
