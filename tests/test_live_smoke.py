from __future__ import annotations

import os

import pytest

from agent_runtime_kit import AgentRuntime, AgentTask, ReadinessStatus, check_readiness
from agent_runtime_kit.adapters import (
    AntigravityAgentRuntime,
    ClaudeAgentRuntime,
    CodexAgentRuntime,
)


def _enabled(provider: str) -> bool:
    if os.environ.get("AGENT_RUNTIME_KIT_LIVE") != "1":
        return False
    selected = os.environ.get("AGENT_RUNTIME_KIT_LIVE_PROVIDER", "").lower()
    return selected in {provider, "all"}


async def _run_smoke(runtime: AgentRuntime) -> None:
    readiness = await check_readiness(runtime)
    if readiness.status is ReadinessStatus.NOT_READY:
        pytest.skip(readiness.message)
    result = await runtime.run(AgentTask(goal="Reply with exactly: agent-runtime-kit smoke"))
    assert result.output


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_claude_smoke() -> None:
    if not _enabled("claude"):
        pytest.skip("Set AGENT_RUNTIME_KIT_LIVE=1 and AGENT_RUNTIME_KIT_LIVE_PROVIDER=claude")
    await _run_smoke(ClaudeAgentRuntime())


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_codex_smoke() -> None:
    if not _enabled("codex"):
        pytest.skip("Set AGENT_RUNTIME_KIT_LIVE=1 and AGENT_RUNTIME_KIT_LIVE_PROVIDER=codex")
    await _run_smoke(CodexAgentRuntime())


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_antigravity_smoke() -> None:
    if not _enabled("antigravity"):
        pytest.skip(
            "Set AGENT_RUNTIME_KIT_LIVE=1 and AGENT_RUNTIME_KIT_LIVE_PROVIDER=antigravity"
        )
    await _run_smoke(AntigravityAgentRuntime())
