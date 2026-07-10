"""Provider adapter diagnostics."""

from __future__ import annotations

import asyncio

from agent_runtime_kit._types import RuntimeAvailability, RuntimeReadiness
from agent_runtime_kit.adapters.antigravity import AntigravityAgentRuntime
from agent_runtime_kit.adapters.claude import ClaudeAgentRuntime
from agent_runtime_kit.adapters.codex import CodexAgentRuntime
from agent_runtime_kit.readiness import DEFAULT_READINESS_TIMEOUT, check_readiness


def collect_provider_diagnostics() -> tuple[RuntimeAvailability, ...]:
    """Return synchronous, package-only diagnostics for provider adapters."""

    return (
        ClaudeAgentRuntime().availability(),
        CodexAgentRuntime().availability(),
        AntigravityAgentRuntime().availability(),
    )


async def collect_provider_readiness(
    *,
    timeout: float = DEFAULT_READINESS_TIMEOUT,
) -> tuple[RuntimeReadiness, ...]:
    """Probe provider credentials/setup concurrently with a bound per runtime."""

    runtimes = (
        ClaudeAgentRuntime(),
        CodexAgentRuntime(),
        AntigravityAgentRuntime(),
    )
    return tuple(
        await asyncio.gather(
            *(check_readiness(runtime, timeout=timeout) for runtime in runtimes)
        )
    )


__all__ = ["collect_provider_diagnostics", "collect_provider_readiness"]
