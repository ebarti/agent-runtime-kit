"""Provider adapter diagnostics."""

from __future__ import annotations

from agent_runtime_kit._types import RuntimeAvailability
from agent_runtime_kit.adapters.antigravity import AntigravityAgentRuntime
from agent_runtime_kit.adapters.claude import ClaudeAgentRuntime
from agent_runtime_kit.adapters.codex import CodexAgentRuntime


def collect_provider_diagnostics() -> tuple[RuntimeAvailability, ...]:
    """Return availability diagnostics for installed provider adapters."""

    return (
        ClaudeAgentRuntime().availability(),
        CodexAgentRuntime().availability(),
        AntigravityAgentRuntime().availability(),
    )
